/**
 * Google OpenID Connect and encrypted cloud-vault support for hosted Squish.
 *
 * Google proves who the user is. It is deliberately not used as an
 * encryption key. Session and vault keys are purpose-separated from the
 * high-entropy SESSION_SECRET with HKDF.
 */

const GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth';
const GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token';
const GOOGLE_JWKS_URL = 'https://www.googleapis.com/oauth2/v3/certs';
const SESSION_COOKIE = '__Host-squish_session';
const OAUTH_COOKIE = '__Host-squish_oauth';
const SESSION_SECONDS = 8 * 60 * 60;
const OAUTH_SECONDS = 10 * 60;
const CLOCK_SKEW_SECONDS = 60;
const MAX_TOKEN_BYTES = 24 * 1024;
const MAX_VAULT_BYTES = 512 * 1024;
const encoder = new TextEncoder();
const decoder = new TextDecoder();

export class AuthError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = 'AuthError';
    this.status = status;
    this.code = code;
  }
}

function bytesToBase64Url(value) {
  const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function base64UrlToBytes(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]*$/.test(value)) {
    throw new AuthError(400, 'invalid_token', 'Invalid encoded token');
  }
  const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function randomToken(bytes = 32) {
  return bytesToBase64Url(crypto.getRandomValues(new Uint8Array(bytes)));
}

async function sha256(value) {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(value)));
}

async function purposeKey(secret, purpose, algorithm, usages) {
  if (typeof secret !== 'string' || secret.length < 32) {
    throw new AuthError(503, 'auth_not_configured', 'SESSION_SECRET must contain at least 32 characters');
  }
  const material = await crypto.subtle.importKey('raw', encoder.encode(secret), 'HKDF', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    {
      name: 'HKDF',
      hash: 'SHA-256',
      salt: encoder.encode('squish-auth-root-v1'),
      info: encoder.encode(purpose),
    },
    material,
    algorithm,
    false,
    usages,
  );
}

async function signingKey(secret, purpose) {
  return purposeKey(secret, purpose, {name: 'HMAC', hash: 'SHA-256', length: 256}, ['sign', 'verify']);
}

export async function signPayload(payload, secret, purpose = 'session-v1') {
  const encoded = bytesToBase64Url(encoder.encode(JSON.stringify(payload)));
  const key = await signingKey(secret, `squish-${purpose}`);
  const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(encoded));
  return `${encoded}.${bytesToBase64Url(signature)}`;
}

export async function verifySignedPayload(token, secret, purpose = 'session-v1') {
  if (typeof token !== 'string' || token.length > MAX_TOKEN_BYTES) return null;
  const parts = token.split('.');
  if (parts.length !== 2) return null;
  try {
    const key = await signingKey(secret, `squish-${purpose}`);
    const supplied = base64UrlToBytes(parts[1]);
    const expected = new Uint8Array(await crypto.subtle.sign('HMAC', key, encoder.encode(parts[0])));
    let valid = false;
    if (supplied.byteLength === expected.byteLength && typeof crypto.subtle.timingSafeEqual === 'function') {
      valid = crypto.subtle.timingSafeEqual(supplied, expected);
    } else if (supplied.byteLength === expected.byteLength) {
      let diff = 0;
      for (let i = 0; i < supplied.byteLength; i++) diff |= supplied[i] ^ expected[i];
      valid = diff === 0;
    }
    if (!valid) return null;
    const payload = JSON.parse(decoder.decode(base64UrlToBytes(parts[0])));
    return payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : null;
  } catch {
    return null;
  }
}

