'use strict';

const { OrderRepository } = require('./repository');

class OrderService {
  constructor(db) {
    this.repo = new OrderRepository(db);
  }

  async get(id) {
    const order = await this.repo.findById(id);
    if (!order || order.deleted_at) {
      return null;
    }
    return order;
  }

  async ship(id, clock = Date) {
    const order = await this.get(id);
    if (!order) {
      throw new Error(`order not found: ${id}`);
    }
    if (order.status !== 'paid') {
      throw new Error(`cannot ship order in status ${order.status}`);
    }
    await this.repo.markShipped(id, clock.now());
    return this.get(id);
  }
}

module.exports = { OrderService };
