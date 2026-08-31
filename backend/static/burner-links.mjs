const ID_BYTES = 24;
const SECRET_BYTES = 32;
const IV_BYTES = 12;
const MAX_TTL_HOURS = 72;

export function encodeBase64Url(bytes) {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

export function decodeBase64Url(value) {
  if (!/^[A-Za-z0-9_-]+$/.test(String(value || ''))) throw new Error('Invalid unlock-link data');
  const text = String(value);
  const padded = text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - text.length % 4) % 4);
  return Uint8Array.from(atob(padded), char => char.charCodeAt(0));
}

function additionalData(id) {
  return new TextEncoder().encode(`squish-burner:${id}:v1`);
}

export async function encryptBurnerSecret(password, id, secret, iv) {
  const key = await crypto.subtle.importKey('raw', secret, {name: 'AES-GCM'}, false, ['encrypt']);
  const ciphertext = await crypto.subtle.encrypt(
    {name: 'AES-GCM', iv, additionalData: additionalData(id)}, key,
    new TextEncoder().encode(String(password))
  );
  return encodeBase64Url(new Uint8Array(ciphertext));
}

export async function decryptBurnerSecret(envelope, id, secret) {
  const key = await crypto.subtle.importKey('raw', secret, {name: 'AES-GCM'}, false, ['decrypt']);
  const plaintext = await crypto.subtle.decrypt(
    {name: 'AES-GCM', iv: decodeBase64Url(envelope.iv), additionalData: additionalData(id)}, key,
    decodeBase64Url(envelope.ciphertext)
  );
  return new TextDecoder().decode(plaintext);
}

export async function createBurnerLink(password, {ttlHours = 24, fetchImpl = fetch, origin = location.origin} = {}) {
  if (String(password).length < 4) throw new Error('The decryption key is missing');
  const hours = Number(ttlHours);
  if (!Number.isFinite(hours) || hours <= 0 || hours > MAX_TTL_HOURS) throw new Error('Link lifetime must be between 1 and 72 hours');
  const id = encodeBase64Url(crypto.getRandomValues(new Uint8Array(ID_BYTES)));
  const secret = crypto.getRandomValues(new Uint8Array(SECRET_BYTES));
  const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const response = await fetchImpl('/api/burner-links', {
    method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      id,
      ciphertext: await encryptBurnerSecret(password, id, secret, iv),
      iv: encodeBase64Url(iv),
      expires_at: Date.now() + hours * 3600000,
    }),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || `Could not create link (HTTP ${response.status})`);
  return {url: `${origin}/unlock#id=${id}&secret=${encodeBase64Url(secret)}`, expiresAt: Number(result.expires_at)};
}

export function parseUnlockFragment(hash) {
  const params = new URLSearchParams(String(hash || '').replace(/^#/, ''));
  const id = params.get('id') || '';
  const secret = params.get('secret') || '';
  if (!/^[A-Za-z0-9_-]{32}$/.test(id) || decodeBase64Url(secret).length !== SECRET_BYTES) throw new Error('This unlock link is incomplete or invalid');
  return {id, secret};
}

export async function redeemBurnerLink({hash = location.hash, fetchImpl = fetch} = {}) {
  const {id, secret} = parseUnlockFragment(hash);
  if (typeof history !== 'undefined') history.replaceState(null, '', '/unlock');
  const response = await fetchImpl('/api/burner-links/redeem', {
    method: 'POST', credentials: 'omit', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'This link was already opened or has expired');
  return decryptBurnerSecret(result, id, decodeBase64Url(secret));
}

if (typeof window !== 'undefined') window.SquishBurnerLinks = {create: createBurnerLink, redeem: redeemBurnerLink};
