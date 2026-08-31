import assert from 'node:assert/strict';
import test from 'node:test';
import {signPayload} from '../backend/static/squish-auth.mjs';
import {createBurnerLink, redeemBurnerLink} from '../backend/static/burner-links.mjs';

const base = process.env.SQUISH_WORKER_URL;
const secret = process.env.SQUISH_TEST_SESSION_SECRET;

test('Durable Object permits exactly one concurrent redemption', {skip:!base || !secret}, async () => {
  const now = Math.floor(Date.now() / 1000);
  const token = await signPayload({
    kind:'session', sub:'integration-user', email:'integration@example.test',
    name:'Integration Test', picture:'', iat:now, exp:now + 600,
  }, secret, 'session-v1');
  const authenticatedFetch = (path, options = {}) => fetch(new URL(path, base), {
    ...options,
    headers:{...options.headers, Origin:new URL(base).origin, Cookie:`__Host-squish_session=${encodeURIComponent(token)}`},
  });
  const anonymousFetch = (path, options = {}) => fetch(new URL(path, base), {
    ...options, headers:{...options.headers, Origin:new URL(base).origin},
  });
  const created = await createBurnerLink('atomic-password', {fetchImpl:authenticatedFetch, origin:base, ttlHours:1});
  const hash = new URL(created.url).hash;
  const results = await Promise.allSettled([
    redeemBurnerLink({hash, fetchImpl:anonymousFetch}),
    redeemBurnerLink({hash, fetchImpl:anonymousFetch}),
  ]);
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
  assert.equal(results.filter(result => result.status === 'rejected').length, 1);
  assert.equal(results.find(result => result.status === 'fulfilled').value, 'atomic-password');
});

test('unlock route has non-cache and non-referrer protections', {skip:!base}, async () => {
  const response = await fetch(new URL('/unlock', base));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('referrer-policy'), 'no-referrer');
  assert.match(response.headers.get('content-security-policy') || '', /frame-ancestors 'none'/);
});
