'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { OrderService } = require('../src/orders/service');

function fakeDb(rows) {
  return {
    async query() {
      return rows;
    },
  };
}

test('소프트 삭제된 주문은 조회되지 않는다', async () => {
  const svc = new OrderService(fakeDb([{ id: 1, status: 'paid', deleted_at: 123 }]));
  assert.equal(await svc.get(1), null);
});

test('결제되지 않은 주문은 배송할 수 없다', async () => {
  const svc = new OrderService(fakeDb([{ id: 1, status: 'pending', deleted_at: null }]));
  await assert.rejects(() => svc.ship(1), /cannot ship order in status pending/);
});
