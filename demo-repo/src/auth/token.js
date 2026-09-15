'use strict';

const { postJson } = require('../http/client');

/** 만료 판정 시 앞당겨 잡는 여유. 시계 오차와 왕복 시간을 흡수한다. */
const CLOCK_SKEW_MS = 60_000;

const TOKEN_ENDPOINT = process.env.ORDERLY_TOKEN_ENDPOINT ?? 'https://auth.internal/oauth/token';

/**
 * @typedef {{ accessToken: string, refreshToken: string, expiresAt: number }} TokenSet
 */

/** @param {TokenSet} token */
function isExpired(token, now = Date.now()) {
  return now >= token.expiresAt - CLOCK_SKEW_MS;
}

/**
 * 리프레시 토큰으로 새 토큰 세트를 받아온다.
 * 실패는 그대로 던진다. 재시도 정책은 호출자가 정한다.
 *
 * @param {TokenSet} token
 * @returns {Promise<TokenSet>}
 */
async function refresh(token) {
  const body = await postJson(TOKEN_ENDPOINT, {
    grant_type: 'refresh_token',
    refresh_token: token.refreshToken,
  });

  return {
    accessToken: body.access_token,
    refreshToken: body.refresh_token ?? token.refreshToken,
    expiresAt: Date.now() + body.expires_in * 1000,
  };
}

/**
 * 만료가 임박했으면 갱신하고, 아니면 그대로 돌려준다.
 *
 * @param {TokenSet} token
 * @returns {Promise<TokenSet>}
 */
async function ensureFresh(token) {
  if (!isExpired(token)) {
    return token;
  }
  return refresh(token);
}

module.exports = { isExpired, refresh, ensureFresh, CLOCK_SKEW_MS };
