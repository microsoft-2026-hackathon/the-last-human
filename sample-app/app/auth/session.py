"""Per-session token storage."""

from __future__ import annotations

from dataclasses import dataclass

from ..http_client import Transport
from .token import TokenSet, ensure_fresh


@dataclass(eq=False)
class _Session:
    token: TokenSet


class SessionStore:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._sessions: dict[str, _Session] = {}

    def put(self, session_id: str, token: TokenSet) -> None:
        self._sessions[session_id] = _Session(token)

    def drop(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def current_token(self, session_id: str) -> TokenSet:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"unknown session: {session_id}")
        fresh = await ensure_fresh(self._transport, session.token)
        if self._sessions.get(session_id) is not session:
            raise KeyError(f"unknown session: {session_id}")
        session.token = fresh
        return fresh
