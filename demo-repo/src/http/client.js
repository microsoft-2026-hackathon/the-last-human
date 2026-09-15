'use strict';

/** 일시적 실패로 간주하는 상태 코드. */
const TRANSIENT_STATUS = new Set([429, 500, 502, 503, 504]);

class HttpError extends Error {
  constructor(status, body) {
    super(`HTTP ${status}`);
    this.status = status;
    this.body = body;
  }

  get transient() {
    return TRANSIENT_STATUS.has(this.status);
  }
}

async function postJson(url, payload, options = {}) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...(options.headers ?? {}) },
    body: JSON.stringify(payload),
    signal: options.signal,
  });

  const text = await res.text();
  if (!res.ok) {
    throw new HttpError(res.status, text);
  }
  return text ? JSON.parse(text) : null;
}

module.exports = { postJson, HttpError, TRANSIENT_STATUS };
