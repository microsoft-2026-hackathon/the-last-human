"""아주 얇은 쿼리 실행기.

파라미터 바인딩을 강제하고, 문자열로 조립한 SQL은 raw()로만 통과시킨다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

#: (sql, params) -> rows
Executor = Callable[[str, tuple], Awaitable[list[dict[str, Any]]]]


class Db:
    def __init__(self, execute: Executor) -> None:
        self._execute = execute

    async def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """바인딩된 쿼리. 일반 경로는 전부 이쪽을 쓴다."""
        return await self._execute(sql, params)

    async def raw(self, sql: str) -> list[dict[str, Any]]:
        """바인딩 없는 원시 쿼리. 리뷰가 필요한 경로다."""
        return await self._execute(sql, ())
