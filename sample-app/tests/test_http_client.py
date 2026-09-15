import asyncio
import json

import pytest

from app import http_client
from app.http_client import MAX_ATTEMPTS, HttpError, post_json

URL = "https://auth.internal/oauth/token"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(http_client, "RETRY_BACKOFF_SEC", 0.0)


def _transport(statuses: list[int], calls: list[str]):
    async def transport(method: str, url: str, body: str) -> tuple[int, str]:
        assert method == "POST"
        assert url == URL
        calls.append(body)
        status = statuses.pop(0)
        return status, json.dumps({"ok": True}) if status == 200 else ""

    return transport


def test_성공하면_한_번만_보낸다():
    calls: list[str] = []
    result = asyncio.run(post_json(_transport([200], calls), URL, {"a": 1}))
    assert result == {"ok": True}
    assert calls == [json.dumps({"a": 1})]


def test_일시적_실패는_다시_보내고_성공하면_돌려준다():
    calls: list[str] = []
    result = asyncio.run(post_json(_transport([503, 200], calls), URL, {"a": 1}))
    assert result == {"ok": True}
    assert len(calls) == 2


def test_최대_시도_횟수까지_실패하면_마지막_오류를_던진다():
    calls: list[str] = []
    with pytest.raises(HttpError) as raised:
        asyncio.run(post_json(_transport([503, 502, 429], calls), URL, {"a": 1}))
    assert raised.value.status == 429
    assert len(calls) == MAX_ATTEMPTS == 3


def test_영구_실패는_즉시_던진다():
    calls: list[str] = []
    with pytest.raises(HttpError) as raised:
        asyncio.run(post_json(_transport([401, 200], calls), URL, {"a": 1}))
    assert raised.value.status == 401
    assert raised.value.transient is False
    assert len(calls) == 1
