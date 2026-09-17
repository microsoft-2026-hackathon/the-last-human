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


def test_갱신에_성공하면_즉시_새_토큰을_반환한다(monkeypatch):
    refresh_calls = 0
    refreshed = TokenSet("new-a", "new-r", expires_at=2**40)

    async def refresh(_transport, _token):
        nonlocal refresh_calls
        refresh_calls += 1
        return refreshed

    monkeypatch.setattr(token_module, "refresh", refresh)

    assert asyncio.run(ensure_fresh(None, BASE)) is refreshed
    assert refresh_calls == 1


def test_일시적_실패는_세_번까지_갱신하고_지수_백오프한다(monkeypatch):
    refresh_calls = 0
    waits: list[float] = []

    async def refresh(_transport, _token):
        nonlocal refresh_calls
        refresh_calls += 1
        raise HttpError(503)

    async def sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(token_module, "refresh", refresh)
    monkeypatch.setattr(token_module.asyncio, "sleep", sleep)

    assert asyncio.run(ensure_fresh(None, BASE)) is BASE
    assert refresh_calls == MAX_REFRESH_ATTEMPTS == 3
    assert waits == [REFRESH_RETRY_BACKOFF_SEC, REFRESH_RETRY_BACKOFF_SEC * 2]


def test_두_번째_갱신에서_성공하면_새_토큰을_반환한다(monkeypatch):
    refresh_calls = 0
    refreshed = TokenSet("new-a", "new-r", expires_at=2**40)

    async def refresh(_transport, _token):
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls == 1:
            raise HttpError(503)
        return refreshed

    async def sleep(delay):
        assert delay == REFRESH_RETRY_BACKOFF_SEC

    monkeypatch.setattr(token_module, "refresh", refresh)
    monkeypatch.setattr(token_module.asyncio, "sleep", sleep)

    assert asyncio.run(ensure_fresh(None, BASE)) is refreshed
    assert refresh_calls == 2


def test_영구_실패는_즉시_다시_던진다(monkeypatch):
    refresh_calls = 0

    async def refresh(_transport, _token):
        nonlocal refresh_calls
        refresh_calls += 1
        raise HttpError(401)

    monkeypatch.setattr(token_module, "refresh", refresh)

    with pytest.raises(HttpError) as raised:
        asyncio.run(ensure_fresh(None, BASE))

    assert raised.value.status == 401
    assert refresh_calls == 1


def test_실제_HTTP_재시도와_갱신_재시도는_곱한_예산을_지킨다(monkeypatch):
    transport_calls = 0

    async def transport(method: str, _url: str, body: str) -> tuple[int, str]:
        nonlocal transport_calls
        transport_calls += 1
        assert method == "POST"
        assert json.loads(body)["refresh_token"] == BASE.refresh_token
        return 503, ""

    async def sleep(_delay):
        return None

    monkeypatch.setattr(http_client, "RETRY_BACKOFF_SEC", 0.0)
    monkeypatch.setattr(token_module.asyncio, "sleep", sleep)

    assert asyncio.run(ensure_fresh(transport, BASE)) is BASE
    assert transport_calls == MAX_REFRESH_ATTEMPTS * MAX_ATTEMPTS
