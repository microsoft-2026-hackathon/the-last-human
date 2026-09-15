'use strict';

const { ensureFresh } = require('./token');

const sessions = new Map();

async function currentToken(sessionId) {
  const token = sessions.get(sessionId);
  if (!token) {
    throw new Error(`unknown session: ${sessionId}`);
  }
  const fresh = await ensureFresh(token);
  if (fresh !== token) {
    sessions.set(sessionId, fresh);
  }
  return fresh;
}

function putSession(sessionId, token) {
  sessions.set(sessionId, token);
}

function dropSession(sessionId) {
  sessions.delete(sessionId);
}

module.exports = { currentToken, putSession, dropSession };
