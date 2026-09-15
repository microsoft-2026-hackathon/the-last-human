"""세션별 토큰 보관."""

from __future__ import annotations

from ..http_client import Transport
from .token import TokenSet, ensure_fresh


class SessionStore:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._sessions: dict[str, TokenSet] = {}

    def put(self, session_id: str, token: TokenSet) -> None:
        self._sessions[session_id] = token

    def drop(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def current_token(self, session_id: str) -> TokenSet:
        token = self._sessions.get(session_id)
        if token is None:
            raise KeyError(f"unknown session: {session_id}")
        fresh = await ensure_fresh(self._transport, token)
        if fresh is not token:
            self._sessions[session_id] = fresh
        return fresh
