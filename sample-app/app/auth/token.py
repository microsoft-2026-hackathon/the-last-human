"""Access token refresh."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass

from ..http_client import Transport, post_json

#: Margin applied when deciding expiry. Absorbs clock skew and round-trip time.
CLOCK_SKEW_SEC = 60.0

TOKEN_ENDPOINT = os.environ.get("ORDERLY_TOKEN_ENDPOINT", "https://auth.internal/oauth/token")


class TokenResponseError(ValueError):
    """A successful token response contains malformed or unusable token data."""


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

    Invalid successful responses raise TokenResponseError. Other failures are
    raised as-is; only the transport layer retries transient HTTP failures.
    """
    try:
        body = await post_json(
            transport,
            TOKEN_ENDPOINT,
            {"grant_type": "refresh_token", "refresh_token": token.refresh_token},
        )
    except json.JSONDecodeError:
        raise TokenResponseError("Token response must be valid JSON.") from None

    if not isinstance(body, dict):
        raise TokenResponseError("Token response must be a JSON object.")

    access_token = body.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise TokenResponseError("Token response access_token must be a nonblank string.")

    refresh_token = body.get("refresh_token", token.refresh_token)
    if "refresh_token" in body and (not isinstance(refresh_token, str) or not refresh_token.strip()):
        raise TokenResponseError("Token response refresh_token must be a nonblank string.")

    expires_in = body.get("expires_in")
    if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or expires_in <= 0:
        raise TokenResponseError("Token response expires_in must be a positive finite number.")
    now = time.time()
    try:
        expires_at = now + expires_in
    except OverflowError:
        raise TokenResponseError("Token response expiry is out of range.") from None
    if not math.isfinite(expires_at):
        raise TokenResponseError("Token response expiry must be finite.")

    return TokenSet(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


async def ensure_fresh(transport: Transport, token: TokenSet) -> TokenSet:
    """Refresh a near-expiry token, otherwise return it unchanged."""
    if not is_expired(token):
        return token
    return await refresh(transport, token)
