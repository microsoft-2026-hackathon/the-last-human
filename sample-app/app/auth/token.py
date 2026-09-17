"""Access token refresh."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from ..http_client import HttpError, Transport, post_json

#: Margin applied when deciding expiry. Absorbs clock skew and round-trip time.
CLOCK_SKEW_SEC = 60.0

TOKEN_ENDPOINT = os.environ.get("ORDERLY_TOKEN_ENDPOINT", "https://auth.internal/oauth/token")


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float


def is_expired(token: TokenSet, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return now >= token.expires_at - CLOCK_SKEW_SEC


async def refresh(transport: Transport, token: TokenSet) -> TokenSet:
    """Exchange the refresh token for a new token set.

    The HTTP layer retries transient failures with backoff. Failures that remain
    after that retry policy are raised as-is.
    """
    body = await post_json(
        transport,
        TOKEN_ENDPOINT,
        {"grant_type": "refresh_token", "refresh_token": token.refresh_token},
    )
    return TokenSet(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token", token.refresh_token),
        expires_at=time.time() + body["expires_in"],
    )


async def ensure_fresh(transport: Transport, token: TokenSet) -> TokenSet:
    """Refresh a near-expiry token, retaining it after transient failures."""
    if not is_expired(token):
        return token
    try:
        return await refresh(transport, token)
    except HttpError as error:
        if error.transient:
            return token
        raise
