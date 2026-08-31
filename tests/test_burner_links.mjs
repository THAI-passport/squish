import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import test from 'node:test';

globalThis.crypto ??= webcrypto;
const burner = await import('../backend/static/burner-links.mjs');

test('burner link keeps the wrapping secret out of the request', async () => {
  let stored;
  const fetchImpl = async (url, options) => {
    assert.equal(url, '/api/burner-links');
    stored = JSON.parse(options.body);
    return new Response(JSON.stringify({ok:true, expires_at:stored.expires_at}), {status:201});
  };
  const result = await burner.createBurnerLink('correct horse battery staple', {
    fetchImpl, origin:'https://squish.test', ttlHours:24,
  });
  const fragment = burner.parseUnlockFragment(new URL(result.url).hash);
  assert.equal(stored.id, fragment.id);
  assert.equal(stored.secret, undefined);
  assert.equal(JSON.stringify(stored).includes(fragment.secret), false);
  assert.equal(stored.ciphertext.includes('correct'), false);
});

test('redeems and decrypts one envelope exactly once', async () => {
  let envelope;
  const createFetch = async (_url, options) => {
    envelope = JSON.parse(options.body);
    return new Response(JSON.stringify({ok:true, expires_at:envelope.expires_at}), {status:201});
  };
  const created = await burner.createBurnerLink('single-use-password', {
    fetchImpl:createFetch, origin:'https://squish.test', ttlHours:1,
  });
  let available = true;
  const redeemFetch = async (_url, options) => {
    const body = JSON.parse(options.body);
    assert.equal(body.id, envelope.id);
    if (!available) return new Response(JSON.stringify({error:'This link was already opened or has expired'}), {status:410});
    available = false;
    return new Response(JSON.stringify({ok:true, ciphertext:envelope.ciphertext, iv:envelope.iv, expires_at:envelope.expires_at}));
  };
  assert.equal(await burner.redeemBurnerLink({hash:new URL(created.url).hash, fetchImpl:redeemFetch}), 'single-use-password');
  await assert.rejects(
    burner.redeemBurnerLink({hash:new URL(created.url).hash, fetchImpl:redeemFetch}),
    /already opened or has expired/
  );
});

test('AES-GCM rejects a modified envelope', async () => {
  const id = burner.encodeBase64Url(new Uint8Array(24).fill(7));
  const secret = new Uint8Array(32).fill(9);
  const iv = new Uint8Array(12).fill(3);
  const ciphertext = await burner.encryptBurnerSecret('hidden', id, secret, iv);
  const bytes = burner.decodeBase64Url(ciphertext);
  bytes[0] ^= 1;
  await assert.rejects(
    burner.decryptBurnerSecret({ciphertext:burner.encodeBase64Url(bytes), iv:burner.encodeBase64Url(iv)}, id, secret)
  );
});
