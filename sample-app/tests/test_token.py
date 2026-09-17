import asyncio
import json

import pytest

from app import http_client
from app.auth import token as token_module
from app.auth.token import (
    CLOCK_SKEW_SEC,
    MAX_REFRESH_ATTEMPTS,
    REFRESH_RETRY_BACKOFF_SEC,
    TokenSet,
    ensure_fresh,
    is_expired,
)
from app.http_client import MAX_ATTEMPTS, HttpError

BASE = TokenSet(access_token="a", refresh_token="r", expires_at=1_000_000.0)


def test_여유_시간_밖이면_만료가_아니다():
    assert is_expired(BASE, BASE.expires_at - CLOCK_SKEW_SEC - 1) is False


def test_여유_시간_안으로_들어오면_만료로_본다():
    assert is_expired(BASE, BASE.expires_at - CLOCK_SKEW_SEC) is True


def test_이미_지났으면_만료다():
    assert is_expired(BASE, BASE.expires_at + 1) is True


def test_만료되지_않은_토큰은_갱신하지_않는다():
    async def transport(method, url, body):  # pragma: no cover - 호출되면 안 된다
        raise AssertionError("갱신이 일어나서는 안 된다")

    token = TokenSet("a", "r", expires_at=2**40)
    assert asyncio.run(ensure_fresh(transport, token)) is token


def test_갱신에_성공하면_즉시_새_토큰을_돌려준다():
    calls = 0

    async def transport(method: str, url: str, body: str) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        return 200, json.dumps(
            {"access_token": "new", "refresh_token": "new-r", "expires_in": 3600}
        )

    result = asyncio.run(ensure_fresh(transport, BASE))

    assert result.access_token == "new"
    assert result.refresh_token == "new-r"
    assert calls == 1


def test_외부_재시도는_0_5초와_1_0초를_기다린다(monkeypatch):
    refresh_calls = 0
    sleeps: list[float] = []

    async def failing_refresh(transport, token):
        nonlocal refresh_calls
        refresh_calls += 1
        raise HttpError(503)

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(token_module, "refresh", failing_refresh)
    monkeypatch.setattr(token_module.asyncio, "sleep", record_sleep)

    assert asyncio.run(ensure_fresh(None, BASE)) is BASE
    assert refresh_calls == MAX_REFRESH_ATTEMPTS
    assert sleeps == [REFRESH_RETRY_BACKOFF_SEC, REFRESH_RETRY_BACKOFF_SEC * 2]


def test_전체_HTTP_재시도_묶음_실패_후_다음_갱신이_성공한다(monkeypatch):
    calls = 0
    monkeypatch.setattr(http_client, "RETRY_BACKOFF_SEC", 0.0)
    monkeypatch.setattr(token_module, "REFRESH_RETRY_BACKOFF_SEC", 0.0)

    async def transport(method: str, url: str, body: str) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        if calls <= MAX_ATTEMPTS:
            return 503, ""
        return 200, json.dumps({"access_token": "new", "expires_in": 3600})

    result = asyncio.run(ensure_fresh(transport, BASE))

    assert result.access_token == "new"
    assert calls == MAX_ATTEMPTS + 1


def test_일시적_실패가_계속되면_원래_토큰을_돌려준다(monkeypatch):
    calls = 0
    monkeypatch.setattr(http_client, "RETRY_BACKOFF_SEC", 0.0)
    monkeypatch.setattr(token_module, "REFRESH_RETRY_BACKOFF_SEC", 0.0)

    async def transport(method: str, url: str, body: str) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        return 503, ""

    result = asyncio.run(ensure_fresh(transport, BASE))

    assert result is BASE
    assert calls == MAX_REFRESH_ATTEMPTS * MAX_ATTEMPTS


def test_영구_실패는_즉시_다시_던진다():
    calls = 0

    async def transport(method: str, url: str, body: str) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        return 401, ""

    with pytest.raises(HttpError) as raised:
        asyncio.run(ensure_fresh(transport, BASE))

    assert raised.value.status == 401
    assert calls == 1
