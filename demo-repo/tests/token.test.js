'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { isExpired, CLOCK_SKEW_MS } = require('../src/auth/token');

const base = { accessToken: 'a', refreshToken: 'r', expiresAt: 1_000_000 };

test('여유 시간 밖이면 만료가 아니다', () => {
  assert.equal(isExpired(base, base.expiresAt - CLOCK_SKEW_MS - 1), false);
});

test('여유 시간 안으로 들어오면 만료로 본다', () => {
  assert.equal(isExpired(base, base.expiresAt - CLOCK_SKEW_MS), true);
});

test('이미 지났으면 만료다', () => {
  assert.equal(isExpired(base, base.expiresAt + 1), true);
});
