import asyncio

import pytest

from app import http_client
from app.auth import token as token_module
from app.auth.token import (
    CLOCK_SKEW_SEC,
    MAX_REFRESH_ATTEMPTS,
    TokenSet,
    ensure_fresh,
    is_expired,
)
from app.http_client import HttpError

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


def test_갱신에_성공하면_즉시_반환한다(monkeypatch):
    calls = 0
    original = TokenSet("old", "refresh", expires_at=0)
    refreshed = TokenSet("new", "refresh", expires_at=2**40)

    async def successful_refresh(transport, token):
        nonlocal calls
        calls += 1
        return refreshed

    monkeypatch.setattr(token_module, "refresh", successful_refresh)

    assert asyncio.run(ensure_fresh(lambda *_: None, original)) is refreshed
    assert calls == 1


def test_일시적_실패는_세_번까지만_재시도하고_마지막에는_기존_토큰을_반환한다(
    monkeypatch,
):
    refresh_calls = 0
    waits = []
    original = TokenSet("old", "refresh", expires_at=0)

    async def failing_refresh(transport, token):
        nonlocal refresh_calls
        refresh_calls += 1
        raise HttpError(503)

    async def record_sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(token_module, "refresh", failing_refresh)
    monkeypatch.setattr(token_module.asyncio, "sleep", record_sleep)

    result = asyncio.run(ensure_fresh(lambda *_: None, original))

    assert result is original
    assert refresh_calls == MAX_REFRESH_ATTEMPTS == 3
    assert waits == [0.5, 1.0]


def test_영구적_실패는_즉시_전파한다(monkeypatch):
    refresh_calls = 0
    original = TokenSet("old", "refresh", expires_at=0)

    async def failing_refresh(transport, token):
        nonlocal refresh_calls
        refresh_calls += 1
        raise HttpError(401)

    monkeypatch.setattr(token_module, "refresh", failing_refresh)

    with pytest.raises(HttpError) as raised:
        asyncio.run(ensure_fresh(lambda *_: None, original))

    assert raised.value.status == 401
    assert refresh_calls == 1


def test_첫_HTTP_재시도_묶음이_실패해도_다음_갱신에서_성공한다(monkeypatch):
    calls = 0
    original = TokenSet("old", "refresh", expires_at=0)

    async def transport(method, url, body):
        nonlocal calls
        calls += 1
        if calls <= http_client.MAX_ATTEMPTS:
            return 503, "temporarily unavailable"
        return 200, '{"access_token": "new", "expires_in": 3600}'

    monkeypatch.setattr(token_module, "REFRESH_RETRY_BACKOFF_SEC", 0.0)
    monkeypatch.setattr(http_client, "RETRY_BACKOFF_SEC", 0.0)

    result = asyncio.run(ensure_fresh(transport, original))

    assert result.access_token == "new"
    assert calls == http_client.MAX_ATTEMPTS + 1


def test_실제_갱신_체인의_재시도_횟수는_두_계층_한도의_곱이다(monkeypatch):
    calls = []
    original = TokenSet("old", "refresh", expires_at=0)

    async def persistently_transient_transport(method, url, body):
        calls.append((method, url, body))
        return 503, "temporarily unavailable"

    monkeypatch.setattr(token_module, "REFRESH_RETRY_BACKOFF_SEC", 0.0)
    monkeypatch.setattr(http_client, "RETRY_BACKOFF_SEC", 0.0)

    result = asyncio.run(ensure_fresh(persistently_transient_transport, original))

    assert result is original
    assert len(calls) == MAX_REFRESH_ATTEMPTS * http_client.MAX_ATTEMPTS
