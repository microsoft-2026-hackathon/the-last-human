"""주문 상태 전이."""

from __future__ import annotations

import time
from typing import Any

from ..db.client import Db
from .repository import OrderRepository


class OrderService:
    def __init__(self, db: Db) -> None:
        self.repo = OrderRepository(db)

    async def get(self, order_id: int) -> dict[str, Any] | None:
        order = await self.repo.find_by_id(order_id)
        if order is None or order.get("deleted_at"):
            return None
        return order

    async def ship(self, order_id: int, now: float | None = None) -> dict[str, Any] | None:
        order = await self.get(order_id)
        if order is None:
            raise LookupError(f"order not found: {order_id}")
        if order["status"] != "paid":
            raise ValueError(f"cannot ship order in status {order['status']}")
        await self.repo.mark_shipped(order_id, time.time() if now is None else now)
        return await self.get(order_id)
