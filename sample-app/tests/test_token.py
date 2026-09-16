import asyncio
import json
import sys
import traceback

import pytest

from app import http_client
from app.auth import token as token_module
from app.auth.session import SessionStore
from app.auth.token import (
    CLOCK_SKEW_SEC,
    TOKEN_ENDPOINT,
    TokenResponseError,
    TokenSet,
    ensure_fresh,
    is_expired,
    refresh,
)
from app.http_client import MAX_ATTEMPTS, HttpError, Transport

NOW = 1_000_000.0
BASE = TokenSet(access_token="private-old-access", refresh_token="private-old-refresh", expires_at=NOW)


@pytest.fixture(autouse=True)
def _controlled_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_module.time, "time", lambda: NOW)
    monkeypatch.setattr(http_client, "RETRY_BACKOFF_SEC", 0.0)


def _transport(responses: list[tuple[int, str]], calls: list[str]) -> Transport:
    async def transport(method: str, url: str, body: str) -> tuple[int, str]:
        assert method == "POST"
        assert url == TOKEN_ENDPOINT
        calls.append(body)
        return responses.pop(0)

    return transport


def _assert_invalid_response(response: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    store = SessionStore(_transport([(200, response)], calls))
    store.put("session", BASE)

    with pytest.raises(TokenResponseError, match="Token response") as raised:
        asyncio.run(store.current_token("session"))

    assert isinstance(raised.value, ValueError)
    assert raised.value.__cause__ is None
    error_text = "".join(traceback.format_exception(raised.value))
    for secret in ("private-new-access", "private-new-refresh", BASE.access_token, BASE.refresh_token):
        assert secret not in str(raised.value)
        assert secret not in error_text

    monkeypatch.setattr(token_module.time, "time", lambda: BASE.expires_at - CLOCK_SKEW_SEC - 1)
    assert asyncio.run(store.current_token("session")) is BASE
    assert len(calls) == 1


def test_여유_시간_밖이면_만료가_아니다() -> None:
    assert is_expired(BASE, BASE.expires_at - CLOCK_SKEW_SEC - 1) is False


def test_여유_시간_안으로_들어오면_만료로_본다() -> None:
    assert is_expired(BASE, BASE.expires_at - CLOCK_SKEW_SEC) is True


def test_이미_지났으면_만료다() -> None:
    assert is_expired(BASE, BASE.expires_at + 1) is True


def test_만료되지_않은_토큰은_갱신하지_않는다() -> None:
    async def transport(_method: str, _url: str, _body: str) -> tuple[int, str]:
        raise AssertionError("갱신이 일어나서는 안 된다")

    token = TokenSet("a", "r", expires_at=NOW + CLOCK_SKEW_SEC + 1)
    assert asyncio.run(ensure_fresh(transport, token)) is token
    store = SessionStore(transport)
    store.put("session", token)
    assert asyncio.run(store.current_token("session")) is token


@pytest.mark.parametrize("expires_in", [3600, 90.5, 1e308, 10**308])
@pytest.mark.parametrize("rotate_refresh_token", [False, True])
def test_정상_응답은_토큰과_만료_시각을_반환한다(
    expires_in: int | float, rotate_refresh_token: bool
) -> None:
    payload: dict[str, object] = {"access_token": "private-new-access", "expires_in": expires_in}
    if rotate_refresh_token:
        payload["refresh_token"] = "private-new-refresh"
    calls: list[str] = []

    result = asyncio.run(refresh(_transport([(200, json.dumps(payload))], calls), BASE))

    assert result == TokenSet(
        "private-new-access",
        "private-new-refresh" if rotate_refresh_token else BASE.refresh_token,
        NOW + expires_in,
    )
    assert isinstance(result.expires_at, float)
    assert len(calls) == 1
    assert json.loads(calls[0]) == {"grant_type": "refresh_token", "refresh_token": BASE.refresh_token}


def test_정상_토큰_문자열은_수정하지_않는다() -> None:
    payload = {"access_token": " access ", "refresh_token": " refresh ", "expires_in": 3600}
    calls: list[str] = []
    store = SessionStore(_transport([(200, json.dumps(payload))], calls))
    store.put("session", BASE)

    result = asyncio.run(store.current_token("session"))

    assert result == TokenSet(" access ", " refresh ", NOW + 3600)
    assert asyncio.run(store.current_token("session")) is result
    assert len(calls) == 1


@pytest.mark.parametrize("original_refresh_token", ["", " ", "original-refresh"])
def test_생략된_갱신_토큰은_기존_값을_그대로_유지한다(original_refresh_token: str) -> None:
    token = TokenSet("old-access", original_refresh_token, NOW)
    calls: list[str] = []
    response = json.dumps({"access_token": "private-new-access", "expires_in": 3600})

    result = asyncio.run(refresh(_transport([(200, response)], calls), token))

    assert result.refresh_token == original_refresh_token
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        pytest.param("", id="empty-body"),
        pytest.param(" \n\t", id="whitespace-body"),
        pytest.param('{"access_token": "private-new-access",', id="malformed-json"),
        pytest.param(
            '{"access_token": "private-new-access"} private-new-refresh',
            id="trailing-content",
        ),
        pytest.param("null", id="null"),
        pytest.param("[]", id="array"),
        pytest.param('["private-new-access"]', id="nonempty-array"),
        pytest.param('"private-new-access"', id="string"),
        pytest.param("42", id="number"),
        pytest.param("true", id="boolean"),
        pytest.param("{}", id="empty-object"),
    ],
)
def test_본문_오류는_명시적으로_전달하고_세션을_유지한다(
    response: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_invalid_response(response, monkeypatch)


@pytest.mark.parametrize("missing_field", ["access_token", "expires_in"])
def test_필수_필드가_없으면_세션을_유지한다(missing_field: str, monkeypatch: pytest.MonkeyPatch) -> None:
    payload: dict[str, object] = {"access_token": "private-new-access", "expires_in": 3600}
    del payload[missing_field]
    _assert_invalid_response(json.dumps(payload), monkeypatch)


@pytest.mark.parametrize("field", ["access_token", "refresh_token"])
@pytest.mark.parametrize("value", [None, "", " \n\t", True, 123, 1.5, [], {}])
def test_토큰_필드는_비어_있지_않은_문자열이어야_한다(
    field: str, value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload: dict[str, object] = {
        "access_token": "private-new-access",
        "refresh_token": "private-new-refresh",
        "expires_in": 3600,
    }
    payload[field] = value
    _assert_invalid_response(json.dumps(payload), monkeypatch)


@pytest.mark.parametrize(
    "expires_in",
    [
        pytest.param(None, id="null"),
        pytest.param(True, id="true"),
        pytest.param(False, id="false"),
        pytest.param("3600", id="numeric-string"),
        pytest.param("", id="empty-string"),
        pytest.param([], id="array"),
        pytest.param({}, id="object"),
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative-integer"),
        pytest.param(-0.5, id="negative-fraction"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(10**400, id="integer-overflow"),
    ],
)
def test_만료_시간은_양수인_유한한_숫자여야_한다(
    expires_in: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"access_token": "private-new-access", "expires_in": expires_in}
    _assert_invalid_response(json.dumps(payload), monkeypatch)


def test_JSON_지수_오버플로는_응답_오류다(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_invalid_response('{"access_token": "private-new-access", "expires_in": 1e400}', monkeypatch)


def test_만료_시각_합산_오버플로는_응답_오류다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(token_module.time, "time", lambda: sys.float_info.max)
    payload = {"access_token": "private-new-access", "expires_in": sys.float_info.max}
    _assert_invalid_response(json.dumps(payload), monkeypatch)


def test_일시적_HTTP_실패는_기존_계층에서만_재시도한다() -> None:
    calls: list[str] = []
    response = json.dumps({"access_token": "private-new-access", "expires_in": 3600})
    transport = _transport([(503, ""), (429, ""), (200, response)], calls)

    result = asyncio.run(refresh(transport, BASE))

    assert result == TokenSet("private-new-access", BASE.refresh_token, NOW + 3600)
    assert len(calls) == MAX_ATTEMPTS == 3


@pytest.mark.parametrize("statuses", [(401,), (503, 502, 429)])
def test_HTTP_실패는_기존_재시도와_오류를_유지한다(statuses: tuple[int, ...]) -> None:
    calls: list[str] = []
    transport = _transport([(status, "private-error-body") for status in statuses], calls)

    with pytest.raises(HttpError) as raised:
        asyncio.run(refresh(transport, BASE))

    assert raised.value.status == statuses[-1]
    assert raised.value.body == "private-error-body"
    assert len(calls) == len(statuses)


@pytest.mark.parametrize(
    "error",
    [OSError("offline"), ValueError("transport failure"), RuntimeError("transport failure"), HttpError(503)],
)
def test_전송_예외는_응답_오류로_변환하지_않는다(error: Exception) -> None:
    calls: list[str] = []

    async def transport(_method: str, _url: str, body: str) -> tuple[int, str]:
        calls.append(body)
        raise error

    with pytest.raises(type(error)) as raised:
        asyncio.run(refresh(transport, BASE))

    assert raised.value is error
    assert len(calls) == 1
