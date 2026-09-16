import asyncio
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db.client import Db
from app.orders.repository import OrderRepository
from app.orders.service import OrderService


@pytest.fixture(name="db")
def sqlite_db() -> Iterator[Db]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        migration = Path(__file__).resolve().parents[1] / "migrations" / "001_init.sql"
        connection.executescript(migration.read_text(encoding="utf-8"))

        async def execute(sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

        yield Db(execute)
    finally:
        connection.close()


def insert_order(
    db: Db,
    order_id: int,
    status: str = "paid",
    *,
    customer_id: int = 10,
    created_at: float = 1.0,
    deleted_at: float | None = None,
) -> None:
    asyncio.run(
        db.query(
            "INSERT INTO orders (id, customer_id, status, total_cents, created_at, deleted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, customer_id, status, 1000, created_at, deleted_at),
        )
    )


def test_소프트_삭제된_주문은_조회되지_않는다(db: Db) -> None:
    insert_order(db, 1, deleted_at=123)
    svc = OrderService(db)
    assert asyncio.run(svc.get(1)) is None


def test_결제되지_않은_주문은_배송할_수_없다(db: Db) -> None:
    insert_order(db, 1, "pending")
    svc = OrderService(db)
    with pytest.raises(ValueError, match="cannot ship order in status pending"):
        asyncio.run(svc.ship(1))


def test_활성_주문은_고객과_상태를_확인하고_최신순으로_조회한다(db: Db) -> None:
    insert_order(db, 1, "pending", created_at=10)
    insert_order(db, 2, "paid", created_at=30)
    insert_order(db, 3, "shipped", created_at=20)
    insert_order(db, 4, "pending", customer_id=20, created_at=40)
    insert_order(db, 5, "paid", customer_id=20, created_at=50)
    insert_order(db, 6, "shipped", customer_id=20, created_at=60)
    insert_order(db, 7, "cancelled", created_at=70)
    insert_order(db, 8, "delivered", created_at=80)
    repo = OrderRepository(db)

    rows = asyncio.run(repo.list_active(10))

    assert [row["id"] for row in rows] == [2, 3, 1]
    assert asyncio.run(repo.list_active(30)) == []


@pytest.mark.parametrize("limit", [0, 1, 2, 3])
def test_삭제된_주문을_제외한_후_조회_개수를_제한한다(db: Db, limit: int) -> None:
    insert_order(db, 1, created_at=10)
    insert_order(db, 2, created_at=40)
    insert_order(db, 3, created_at=20)
    insert_order(db, 4, created_at=50, deleted_at=123)
    insert_order(db, 5, created_at=30, deleted_at=123)
    insert_order(db, 6, created_at=60, deleted_at=123)
    repo = OrderRepository(db)

    rows = asyncio.run(repo.list_active(10, limit=limit))

    assert [row["id"] for row in rows] == [2, 3, 1][:limit]


def test_기본_조회_개수는_미삭제_주문_50개다(db: Db) -> None:
    for order_id in range(1, 52):
        insert_order(db, order_id, created_at=float(order_id))
    insert_order(db, 52, created_at=52, deleted_at=123)
    repo = OrderRepository(db)

    rows = asyncio.run(repo.list_active(10))

    assert [row["id"] for row in rows] == list(range(51, 1, -1))


def test_정상_주문은_단건_조회되고_없는_주문은_조회되지_않는다(db: Db) -> None:
    insert_order(db, 1)
    svc = OrderService(db)

    assert asyncio.run(svc.get(1)) == {
        "id": 1,
        "customer_id": 10,
        "status": "paid",
        "total_cents": 1000,
        "created_at": 1.0,
        "shipped_at": None,
        "deleted_at": None,
    }
    assert asyncio.run(svc.get(999)) is None


@pytest.mark.parametrize("status", ["pending", "paid", "shipped"])
def test_소프트_삭제하면_활성_목록과_단건_조회에서_제외된다(db: Db, status: str) -> None:
    insert_order(db, 1, status, created_at=20)
    insert_order(db, 2, status, created_at=10)
    repo = OrderRepository(db)
    svc = OrderService(db)
    assert [row["id"] for row in asyncio.run(repo.list_active(10))] == [1, 2]
    assert asyncio.run(svc.get(1)) is not None

    asyncio.run(repo.soft_delete(1, deleted_at=123))

    assert [row["id"] for row in asyncio.run(repo.list_active(10))] == [2]
    assert asyncio.run(svc.get(1)) is None
    assert asyncio.run(svc.get(2)) is not None
    deleted_order = asyncio.run(repo.find_by_id(1))
    assert deleted_order is not None
    assert deleted_order["deleted_at"] == 123
