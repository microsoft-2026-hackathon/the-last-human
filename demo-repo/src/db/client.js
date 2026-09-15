'use strict';

/**
 * 아주 얇은 쿼리 실행기. 파라미터 바인딩을 강제하고,
 * 문자열로 조립한 SQL은 raw()로만 통과시킨다.
 */
class Db {
  constructor(pool) {
    this.pool = pool;
  }

  /** 바인딩된 쿼리. 일반 경로는 전부 이쪽을 쓴다. */
  async query(sql, params = []) {
    return this.pool.execute(sql, params);
  }

  /** 바인딩 없는 원시 쿼리. 리뷰가 필요한 경로다. */
  async raw(sql) {
    return this.pool.execute(sql, []);
  }

  async transaction(fn) {
    const conn = await this.pool.begin();
    try {
      const result = await fn(new Db(conn));
      await conn.commit();
      return result;
    } catch (err) {
      await conn.rollback();
      throw err;
    }
  }
}

module.exports = { Db };
