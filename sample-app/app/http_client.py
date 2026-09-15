"""아주 얇은 HTTP 계층.

전송을 주입받는다. 테스트가 네트워크를 타지 않게 하려는 것이고,
덕분에 샘플 앱에 외부 의존성이 없다.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

#: 일시적 실패로 간주하는 상태 코드.
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

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
    status, text = await transport("POST", url, json.dumps(payload))
    if not 200 <= status < 300:
        raise HttpError(status, text)
    return json.loads(text) if text else None
