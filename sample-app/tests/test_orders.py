import asyncio

import pytest

from app.db.client import Db
from app.orders.service import OrderService


def fake_db(rows):
    async def execute(sql, params=()):
        return rows

    return Db(execute)


def test_소프트_삭제된_주문은_조회되지_않는다():
    svc = OrderService(fake_db([{"id": 1, "status": "paid", "deleted_at": 123}]))
    assert asyncio.run(svc.get(1)) is None


def test_결제되지_않은_주문은_배송할_수_없다():
    svc = OrderService(fake_db([{"id": 1, "status": "pending", "deleted_at": None}]))
    with pytest.raises(ValueError, match="cannot ship order in status pending"):
        asyncio.run(svc.ship(1))
