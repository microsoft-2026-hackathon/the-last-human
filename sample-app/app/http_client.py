"""아주 얇은 HTTP 계층.

전송을 주입받는다. 테스트가 네트워크를 타지 않게 하려는 것이고,
덕분에 샘플 앱에 외부 의존성이 없다.

일시적 실패(429·5xx)는 **이 계층이 스스로 재시도한다.** 호출자가 그 위에
재시도를 또 얹으면 한 요청이 상대 서버에 도달하는 횟수가 곱으로 늘어난다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

#: 일시적 실패로 간주하는 상태 코드.
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

#: 한 번의 post_json 호출이 전송을 시도하는 최대 횟수. 첫 시도를 포함한다.
#: 일시적 실패에만 적용되고, 그 밖의 실패는 즉시 던진다.
MAX_ATTEMPTS = 3

#: 재시도 사이의 기준 간격(초). 시도 횟수에 비례해 늘어난다. 지터는 없다.
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
    """JSON을 보내고 JSON을 받는다.

    일시적 실패면 ``MAX_ATTEMPTS``까지 다시 보낸다. 마지막 시도까지 실패하면
    마지막 응답의 ``HttpError``를 던진다.
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
