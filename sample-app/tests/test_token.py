import asyncio
import json

import pytest

from app import http_client
from app.auth.token import (
    CLOCK_SKEW_SEC,
    TOKEN_ENDPOINT,
    TokenSet,
    ensure_fresh,
    is_expired,
)
from app.http_client import MAX_ATTEMPTS, RETRY_BACKOFF_SEC, HttpError

BASE = TokenSet(access_token="a", refresh_token="r", expires_at=1_000_000.0)


def _transport(statuses: list[int], calls: list[str]):
    async def transport(method: str, url: str, body: str) -> tuple[int, str]:
        assert method == "POST"
        assert url == TOKEN_ENDPOINT
        calls.append(body)
        status = statuses.pop(0)
        response = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }
        return status, json.dumps(response) if status == 200 else ""

    return transport


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


def test_일시적_실패는_백오프_후_갱신을_다시_시도한다(monkeypatch):
    calls: list[str] = []
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(http_client.asyncio, "sleep", sleep)

    refreshed = asyncio.run(ensure_fresh(_transport([503, 200], calls), BASE))

    assert refreshed.access_token == "new-access"
    assert len(calls) == 2
    assert delays == [RETRY_BACKOFF_SEC]


def test_일시적_실패가_계속되면_현재_세션을_유지한다(monkeypatch):
    calls: list[str] = []
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(http_client.asyncio, "sleep", sleep)

    result = asyncio.run(ensure_fresh(_transport([503] * MAX_ATTEMPTS, calls), BASE))

    assert result is BASE
    assert len(calls) == MAX_ATTEMPTS
    assert delays == [
        RETRY_BACKOFF_SEC * attempt for attempt in range(1, MAX_ATTEMPTS)
    ]


@pytest.mark.parametrize("status", [400, 401])
def test_영구_실패는_즉시_던진다(status: int):
    calls: list[str] = []

    with pytest.raises(HttpError) as raised:
        asyncio.run(ensure_fresh(_transport([status], calls), BASE))

    assert raised.value.status == status
    assert len(calls) == 1
