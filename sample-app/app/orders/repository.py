"""주문 저장소."""

from __future__ import annotations

from typing import Any

from ..db.client import Db

ACTIVE_STATES = ("pending", "paid", "shipped")


class OrderRepository:
    def __init__(self, db: Db) -> None:
        self.db = db

    async def find_by_id(self, order_id: int) -> dict[str, Any] | None:
        rows = await self.db.query("SELECT * FROM orders WHERE id = ?", (order_id,))
        return rows[0] if rows else None

    async def list_active(self, customer_id: int, limit: int = 50) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in ACTIVE_STATES)
        return await self.db.query(
            "SELECT * FROM orders WHERE customer_id = ? "
            f"AND status IN ({placeholders}) ORDER BY created_at DESC LIMIT ?",
            (customer_id, *ACTIVE_STATES, limit),
        )

    async def mark_shipped(self, order_id: int, shipped_at: float) -> None:
        await self.db.query(
            "UPDATE orders SET status = ?, shipped_at = ? WHERE id = ?",
            ("shipped", shipped_at, order_id),
        )

    async def soft_delete(self, order_id: int, deleted_at: float) -> None:
        await self.db.query(
            "UPDATE orders SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
            (deleted_at, order_id),
        )
