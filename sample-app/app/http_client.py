"""A very thin HTTP layer.

The transport is injected: tests never touch the network, and the sample app
has no external dependency because of it.

Transient failures (429 and 5xx) are **retried here, by this layer.** A caller
that stacks its own retry on top multiplies how many times a single request
reaches the upstream server.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

#: Status codes treated as transient failures.
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

#: How many times one post_json call may try the transport, first attempt included.
#: Applies to transient failures only; anything else is raised immediately.
MAX_ATTEMPTS = 3

#: Base delay between attempts, in seconds. Grows linearly with the attempt. No jitter.
RETRY_BACKOFF_SEC = 0.2

#: (method, url, body) -> (status, text)
Transport = Callable[[str, str, str], Awaitable[tuple[int, str]]]


class HttpError(Exception):
    def __init__(self, status: int, body: str = "") -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body

    @property
    def transient(self) -> bool:
        return self.status in TRANSIENT_STATUS


async def post_json(transport: Transport, url: str, payload: dict) -> dict | None:
    """Send JSON, receive JSON.

    On a transient failure the request is sent again, up to ``MAX_ATTEMPTS``.
    If the last attempt fails too, the ``HttpError`` of that last response is raised.
    """
    body = json.dumps(payload)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        status, text = await transport("POST", url, body)
        if 200 <= status < 300:
            return json.loads(text) if text else None
        error = HttpError(status, text)
        if not error.transient or attempt == MAX_ATTEMPTS:
            raise error
        await asyncio.sleep(RETRY_BACKOFF_SEC * attempt)
    raise AssertionError("unreachable")