export function canonicalOrigin(env) {
  let value = String(env?.APP_ORIGIN || '').trim().replace(/\/$/, '');
  if (value && !/^https?:\/\//i.test(value)) value = `https://${value}`;
  let parsed;
  try { parsed = new URL(value); } catch { throw new AuthError(503, 'auth_not_configured', 'APP_ORIGIN is invalid'); }
  const local = parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1';
  if ((!local && parsed.protocol !== 'https:') || (local && !['http:', 'https:'].includes(parsed.protocol))) {
    throw new AuthError(503, 'auth_not_configured', 'APP_ORIGIN must use HTTPS');
  }
  return parsed.origin;
}

export function safeReturnTo(value) {
  const path = String(value || '/');
  if (!path.startsWith('/') || path.startsWith('//') || path.startsWith('/auth/')) return '/';
  return path.slice(0, 1200);
}

export function parseCookies(request) {
  const result = {};
  for (const part of String(request.headers.get('Cookie') || '').split(';')) {
    const index = part.indexOf('=');
    if (index < 1) continue;
    const name = part.slice(0, index).trim();
    try { result[name] = decodeURIComponent(part.slice(index + 1).trim()); } catch {}
  }
  return result;
}

export function secureCookie(name, value, maxAge) {
  return `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=${Math.max(0, Math.floor(maxAge))}; HttpOnly; Secure; SameSite=Lax`;
}

function clearCookie(name) {
  return secureCookie(name, '', 0);
}

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...headers},
  });
}

function redirect(location, headers = {}, cookies = []) {
  const responseHeaders = new Headers({'Location': location, 'Cache-Control': 'no-store', ...headers});
  for (const cookie of cookies) responseHeaders.append('Set-Cookie', cookie);
  return new Response(null, {status: 302, headers: responseHeaders});
}

export function authConfiguration(env) {
  try {
    canonicalOrigin(env);
    return {
      configured: Boolean(env?.GOOGLE_CLIENT_ID && env?.GOOGLE_CLIENT_SECRET &&
        typeof env?.SESSION_SECRET === 'string' && env.SESSION_SECRET.length >= 32),
      vault_configured: Boolean(env?.AUTH_DB),
    };
  } catch {
    return {configured: false, vault_configured: Boolean(env?.AUTH_DB)};
  }
}

function requireConfiguration(env) {
  const config = authConfiguration(env);
  if (!config.configured) {
    throw new AuthError(503, 'auth_not_configured', 'Google sign-in is not fully configured');
  }
  return config;
}

export async function pkceChallenge(verifier) {
  return bytesToBase64Url(await sha256(verifier));
}

async function createOAuthTransaction(env, returnTo) {
  const now = Math.floor(Date.now() / 1000);
  const transaction = {
    kind: 'oauth',
    state: randomToken(32),
    nonce: randomToken(32),
    verifier: randomToken(48),
    return_to: safeReturnTo(returnTo),
    iat: now,
    exp: now + OAUTH_SECONDS,
  };
  return {
    transaction,
    cookie: await signPayload(transaction, env.SESSION_SECRET, 'oauth-state-v1'),
  };
}

function stringClaim(value, max = 1024) {
  return typeof value === 'string' ? value.slice(0, max) : '';
}

async function fetchGoogleJwks() {
  const cacheRequest = new Request(GOOGLE_JWKS_URL, {method: 'GET'});
  let response = null;
  if (typeof caches !== 'undefined' && caches.default) response = await caches.default.match(cacheRequest);
  if (!response) {
    response = await fetch(cacheRequest, {headers: {'Accept': 'application/json'}});
    if (!response.ok) throw new AuthError(502, 'google_keys_unavailable', 'Google signing keys are unavailable');
    if (typeof caches !== 'undefined' && caches.default) await caches.default.put(cacheRequest, response.clone());
  }
  const length = Number(response.headers.get('Content-Length') || 0);
  if (length > 256 * 1024) throw new AuthError(502, 'google_keys_invalid', 'Google signing keys response is too large');
  const data = await response.json();
  if (!data || !Array.isArray(data.keys)) throw new AuthError(502, 'google_keys_invalid', 'Google signing keys response is invalid');
  return data.keys;
}

