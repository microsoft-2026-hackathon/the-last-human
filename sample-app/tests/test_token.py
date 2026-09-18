import asyncio
import json

import pytest

from app.auth.token import CLOCK_SKEW_SEC, TokenSet, ensure_fresh, is_expired
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


def test_transient_idp_failure_is_retried_and_session_survives():
    calls: list[str] = []

    async def transport(method: str, url: str, body: str) -> tuple[int, str]:
        calls.append(body)
        if len(calls) == 1:
            return 503, "Service Unavailable"
        return 200, json.dumps({"access_token": "new", "expires_in": 3600})

    expired = TokenSet("a", "r", expires_at=1.0)
    result = asyncio.run(ensure_fresh(transport, expired))
    assert result.access_token == "new"
    assert len(calls) == 2


def test_permanent_idp_failure_is_not_retried():
    calls: list[str] = []

    async def transport(method: str, url: str, body: str) -> tuple[int, str]:
        calls.append(body)
        return 401, "invalid_grant"

    expired = TokenSet("a", "r", expires_at=1.0)
    with pytest.raises(HttpError):
        asyncio.run(ensure_fresh(transport, expired))
    assert len(calls) == 1
