import asyncio
import json

import pytest

from app.auth import token as token_module
from app.auth.token import (
    CLOCK_SKEW_SEC,
    MAX_REFRESH_ATTEMPTS,
    REFRESH_BACKOFF_SEC,
    TokenSet,
    ensure_fresh,
    is_expired,
)
from app.http_client import MAX_ATTEMPTS, RETRY_BACKOFF_SEC, HttpError

BASE = TokenSet(access_token="a", refresh_token="r", expires_at=1_000_000.0)
EXPIRED = TokenSet(access_token="old", refresh_token="refresh", expires_at=0.0)


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


def test_첫_갱신이_성공하면_즉시_새_토큰을_반환한다(monkeypatch):
    refreshed = TokenSet("new", "new-refresh", expires_at=2**40)
    calls = 0

    async def fake_refresh(transport, token):
        nonlocal calls
        calls += 1
        return refreshed

    monkeypatch.setattr(token_module, "refresh", fake_refresh)

    assert asyncio.run(ensure_fresh(None, EXPIRED)) is refreshed
    assert calls == 1


def test_일시적_실패_후_다음_갱신이_성공한다(monkeypatch):
    refreshed = TokenSet("new", "new-refresh", expires_at=2**40)
    calls = 0
    waits = []

    async def fake_refresh(transport, token):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HttpError(503)
        return refreshed

    async def fake_sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(token_module, "refresh", fake_refresh)
    monkeypatch.setattr(token_module.asyncio, "sleep", fake_sleep)

    assert asyncio.run(ensure_fresh(None, EXPIRED)) is refreshed
    assert calls == 2
    assert waits == [REFRESH_BACKOFF_SEC]


def test_영구적_실패는_즉시_다시_발생시킨다(monkeypatch):
    calls = 0

    async def fake_refresh(transport, token):
        nonlocal calls
        calls += 1
        raise HttpError(401)

    monkeypatch.setattr(token_module, "refresh", fake_refresh)

    with pytest.raises(HttpError, match="HTTP 401"):
        asyncio.run(ensure_fresh(None, EXPIRED))
    assert calls == 1


def test_일시적_실패는_정확한_전체_예산_후_원래_토큰을_반환한다(monkeypatch):
    transport_calls = 0
    waits = []

    async def transport(method, url, body):
        nonlocal transport_calls
        transport_calls += 1
        assert method == "POST"
        assert json.loads(body)["refresh_token"] == EXPIRED.refresh_token
        return 503, "temporarily unavailable"

    async def fake_sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(token_module.asyncio, "sleep", fake_sleep)

    result = asyncio.run(ensure_fresh(transport, EXPIRED))

    assert result is EXPIRED
    assert transport_calls == MAX_REFRESH_ATTEMPTS * MAX_ATTEMPTS
    assert waits == [
        RETRY_BACKOFF_SEC,
        RETRY_BACKOFF_SEC * 2,
        REFRESH_BACKOFF_SEC,
        RETRY_BACKOFF_SEC,
        RETRY_BACKOFF_SEC * 2,
        REFRESH_BACKOFF_SEC * 2,
        RETRY_BACKOFF_SEC,
        RETRY_BACKOFF_SEC * 2,
    ]