export async function verifyGoogleIdToken(idToken, env, expectedNonce, jwks = null) {
  if (typeof idToken !== 'string' || idToken.length > MAX_TOKEN_BYTES) {
    throw new AuthError(401, 'invalid_id_token', 'Google returned an invalid identity token');
  }
  const parts = idToken.split('.');
  if (parts.length !== 3) throw new AuthError(401, 'invalid_id_token', 'Google returned an invalid identity token');
  let header;
  let claims;
  try {
    header = JSON.parse(decoder.decode(base64UrlToBytes(parts[0])));
    claims = JSON.parse(decoder.decode(base64UrlToBytes(parts[1])));
  } catch {
    throw new AuthError(401, 'invalid_id_token', 'Google returned an invalid identity token');
  }
  if (header?.alg !== 'RS256' || typeof header?.kid !== 'string') {
    throw new AuthError(401, 'invalid_id_token', 'Google identity token algorithm is invalid');
  }
  const keys = jwks || await fetchGoogleJwks();
  const jwk = keys.find(key => key?.kid === header.kid && key?.kty === 'RSA');
  if (!jwk) throw new AuthError(401, 'unknown_signing_key', 'Google identity token signing key was not found');
  const verificationKey = await crypto.subtle.importKey(
    'jwk', jwk, {name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256'}, false, ['verify']);
  const signatureValid = await crypto.subtle.verify(
    'RSASSA-PKCS1-v1_5', verificationKey, base64UrlToBytes(parts[2]), encoder.encode(`${parts[0]}.${parts[1]}`));
  if (!signatureValid) throw new AuthError(401, 'invalid_id_token', 'Google identity token signature is invalid');

  const now = Math.floor(Date.now() / 1000);
  const issuerValid = claims.iss === 'https://accounts.google.com' || claims.iss === 'accounts.google.com';
  const audience = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  const audienceValid = audience.includes(env.GOOGLE_CLIENT_ID) &&
    (audience.length === 1 || claims.azp === env.GOOGLE_CLIENT_ID);
  if (!issuerValid || !audienceValid || !Number.isFinite(claims.exp) || claims.exp < now - CLOCK_SKEW_SECONDS ||
      (Number.isFinite(claims.iat) && claims.iat > now + CLOCK_SKEW_SECONDS) ||
      (Number.isFinite(claims.nbf) && claims.nbf > now + CLOCK_SKEW_SECONDS) ||
      claims.nonce !== expectedNonce || claims.email_verified !== true || !stringClaim(claims.sub, 255) ||
      !stringClaim(claims.email, 320)) {
    throw new AuthError(401, 'invalid_id_token', 'Google identity token claims are invalid');
  }
  return {
    sub: stringClaim(claims.sub, 255),
    email: stringClaim(claims.email, 320),
    name: stringClaim(claims.name, 160),
    picture: stringClaim(claims.picture, 1000),
  };
}

async function exchangeCode(code, verifier, env) {
  const redirectUri = `${canonicalOrigin(env)}/auth/google/callback`;
  const body = new URLSearchParams({
    code,
    client_id: env.GOOGLE_CLIENT_ID,
    client_secret: env.GOOGLE_CLIENT_SECRET,
    redirect_uri: redirectUri,
    grant_type: 'authorization_code',
    code_verifier: verifier,
  });
  const response = await fetch(GOOGLE_TOKEN_URL, {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
    body,
  });
  const length = Number(response.headers.get('Content-Length') || 0);
  if (length > 256 * 1024) throw new AuthError(502, 'token_exchange_failed', 'Google token response is too large');
  const data = await response.json().catch(() => ({}));
  if (!response.ok || typeof data.id_token !== 'string') {
    throw new AuthError(401, 'token_exchange_failed', 'Google sign-in could not be completed');
  }
  return data.id_token;
}

async function createSession(user, env) {
  const now = Math.floor(Date.now() / 1000);
  return signPayload({kind: 'session', ...user, iat: now, exp: now + SESSION_SECONDS}, env.SESSION_SECRET, 'session-v1');
}

export async function readSession(request, env) {
  if (!authConfiguration(env).configured) return null;
  const token = parseCookies(request)[SESSION_COOKIE];
  if (!token) return null;
  const payload = await verifySignedPayload(token, env.SESSION_SECRET, 'session-v1');
  const now = Math.floor(Date.now() / 1000);
  if (!payload || payload.kind !== 'session' || !Number.isFinite(payload.exp) || payload.exp < now ||
      !stringClaim(payload.sub, 255) || !stringClaim(payload.email, 320)) return null;
  return {
    sub: stringClaim(payload.sub, 255),
    email: stringClaim(payload.email, 320),
    name: stringClaim(payload.name, 160),
    picture: stringClaim(payload.picture, 1000),
    expires_at: payload.exp,
  };
}

export async function requireSession(request, env) {
  const session = await readSession(request, env);
  if (!session) throw new AuthError(401, 'authentication_required', 'Sign in with Google to continue');
  return session;
}

function assertSameOrigin(request, env) {
  const origin = request.headers.get('Origin');
  if (origin && origin !== canonicalOrigin(env)) {
    throw new AuthError(403, 'cross_origin_request', 'Cross-origin account requests are not allowed');
  }
}

async function upsertUser(env, user) {
  if (!env.AUTH_DB) return;
  const now = new Date().toISOString();
  await env.AUTH_DB.prepare(
    `INSERT INTO oauth_users (google_sub, email, display_name, picture_url, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT(google_sub) DO UPDATE SET
       email = excluded.email, display_name = excluded.display_name,
       picture_url = excluded.picture_url, updated_at = excluded.updated_at`,
  ).bind(user.sub, user.email, user.name, user.picture, now, now).run();
}

function callbackFailure(env, code) {
  const target = new URL('/', canonicalOrigin(env));
  target.searchParams.set('auth_error', String(code || 'sign_in_failed').slice(0, 80));
  return redirect(target.toString(), {}, [clearCookie(OAUTH_COOKIE)]);
}

async function startGoogle(request, env) {
  requireConfiguration(env);
  const requestUrl = new URL(request.url);
  const {transaction, cookie} = await createOAuthTransaction(env, requestUrl.searchParams.get('return_to'));
  const authorization = new URL(GOOGLE_AUTH_URL);
  authorization.search = new URLSearchParams({
    client_id: env.GOOGLE_CLIENT_ID,
    redirect_uri: `${canonicalOrigin(env)}/auth/google/callback`,
    response_type: 'code',
    scope: 'openid email profile',
    state: transaction.state,
    nonce: transaction.nonce,
    code_challenge: await pkceChallenge(transaction.verifier),
    code_challenge_method: 'S256',
    access_type: 'online',
  }).toString();
  return redirect(authorization.toString(), {}, [secureCookie(OAUTH_COOKIE, cookie, OAUTH_SECONDS)]);
}

async function finishGoogle(request, env) {
  requireConfiguration(env);
  const url = new URL(request.url);
  if (url.searchParams.get('error')) return callbackFailure(env, 'google_denied');
  const code = url.searchParams.get('code');
  const returnedState = url.searchParams.get('state');
  const signedTransaction = parseCookies(request)[OAUTH_COOKIE];
  const transaction = signedTransaction
    ? await verifySignedPayload(signedTransaction, env.SESSION_SECRET, 'oauth-state-v1')
    : null;
  const now = Math.floor(Date.now() / 1000);
  if (!code || !returnedState || !transaction || transaction.kind !== 'oauth' ||
      !Number.isFinite(transaction.exp) || transaction.exp < now) return callbackFailure(env, 'invalid_oauth_state');
  const [givenState, expectedState] = await Promise.all([sha256(returnedState), sha256(transaction.state || '')]);
  const statesMatch = typeof crypto.subtle.timingSafeEqual === 'function'
    ? crypto.subtle.timingSafeEqual(givenState, expectedState)
    : givenState.every((byte, index) => byte === expectedState[index]);
  if (!statesMatch) return callbackFailure(env, 'invalid_oauth_state');
  try {
    const idToken = await exchangeCode(code, transaction.verifier, env);
    const user = await verifyGoogleIdToken(idToken, env, transaction.nonce);
    await upsertUser(env, user);
    const session = await createSession(user, env);
    return redirect(
      new URL(safeReturnTo(transaction.return_to), canonicalOrigin(env)).toString(),
      {},
      [secureCookie(SESSION_COOKIE, session, SESSION_SECONDS), clearCookie(OAUTH_COOKIE)],
    );
  } catch (error) {
    console.error(JSON.stringify({message: 'google_oauth_callback_failed', code: error?.code || 'unexpected'}));
    return callbackFailure(env, error?.code || 'sign_in_failed');
  }
}

function bounded(value, max, label, required = false) {
  const result = typeof value === 'string' ? value : '';
  if (required && !result.trim()) throw new AuthError(400, 'invalid_vault', `${label} is required`);
  if (encoder.encode(result).byteLength > max) throw new AuthError(400, 'invalid_vault', `${label} is too large`);
  return result;
}

function validId(value, prefix) {
  const id = bounded(value, 120, `${prefix} id`, true);
  if (!/^[A-Za-z0-9_.:-]+$/.test(id)) throw new AuthError(400, 'invalid_vault', `${prefix} id is invalid`);
  return id;
}

export function validateCloudVault(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new AuthError(400, 'invalid_vault', 'Cloud vault is invalid');
  }
  const profiles = Array.isArray(payload.profiles) ? payload.profiles : [];
  const templates = Array.isArray(payload.templates) ? payload.templates : [];
  const history = Array.isArray(payload.history) ? payload.history : [];
  if (profiles.length > 5 || templates.length > 20 || history.length > 50) {
    throw new AuthError(400, 'invalid_vault', 'Cloud vault exceeds its item limits');
  }
  const uniqueIds = new Set();
  const takeId = (value, prefix) => {
    const id = validId(value, prefix);
    if (uniqueIds.has(`${prefix}:${id}`)) throw new AuthError(400, 'invalid_vault', `Duplicate ${prefix} id`);
    uniqueIds.add(`${prefix}:${id}`);
    return id;
  };
  const safeProfiles = profiles.map(profile => {
    const port = Number(profile?.port);
    const security = bounded(profile?.security || 'starttls', 20, 'SMTP security');
    if (!Number.isInteger(port) || port < 1 || port > 65535 || !['starttls', 'ssl', 'none'].includes(security)) {
      throw new AuthError(400, 'invalid_vault', 'SMTP profile settings are invalid');
    }
    return {
      id: takeId(profile?.id, 'profile'),
      name: bounded(profile?.name || 'SMTP Profile', 160, 'Profile name'),
      server: bounded(profile?.server, 255, 'SMTP server', true),
      port,
      username: bounded(profile?.username, 320, 'SMTP username', true),
      password: bounded(profile?.password, 4096, 'SMTP password', true),
      from_name: bounded(profile?.from_name, 320, 'Sender name'),
      security,
      created_at: bounded(profile?.created_at, 80, 'Profile timestamp'),
    };
  });
  const safeTemplates = templates.map(template => {
    const html = bounded(template?.html, 100 * 1024, 'Template HTML', true);
    if (/\{\{\s*password\s*\}\}/i.test(html)) throw new AuthError(400, 'invalid_vault', 'Email templates cannot contain passwords');
    return {
      id: takeId(template?.id, 'template'),
      name: bounded(template?.name, 160, 'Template name', true),
      subject: bounded(template?.subject, 480, 'Template subject'),
      html,
      text: bounded(template?.text, 100 * 1024, 'Template text'),
      updated_at: bounded(template?.updated_at, 80, 'Template timestamp'),
    };
  });
  const safeHistory = history.map(record => ({
    id: takeId(record?.id, 'history'),
    timestamp: bounded(record?.timestamp, 80, 'History timestamp'),
    recipient: bounded(record?.recipient || 'Unknown', 640, 'History recipient'),
    pdf_filename: bounded(record?.pdf_filename || 'document.pdf', 512, 'History filename'),
    attachments: Array.isArray(record?.attachments)
      ? record.attachments.slice(0, 10).map(name => bounded(name, 512, 'Attachment filename', true))
      : [],
    subject: bounded(record?.subject, 480, 'History subject'),
    status: bounded(record?.status || 'success', 40, 'History status'),
    phone: bounded(record?.phone, 32, 'History phone'),
    pages: bounded(record?.pages, 2048, 'History pages'),
    key_delivery_mode: ['email', 'oob', 'dual'].includes(record?.key_delivery_mode) ? record.key_delivery_mode : 'email',
    signed: Boolean(record?.signed),
    message: bounded(record?.message, 4096, 'History message'),
    error: bounded(record?.error, 4096, 'History error'),
    password: bounded(record?.password, 4096, 'History password'),
    has_password: Boolean(record?.password || record?.has_password),
  }));
  const safe = {schema_version: 1, profiles: safeProfiles, templates: safeTemplates, history: safeHistory};
  if (encoder.encode(JSON.stringify(safe)).byteLength > MAX_VAULT_BYTES) {
    throw new AuthError(413, 'vault_too_large', 'Cloud vault exceeds 512 KB');
  }
  return safe;
}

async function vaultKey(env, googleSub) {
  return purposeKey(
    env.SESSION_SECRET,
    `squish-cloud-vault-v1:${googleSub}`,
    {name: 'AES-GCM', length: 256},
    ['encrypt', 'decrypt'],
  );
}

export async function encryptCloudVault(payload, env, googleSub) {
  const safe = validateCloudVault(payload);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await vaultKey(env, googleSub);
  const ciphertext = await crypto.subtle.encrypt(
    {name: 'AES-GCM', iv, additionalData: encoder.encode(`squish-vault:${googleSub}:v1`)},
    key,
    encoder.encode(JSON.stringify(safe)),
  );
  return {schema_version: 1, iv: bytesToBase64Url(iv), ciphertext: bytesToBase64Url(ciphertext)};
}

export async function decryptCloudVault(envelope, env, googleSub) {
  if (!envelope || Number(envelope.schema_version) !== 1) throw new AuthError(500, 'vault_unreadable', 'Cloud vault format is unsupported');
  try {
    const key = await vaultKey(env, googleSub);
    const plaintext = await crypto.subtle.decrypt(
      {name: 'AES-GCM', iv: base64UrlToBytes(envelope.iv), additionalData: encoder.encode(`squish-vault:${googleSub}:v1`)},
      key,
      base64UrlToBytes(envelope.ciphertext),
    );
    return validateCloudVault(JSON.parse(decoder.decode(plaintext)));
  } catch (error) {
    if (error instanceof AuthError) throw error;
    throw new AuthError(500, 'vault_unreadable', 'Cloud vault could not be decrypted');
  }
}

function requireVaultDatabase(env) {
  if (!env.AUTH_DB) throw new AuthError(503, 'vault_not_configured', 'AUTH_DB is not bound to this Pages project');
  return env.AUTH_DB;
}

async function readBoundedJson(request) {
  const length = Number(request.headers.get('Content-Length') || 0);
  if (length > MAX_VAULT_BYTES + 16 * 1024) throw new AuthError(413, 'vault_too_large', 'Cloud vault request is too large');
  if (!request.body) throw new AuthError(400, 'invalid_json', 'Request body must be valid JSON');
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_VAULT_BYTES + 16 * 1024) {
      await reader.cancel('Cloud vault request is too large');
      throw new AuthError(413, 'vault_too_large', 'Cloud vault request is too large');
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  const text = decoder.decode(bytes);
  try { return JSON.parse(text); } catch { throw new AuthError(400, 'invalid_json', 'Request body must be valid JSON'); }
}

async function handleVault(request, env) {
  assertSameOrigin(request, env);
  const session = await requireSession(request, env);
  const database = requireVaultDatabase(env);
  if (request.method === 'GET') {
    const row = await database.prepare(
      'SELECT schema_version, iv, ciphertext, updated_at FROM oauth_vaults WHERE google_sub = ?',
    ).bind(session.sub).first();
    if (!row) return json({ok: true, vault: validateCloudVault({}), updated_at: null});
    const vault = await decryptCloudVault(row, env, session.sub);
    return json({ok: true, vault, updated_at: row.updated_at || null});
  }
  if (request.method === 'PUT') {
    const body = await readBoundedJson(request);
    const vault = validateCloudVault(body?.vault ?? body);
    const encrypted = await encryptCloudVault(vault, env, session.sub);
    const now = new Date().toISOString();
    await database.batch([
      database.prepare(
        `INSERT INTO oauth_users (google_sub, email, display_name, picture_url, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?)
         ON CONFLICT(google_sub) DO UPDATE SET email = excluded.email,
           display_name = excluded.display_name, picture_url = excluded.picture_url,
           updated_at = excluded.updated_at`,
      ).bind(session.sub, session.email, session.name, session.picture, now, now),
      database.prepare(
        `INSERT INTO oauth_vaults (google_sub, schema_version, iv, ciphertext, updated_at)
         VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(google_sub) DO UPDATE SET schema_version = excluded.schema_version,
           iv = excluded.iv, ciphertext = excluded.ciphertext, updated_at = excluded.updated_at`,
      ).bind(session.sub, encrypted.schema_version, encrypted.iv, encrypted.ciphertext, now),
    ]);
    return json({ok: true, updated_at: now});
  }
  if (request.method === 'DELETE') {
    await database.prepare('DELETE FROM oauth_vaults WHERE google_sub = ?').bind(session.sub).run();
    return json({ok: true});
  }
  return json({ok: false, error: 'Method not allowed'}, 405, {'Allow': 'GET, PUT, DELETE'});
}

export async function handleAuthRoute(request, env) {
  const url = new URL(request.url);
  if (url.pathname === '/api/runtime' && request.method === 'GET') {
    const config = authConfiguration(env);
    const session = await readSession(request, env);
    return json({
      mode: 'hosted',
      auth: {provider: 'google', required_for_vault: true, configured: config.configured, authenticated: Boolean(session)},
      vault: {mode: 'cloud', configured: config.vault_configured},
      user: session ? {email: session.email, name: session.name, picture: session.picture, expires_at: session.expires_at} : null,
    });
  }
  if (url.pathname === '/api/auth/session' && request.method === 'GET') {
    const config = authConfiguration(env);
    const session = await readSession(request, env);
    return json({
      configured: config.configured,
      authenticated: Boolean(session),
      user: session ? {email: session.email, name: session.name, picture: session.picture, expires_at: session.expires_at} : null,
      vault_configured: config.vault_configured,
    });
  }
  if (url.pathname === '/auth/google/start' && request.method === 'GET') return startGoogle(request, env);
  if (url.pathname === '/auth/google/callback' && request.method === 'GET') return finishGoogle(request, env);
  if (url.pathname === '/api/auth/logout' && request.method === 'POST') {
    requireConfiguration(env);
    assertSameOrigin(request, env);
    return json({ok: true}, 200, {'Set-Cookie': clearCookie(SESSION_COOKIE)});
  }
  if (url.pathname === '/api/vault') return handleVault(request, env);
  return null;
}
