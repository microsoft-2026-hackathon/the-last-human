'use strict';

const ACTIVE_STATES = ['pending', 'paid', 'shipped'];

class OrderRepository {
  constructor(db) {
    this.db = db;
  }

  async findById(id) {
    const rows = await this.db.query('SELECT * FROM orders WHERE id = ?', [id]);
    return rows[0] ?? null;
  }

  async listActive(customerId, limit = 50) {
    const placeholders = ACTIVE_STATES.map(() => '?').join(', ');
    return this.db.query(
      `SELECT * FROM orders WHERE customer_id = ? AND status IN (${placeholders}) ORDER BY created_at DESC LIMIT ?`,
      [customerId, ...ACTIVE_STATES, limit],
    );
  }

  async markShipped(id, shippedAt) {
    return this.db.query('UPDATE orders SET status = ?, shipped_at = ? WHERE id = ?', [
      'shipped',
      shippedAt,
      id,
    ]);
  }

  async softDelete(id, deletedAt) {
    return this.db.query('UPDATE orders SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL', [
      deletedAt,
      id,
    ]);
  }
}

module.exports = { OrderRepository, ACTIVE_STATES };
