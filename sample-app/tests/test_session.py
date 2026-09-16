import asyncio
import json

import pytest

from app.auth.session import SessionStore
from app.auth.token import TOKEN_ENDPOINT, TokenSet
from app.http_client import HttpError

EXPIRED = TokenSet("old-access", "old-refresh", expires_at=0.0)
UNEXPIRED = TokenSet("current-access", "current-refresh", expires_at=2**40)


class ControlledTransport:
    def __init__(self, error: Exception | None = None) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[str] = []
        self.error = error

    async def __call__(self, method: str, url: str, body: str) -> tuple[int, str]:
        assert method == "POST"
        assert url == TOKEN_ENDPOINT
        payload = json.loads(body)
        assert payload["grant_type"] == "refresh_token"
        refresh_token = payload["refresh_token"]
        self.calls.append(refresh_token)
        if refresh_token == EXPIRED.refresh_token:
            self.started.set()
            await self.release.wait()
            if self.error is not None:
                raise self.error
        return 200, json.dumps({
            "access_token": f"fresh-{refresh_token}",
            "refresh_token": f"rotated-{refresh_token}",
            "expires_in": 3600,
        })


@pytest.mark.parametrize("change", ["drop", "replace", "reput", "drop-replace", "drop-reput"])
def test_pending_refresh_is_invalidated_by_registration_change(change: str) -> None:
    async def scenario() -> None:
        transport = ControlledTransport()
        store = SessionStore(transport)
        store.put("session", EXPIRED)
        pending = asyncio.create_task(store.current_token("session"))
        await asyncio.wait_for(transport.started.wait(), timeout=5)

        if change.startswith("drop"):
            store.drop("session")
        if change.endswith("replace"):
            store.put("session", UNEXPIRED)
        elif change.endswith("reput"):
            store.put("session", EXPIRED)

        transport.release.set()
        with pytest.raises(KeyError) as raised:
            await pending
        assert raised.value.args == ("unknown session: session",)
        assert transport.calls == [EXPIRED.refresh_token]

        if change == "drop":
            with pytest.raises(KeyError, match="unknown session: session"):
                await store.current_token("session")
        elif change.endswith("replace"):
            assert await store.current_token("session") is UNEXPIRED
        else:
            fresh = await store.current_token("session")
            assert fresh.access_token == "fresh-old-refresh"
            assert transport.calls == [EXPIRED.refresh_token, EXPIRED.refresh_token]
            assert await store.current_token("session") is fresh

    asyncio.run(scenario())


def test_unchanged_registration_refreshes_and_stores_fresh_token() -> None:
    async def scenario() -> None:
        transport = ControlledTransport()
        store = SessionStore(transport)
        store.put("session", EXPIRED)
        pending = asyncio.create_task(store.current_token("session"))
        await asyncio.wait_for(transport.started.wait(), timeout=5)
        transport.release.set()

        fresh = await pending
        assert isinstance(fresh, TokenSet)
        assert fresh.access_token == "fresh-old-refresh"
        assert fresh.refresh_token == "rotated-old-refresh"
        assert fresh.expires_at > EXPIRED.expires_at
        assert await store.current_token("session") is fresh
        assert transport.calls == [EXPIRED.refresh_token]

    asyncio.run(scenario())


def test_unexpired_token_is_reused_without_transport() -> None:
    async def scenario() -> None:
        transport = ControlledTransport()
        store = SessionStore(transport)
        store.put("session", UNEXPIRED)

        assert await store.current_token("session") is UNEXPIRED
        assert transport.calls == []

    asyncio.run(scenario())


def test_missing_session_raises_key_error_without_transport() -> None:
    async def scenario() -> None:
        transport = ControlledTransport()
        store = SessionStore(transport)
        store.drop("missing")

        with pytest.raises(KeyError) as raised:
            await store.current_token("missing")
        assert raised.value.args == ("unknown session: missing",)
        assert transport.calls == []

    asyncio.run(scenario())


def test_other_sessions_can_refresh_while_one_refresh_is_pending() -> None:
    async def scenario() -> None:
        transport = ControlledTransport()
        store = SessionStore(transport)
        store.put("session", EXPIRED)
        store.put("other", TokenSet("other-access", "other-refresh", expires_at=0.0))
        pending = asyncio.create_task(store.current_token("session"))
        await asyncio.wait_for(transport.started.wait(), timeout=5)

        try:
            other = await asyncio.wait_for(store.current_token("other"), timeout=5)
            assert other.access_token == "fresh-other-refresh"
            assert not pending.done()
            assert await store.current_token("other") is other
        finally:
            transport.release.set()
            await pending

        assert transport.calls == [EXPIRED.refresh_token, "other-refresh"]

    asyncio.run(scenario())


@pytest.mark.parametrize("replace", [False, True])
@pytest.mark.parametrize("error", [HttpError(401, "unauthorized"), OSError("transport failed")])
def test_transport_error_propagates_without_changing_registration(error: Exception, replace: bool) -> None:
    async def scenario() -> None:
        transport = ControlledTransport(error)
        store = SessionStore(transport)
        store.put("session", EXPIRED)
        pending = asyncio.create_task(store.current_token("session"))
        await asyncio.wait_for(transport.started.wait(), timeout=5)
        if replace:
            store.put("session", UNEXPIRED)
        transport.release.set()

        with pytest.raises(type(error)) as raised:
            await pending
        assert raised.value is error
        assert transport.calls == [EXPIRED.refresh_token]

        if replace:
            assert await store.current_token("session") is UNEXPIRED
        else:
            transport.error = None
            fresh = await store.current_token("session")
            assert fresh.access_token == "fresh-old-refresh"
            assert transport.calls == [EXPIRED.refresh_token, EXPIRED.refresh_token]

    asyncio.run(scenario())
