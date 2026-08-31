/**
 * Squish Cloudflare Worker with Static Assets.
 *
 * PDF encryption remains in the browser. This Worker receives only the
 * already-encrypted attachment plus one-time SMTP credentials, sends the PDF
 * and key as separate messages. Single-use links store only a locally wrapped
 * key envelope in a per-link Durable Object until redemption or expiry.
 */
import { connect } from 'cloudflare:sockets';
import { DurableObject } from 'cloudflare:workers';
import {
  AuthError,
  authConfiguration,
  handleAuthRoute,
  readSession,
  requireSession,
} from './squish-auth.mjs';

const MAX_ENCRYPTED_PDF_BYTES = 12 * 1024 * 1024;
const MAX_REQUEST_BYTES = 18 * 1024 * 1024;
const MAX_ATTACHMENTS = 10;
const SMTP_STAGE_TIMEOUT_MS = 15000;
const SMTP_CONNECT_TIMEOUT_MS = 8000;
const SMTP_CLOSE_TIMEOUT_MS = 2000;
const SMTP_OVERALL_TIMEOUT_MS = 90000;
const MAX_SMTP_RESPONSE_BYTES = 16 * 1024;
const MAX_SMTP_RESPONSE_LINES = 100;
const MIN_BURNER_TTL_MS = 60 * 1000;
const MAX_BURNER_TTL_MS = 72 * 60 * 60 * 1000;
const BURNER_ID_RE = /^[A-Za-z0-9_-]{32}$/;
const BURNER_VALUE_RE = /^[A-Za-z0-9_-]+$/;

export class BurnerLink extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.ready = ctx.blockConcurrencyWhile(async () => {
      ctx.storage.sql.exec(`
        CREATE TABLE IF NOT EXISTS burner_envelope (
          singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
          ciphertext TEXT NOT NULL,
          iv TEXT NOT NULL,
          expires_at INTEGER NOT NULL,
          created_at INTEGER NOT NULL
        )
      `);
    });
  }

  async create(envelope) {
    await this.ready;
    const now = Date.now();
    const expiresAt = Number(envelope?.expires_at);
    if (!BURNER_VALUE_RE.test(String(envelope?.ciphertext || '')) ||
        String(envelope.ciphertext).length > 4096 ||
        !BURNER_VALUE_RE.test(String(envelope?.iv || '')) ||
        String(envelope.iv).length > 64 ||
        !Number.isSafeInteger(expiresAt) ||
        expiresAt < now + MIN_BURNER_TTL_MS ||
        expiresAt > now + MAX_BURNER_TTL_MS) {
      throw new Error('Invalid burner-link envelope');
    }
    const inserted = this.ctx.storage.sql.exec(
      `INSERT OR IGNORE INTO burner_envelope
       (singleton, ciphertext, iv, expires_at, created_at)
       VALUES (1, ?, ?, ?, ?)`,
      String(envelope.ciphertext), String(envelope.iv), expiresAt, now
    );
    if (inserted.rowsWritten !== 1) throw new Error('Burner link already exists');
    await this.ctx.storage.setAlarm(expiresAt);
    return {created: true, expires_at: expiresAt};
  }

  async redeem() {
    await this.ready;
    return this.ctx.storage.transactionSync(() => {
      const row = this.ctx.storage.sql.exec(
        'SELECT ciphertext, iv, expires_at FROM burner_envelope WHERE singleton = 1'
      ).toArray()[0];
      if (!row) return null;
      this.ctx.storage.sql.exec('DELETE FROM burner_envelope WHERE singleton = 1');
      if (Number(row.expires_at) <= Date.now()) return null;
      return {
        ciphertext: String(row.ciphertext),
        iv: String(row.iv),
        expires_at: Number(row.expires_at),
      };
    });
  }

  alarm() {
    this.ctx.storage.sql.exec('DELETE FROM burner_envelope WHERE singleton = 1');
  }
}

function cleanHeader(value) {
  return String(value ?? '').replace(/[\r\n\x00]/g, '').trim();
}

function cleanFilename(value) {
  return cleanHeader(value).replace(/["\\/<>:|?*]/g, '_').slice(0, 160) || 'document_protected.pdf';
}

function validateEmail(value, label = 'Email address') {
  const email = cleanHeader(value);
  if (!email || email.includes(',') || email.includes(';') ||
      !/^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(email)) {
    throw new Error(`${label} must be one valid email address`);
  }
  return email;
}

function validateSmtp(config, policy = {}) {
  const smtp = config || {};
  const server = cleanHeader(smtp.server || smtp.host);
  const username = validateEmail(smtp.username, 'SMTP username');
  const password = String(smtp.password || '');
  const port = Number.parseInt(smtp.port, 10) || 587;
  const security = cleanHeader(smtp.security || (port === 465 ? 'ssl' : 'starttls')).toLowerCase();
  if (!server || !/^[a-z0-9.-]+$/i.test(server)) throw new Error('A valid SMTP host is required');
  if (!password) throw new Error('SMTP password is required');
  if (password.length > 2048) throw new Error('SMTP password is too long');
  if (port < 1 || port > 65535) throw new Error('SMTP port is invalid');
  if (port === 25) throw new Error('Cloudflare blocks outbound SMTP on port 25; use 465 or 587');
  if (!['ssl', 'starttls'].includes(security)) throw new Error('Use SSL or STARTTLS for SMTP');
  const allowedHosts = new Set(String(policy.SMTP_ALLOWED_HOSTS || '').split(',').map(v => v.trim().toLowerCase()).filter(Boolean));
  if (allowedHosts.size && !allowedHosts.has(server.toLowerCase())) {
    throw new Error('SMTP host is not permitted by SMTP_ALLOWED_HOSTS');
  }
  const allowedPorts = new Set(String(policy.SMTP_ALLOWED_PORTS || '').split(',').map(v => Number.parseInt(v.trim(), 10)).filter(Number.isInteger));
  if (allowedPorts.size && !allowedPorts.has(port)) {
    throw new Error('SMTP port is not permitted by SMTP_ALLOWED_PORTS');
  }
  return {
    server, username, password, port, security,
    from_name: cleanHeader(smtp.from_name || smtp.fromName)
  };
}

function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...extraHeaders}
  });
}

function assertSameOrigin(request) {
  const origin = request.headers.get('Origin');
  const fetchSite = String(request.headers.get('Sec-Fetch-Site') || '').toLowerCase();
  if (fetchSite && !['same-origin', 'none'].includes(fetchSite)) {
    throw new Error('Cross-origin email dispatch is not allowed');
  }
  if (origin && origin !== new URL(request.url).origin) {
    throw new Error('Cross-origin email dispatch is not allowed');
  }
}

async function readJson(request) {
  const length = Number(request.headers.get('Content-Length') || 0);
  if (length > MAX_REQUEST_BYTES) throw new Error('Encrypted attachment is too large for the Cloudflare email worker');
  try {
    if (!request.body) throw new Error('empty body');
    const reader = request.body.getReader();
    const chunks = [];
    let total = 0;
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_REQUEST_BYTES) {
        await reader.cancel('request too large');
        throw new Error('request too large');
      }
      chunks.push(value);
      if (chunks.length > 4096) {
        await reader.cancel('request too fragmented');
        throw new Error('request too fragmented');
      }
    }
    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch (error) {
    if (error?.message === 'request too large' || error?.message === 'request too fragmented') {
      throw new Error('Encrypted attachment is too large for the Cloudflare email worker');
    }
    throw new Error('Request body must be valid JSON');
  }
}

function utf8Base64(value) {
  const bytes = new TextEncoder().encode(String(value ?? ''));
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

function wrapBase64(value) {
  return String(value).match(/.{1,76}/g)?.join('\r\n') || '';
}

function wireBase64Size(value) {
  const length = String(value || '').length;
  return length + (2 * Math.ceil(length / 76));
}

function parseEhloCapabilities(response) {
  const capabilities = new Map();
  for (const line of String(response || '').split('\n')) {
    const value = line.replace(/^250[- ]?/, '').trim();
    if (!value) continue;
    const [rawName, ...rest] = value.split(/\s+/);
    const [name, inlineValue = ''] = rawName.toUpperCase().split('=', 2);
    capabilities.set(name, [inlineValue, ...rest].filter(Boolean).join(' '));
  }
  return capabilities;
}

function encodedHeader(value) {
  const cleaned = cleanHeader(value);
  return /[^\x20-\x7e]/.test(cleaned) ? `=?UTF-8?B?${utf8Base64(cleaned)}?=` : cleaned;
}

function htmlEscape(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[ch]);
}

const ALLOWED_EMAIL_TAGS = new Set(['a','b','blockquote','br','center','code','div','em','h1','h2','h3','h4','hr','i','li','ol','p','pre','small','span','strong','table','tbody','td','tfoot','th','thead','tr','u','ul']);
const ALLOWED_EMAIL_ATTRS = new Set(['align','bgcolor','border','cellpadding','cellspacing','colspan','height','href','role','rowspan','style','target','valign','width']);

function sanitizeEmailHtml(value) {
  const source = String(value || '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<(script|style|iframe|object|svg|math)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, '')
    .replace(/<(?:img|embed|form|input|button|meta|link|base)\b[^>]*\/?\s*>/gi, '');
  let output = '';
  let offset = 0;
  const tags = /<\s*(\/?)\s*([a-z0-9]+)([^>]*)>/gi;
  for (const match of source.matchAll(tags)) {
    output += htmlEscape(source.slice(offset, match.index));
    offset = match.index + match[0].length;
    const closing = match[1] === '/';
    const tag = match[2].toLowerCase();
    if (!ALLOWED_EMAIL_TAGS.has(tag)) continue;
    if (closing) {
      if (!['br', 'hr'].includes(tag)) output += `</${tag}>`;
      continue;
    }
    const attrs = [];
    const attrPattern = /([^\s=\/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
    for (const attr of match[3].matchAll(attrPattern)) {
      const name = attr[1].toLowerCase();
      const attrValue = attr[2] ?? attr[3] ?? attr[4] ?? '';
      if (!ALLOWED_EMAIL_ATTRS.has(name) || name.startsWith('on')) continue;
      if (name === 'href' && !/^(?:https:\/\/|mailto:|tel:|#)/i.test(attrValue)) continue;
      if (name === 'style' && /(url\s*\(|expression\s*\(|@import|behavior\s*:|-moz-binding)/i.test(attrValue)) continue;
      if (tag === 'a' && ['target', 'rel'].includes(name)) continue;
      attrs.push(` ${name}="${htmlEscape(attrValue)}"`);
    }
    if (tag === 'a') attrs.push(' target="_blank" rel="noopener noreferrer"');
    output += `<${tag}${attrs.join('')}>`;
  }
  output += htmlEscape(source.slice(offset));
  return output;
}

function smtpDiagnostic(error, client) {
  const diagnosticId = crypto.randomUUID().replaceAll('-', '').slice(0, 8);
  const rawMessage = cleanHeader(error?.message || '').slice(0, 220);
  const relayResponse = cleanHeader(error?.relayResponse || '').slice(0, 220);
  const smtpCode = Number.isInteger(error?.smtpCode) ? error.smtpCode : null;
  let stage = cleanHeader(error?.smtpStage || client?.stage || 'connection');
  let category = 'connection_failed';
  let message = 'The SMTP connection failed.';
  let hint = 'Check the email worker log using the diagnostic ID below.';

  if (stage === 'configuration') {
    category = 'invalid_configuration';
    message = rawMessage || 'The SMTP configuration is invalid.';
    hint = 'Correct the SMTP host, port, username, password, and security settings.';
  } else if (stage === 'message_size') {
    category = 'message_too_large';
    message = 'The encoded email exceeds the relay message-size limit.';
    hint = 'Reduce the attachment size or use a relay with a larger advertised SIZE limit.';
  } else if (/dns|resolve|not known|name lookup/i.test(rawMessage)) {
    stage = 'dns'; category = 'host_not_found';
    message = 'The SMTP hostname could not be resolved.';
    hint = 'Check the server hostname and DNS available to the Squish deployment.';
  } else if (/certificate|cert |x509|tls|ssl/i.test(rawMessage) || stage === 'starttls') {
    const starttlsFailed = stage === 'starttls';
    stage = 'tls'; category = starttlsFailed ? 'starttls_failed' : 'tls_failed';
    message = 'TLS negotiation with the SMTP server failed.';
    hint = 'Confirm that the selected port and security mode match the relay configuration.';
  } else if (stage.startsWith('authentication') || [530, 534, 535].includes(smtpCode)) {
    stage = 'authentication';
    if ([534, 535].includes(smtpCode)) {
      category = 'credentials_rejected';
      message = 'The SMTP server rejected the username or password.';
      hint = 'Confirm the authentication username, password, and whether this account permits SMTP AUTH.';
    } else if (/not implemented|not supported|not advertised|unrecognized|unknown command/i.test(relayResponse || rawMessage)) {
      category = 'auth_not_supported';
      message = 'The SMTP server does not offer a supported authentication method.';
      hint = 'The relay may use IP allowlisting instead of SMTP AUTH; ask the relay administrator.';
    } else {
      category = smtpCode === 530 ? 'authentication_required' : 'authentication_failed';
      message = smtpCode === 530
        ? 'The SMTP server requires authentication.'
        : 'SMTP authentication failed.';
      hint = 'Check the relay response, account permissions, and source-IP policy.';
    }
  } else if (/timed? ?out|timeout/i.test(rawMessage)) {
    category = 'timeout';
    message = `The SMTP server timed out during ${stage.replaceAll('_', ' ')}.`;
    hint = 'Check firewall rules, relay availability, and access from the actual Squish deployment.';
  } else if (/refused/i.test(rawMessage)) {
    stage = 'connection'; category = 'connection_refused';
    message = 'The SMTP server refused the TCP connection.';
    hint = 'Check the hostname, port, firewall, and whether the relay is listening.';
  } else if (/disconnect|closed|ended/i.test(rawMessage)) {
    category = 'server_disconnected';
    message = `The SMTP server disconnected during ${stage.replaceAll('_', ' ')}.`;
    hint = 'Check the relay log and whether it permits Cloudflare outbound connections.';
  } else if (smtpCode !== null) {
    category = stage === 'greeting' ? 'greeting_rejected' : 'relay_rejected';
    message = stage === 'greeting'
      ? 'The SMTP server rejected the initial connection.'
      : 'The SMTP server rejected the request.';
    hint = 'Check the SMTP reply and whether the Cloudflare source IP is permitted.';
  }

  const result = {ok:false, stage, category, error:message, hint, diagnostic_id:diagnosticId};
  if (smtpCode !== null) result.smtp_code = smtpCode;
  if (relayResponse) result.relay_response = relayResponse;
  console.error('SMTP connection test failed', {stage, category, smtpCode, diagnosticId});
  return result;
}

class SmtpClient {
  constructor(config, policy = {}) {
    this.config = validateSmtp(config, policy);
    this.socket = null;
    this.reader = null;
    this.writer = null;
    this.buffer = '';
    this.encoder = new TextEncoder();
    this.decoder = new TextDecoder();
    this.stage = 'configuration';
    this.capabilities = new Map();
    this.expiresAt = Date.now() + SMTP_OVERALL_TIMEOUT_MS;
  }

  async deadline(promise, timeoutMs = SMTP_STAGE_TIMEOUT_MS) {
    const remaining = this.expiresAt - Date.now();
    const effectiveTimeout = Math.max(1, Math.min(timeoutMs, remaining));
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => {
        const error = new Error(`SMTP timeout during ${this.stage}`);
        error.smtpStage = this.stage;
        try { void this.socket?.close(); } catch {}
        reject(error);
      }, effectiveTimeout);
    });
    try {
      return await Promise.race([promise, timeout]);
    } finally {
      clearTimeout(timer);
    }
  }

  async write(value) {
    await this.deadline(this.writer.write(this.encoder.encode(value)));
  }

  async open() {
    const implicitTls = this.config.security === 'ssl' || this.config.port === 465;
    this.stage = 'connection';
    this.socket = connect(
      {hostname: this.config.server, port: this.config.port},
      {secureTransport: implicitTls ? 'on' : 'starttls'}
    );
    await this.deadline(this.socket.opened, SMTP_CONNECT_TIMEOUT_MS);
    this.reader = this.socket.readable.getReader();
    this.writer = this.socket.writable.getWriter();
    this.stage = 'greeting';
    this.expect(await this.readResponse(), 220, 'SMTP greeting');
    this.stage = 'ehlo';
    const initialEhlo = await this.command('EHLO squish.app');
    this.expect(initialEhlo, 250, 'EHLO');
    this.capabilities = parseEhloCapabilities(initialEhlo);

    if (!implicitTls) {
      this.stage = 'starttls';
      if (!this.capabilities.has('STARTTLS')) {
        const error = new Error('STARTTLS was not advertised by the SMTP server');
        error.smtpStage = this.stage;
        error.relayResponse = initialEhlo;
        throw error;
      }
      this.expect(await this.command('STARTTLS'), 220, 'STARTTLS');
      this.reader.releaseLock();
      this.writer.releaseLock();
      this.socket = this.socket.startTls();
      this.reader = this.socket.readable.getReader();
      this.writer = this.socket.writable.getWriter();
      this.buffer = '';
      await this.deadline(this.socket.opened, SMTP_CONNECT_TIMEOUT_MS);
      this.stage = 'ehlo_after_tls';
      const secureEhlo = await this.command('EHLO squish.app');
      this.expect(secureEhlo, 250, 'EHLO after STARTTLS');
      this.capabilities = parseEhloCapabilities(secureEhlo);
    }
    this.stage = 'authentication';
    await this.authenticate();
    this.stage = 'authenticated';
  }

  expect(response, code, step) {
    if (!String(response).startsWith(String(code))) {
      const relayResponse = cleanHeader(response).slice(0, 220);
      const error = new Error(`${step} failed`);
      error.smtpStage = this.stage;
      error.smtpCode = /^\d{3}/.test(relayResponse) ? Number.parseInt(relayResponse.slice(0, 3), 10) : null;
      error.relayResponse = relayResponse;
      throw error;
    }
  }

  async authenticate() {
    const advertised = String(this.capabilities.get('AUTH') || '').toUpperCase().split(/\s+/).filter(Boolean);
    if (!advertised.length) {
      const error = new Error('SMTP AUTH was not advertised by the server');
      error.smtpStage = 'authentication';
      error.relayResponse = 'AUTH not advertised';
      throw error;
    }
    const plain = utf8Base64(`\0${this.config.username}\0${this.config.password}`);
    if (advertised.includes('PLAIN')) {
      this.stage = 'authentication_credentials';
      const response = await this.command(`AUTH PLAIN ${plain}`);
      if (response.startsWith('334')) {
        this.expect(await this.command(plain), 235, 'SMTP authentication');
      } else {
        this.expect(response, 235, 'SMTP authentication');
      }
      return;
    }
    if (!advertised.includes('LOGIN')) {
      const error = new Error(`No supported SMTP AUTH mechanism: ${advertised.join(' ')}`);
      error.smtpStage = 'authentication';
      error.relayResponse = `AUTH ${advertised.join(' ')}`;
      throw error;
    }
    this.stage = 'authentication_method';
    const login = await this.command('AUTH LOGIN');
    this.expect(login, 334, 'SMTP AUTH LOGIN');
    this.stage = 'authentication_username';
    this.expect(await this.command(utf8Base64(this.config.username)), 334, 'SMTP username');
    this.stage = 'authentication_credentials';
    this.expect(await this.command(utf8Base64(this.config.password)), 235, 'SMTP authentication');
  }

  async command(command) {
    await this.write(`${command}\r\n`);
    return this.readResponse();
  }

  async readResponse() {
    const lines = [];
    let responseBytes = 0;
    while (true) {
      const end = this.buffer.indexOf('\r\n');
      if (end >= 0) {
        const line = this.buffer.slice(0, end);
        this.buffer = this.buffer.slice(end + 2);
        lines.push(line);
        responseBytes += line.length + 2;
        if (lines.length > MAX_SMTP_RESPONSE_LINES || responseBytes > MAX_SMTP_RESPONSE_BYTES) {
          const error = new Error('SMTP response exceeded the safety limit');
          error.smtpStage = this.stage;
          throw error;
        }
        if (line.length < 4 || line[3] !== '-') return lines.join('\n');
        continue;
      }
      const {value, done} = await this.deadline(this.reader.read());
      if (done) return lines.length ? lines.join('\n') : this.buffer;
      this.buffer += this.decoder.decode(value, {stream: true});
      if (this.buffer.length + responseBytes > MAX_SMTP_RESPONSE_BYTES) {
        const error = new Error('SMTP response exceeded the safety limit');
        error.smtpStage = this.stage;
        throw error;
      }
    }
  }

  async writeBase64Lines(encoded) {
    // Keep peak memory bounded: at most ~61 KB of wrapped attachment data is
    // materialized for each socket write instead of another full MIME copy.
    const linesPerWrite = 800;
    const charsPerWrite = 76 * linesPerWrite;
    for (let offset = 0; offset < encoded.length; offset += charsPerWrite) {
      const chunk = encoded.slice(offset, offset + charsPerWrite);
      const lines = [];
      for (let i = 0; i < chunk.length; i += 76) lines.push(chunk.slice(i, i + 76));
      await this.write(lines.join('\r\n') + '\r\n');
    }
  }

  async sendMail({to, subject, text, html, attachment, attachments, inReplyTo, references, messageId}) {
    const recipient = validateEmail(to, 'Recipient');
    const sender = this.config.username;
    const mixed = `squish-mixed-${crypto.randomUUID()}`;
    const alt = `squish-alt-${crypto.randomUUID()}`;
    const display = this.config.from_name ? `${encodedHeader(this.config.from_name)} <${sender}>` : sender;
    const msgId = messageId || `<${crypto.randomUUID()}@squish.app>`;
    const allAttachments = attachments || (attachment ? [attachment] : []);
    const encodedText = utf8Base64(text);
    const encodedHtml = html ? utf8Base64(html) : '';
    const estimatedSize = allAttachments.reduce((sum, item) => sum + wireBase64Size(item.base64), 0) +
      wireBase64Size(encodedText) + wireBase64Size(encodedHtml) + 8192;
    const advertisedSize = Number.parseInt(this.capabilities.get('SIZE') || '', 10);
    if (Number.isFinite(advertisedSize) && advertisedSize > 0 && estimatedSize > advertisedSize) {
      const error = new Error(`Encoded message is approximately ${estimatedSize} bytes; relay limit is ${advertisedSize}`);
      error.smtpStage = 'message_size';
      error.relayResponse = `SIZE ${advertisedSize}`;
      throw error;
    }

    const mailFrom = `MAIL FROM:<${sender}>${this.capabilities.has('SIZE') ? ` SIZE=${estimatedSize}` : ''}`;
    this.stage = 'envelope';
    if (this.capabilities.has('PIPELINING')) {
      await this.write(`${mailFrom}\r\nRCPT TO:<${recipient}>\r\nDATA\r\n`);
      this.expect(await this.readResponse(), 250, 'MAIL FROM');
      this.expect(await this.readResponse(), 250, 'RCPT TO');
      this.expect(await this.readResponse(), 354, 'DATA');
    } else {
      this.expect(await this.command(mailFrom), 250, 'MAIL FROM');
      this.expect(await this.command(`RCPT TO:<${recipient}>`), 250, 'RCPT TO');
      this.expect(await this.command('DATA'), 354, 'DATA');
    }

    const lines = [
      `From: ${display}`, `To: <${recipient}>`, `Subject: ${encodedHeader(subject)}`,
      `Date: ${new Date().toUTCString()}`, `Message-ID: ${msgId}`,
      'MIME-Version: 1.0'
    ];
    if (inReplyTo) lines.push(`In-Reply-To: ${inReplyTo}`);
    if (references) lines.push(`References: ${references}`);
    lines.push(`Content-Type: multipart/mixed; boundary="${mixed}"`, '', `--${mixed}`);
    if (html) {
      lines.push(
        `Content-Type: multipart/alternative; boundary="${alt}"`, '',
        `--${alt}`, 'Content-Type: text/plain; charset="utf-8"', 'Content-Transfer-Encoding: base64', '',
        wrapBase64(encodedText), '',
        `--${alt}`, 'Content-Type: text/html; charset="utf-8"', 'Content-Transfer-Encoding: base64', '',
        wrapBase64(encodedHtml), '', `--${alt}--`, ''
      );
    } else {
      lines.push('Content-Type: text/plain; charset="utf-8"', 'Content-Transfer-Encoding: base64', '',
        wrapBase64(encodedText), '');
    }
    this.stage = 'message_upload';
    await this.write(lines.join('\r\n') + '\r\n');
    for (const item of allAttachments) {
      const filename = cleanFilename(item.filename);
      await this.write([
        `--${mixed}`, `Content-Type: application/pdf; name="${filename}"`,
        `Content-Disposition: attachment; filename="${filename}"`,
        'Content-Transfer-Encoding: base64', ''
      ].join('\r\n') + '\r\n');
      await this.writeBase64Lines(item.base64);
    }
    await this.write(`--${mixed}--\r\n.\r\n`);
    this.stage = 'message_delivery';
    this.expect(await this.readResponse(), 250, 'Message delivery');
    return msgId;
  }

  async close() {
    try { if (this.writer) await this.deadline(this.command('QUIT'), SMTP_CLOSE_TIMEOUT_MS); } catch {}
    try { if (this.socket) await this.socket.close(); } catch {}
  }
}

function retryJitterMs(attempt) {
  const value = new Uint32Array(1);
  crypto.getRandomValues(value);
  return Math.min(1500, 150 * (2 ** attempt)) + (value[0] % 151);
}

async function openSmtpClient(config, policy, maxRetries = 1) {
  let lastError;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const client = new SmtpClient(config, policy);
    try {
      await client.open();
      client.connectRetries = attempt;
      return client;
    } catch (error) {
      error.smtpStage ||= client.stage;
      lastError = error;
      await client.close();
      const transientCode = [421, 450, 451].includes(error.smtpCode);
      const transientStage = ['connection', 'greeting'].includes(error.smtpStage);
      if (attempt >= maxRetries || (!transientCode && !transientStage)) throw error;
      await new Promise(resolve => setTimeout(resolve, retryJitterMs(attempt)));
    }
  }
  throw lastError;
}

function validatePdfBase64(value) {
  const encoded = String(value || '');
  if (!encoded || !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded)) throw new Error('Encrypted PDF data is missing or invalid');
  const approxBytes = Math.floor(encoded.length * 3 / 4);
  if (approxBytes > MAX_ENCRYPTED_PDF_BYTES) throw new Error('Encrypted PDF exceeds the 12 MB Cloudflare email limit');
  return encoded;
}

function validateAttachments(payload) {
  const raw = Array.isArray(payload.attachments) && payload.attachments.length
    ? payload.attachments
    : [{filename: payload.pdfFilename, base64: payload.pdfBase64}];
  if (raw.length > MAX_ATTACHMENTS) throw new Error(`A maximum of ${MAX_ATTACHMENTS} PDF attachments is allowed`);
  let total = 0;
  const attachments = raw.map(item => {
    const base64 = validatePdfBase64(item?.base64);
    total += Math.floor(base64.length * 3 / 4);
    return {filename: cleanFilename(item?.filename), base64};
  });
  if (total > MAX_ENCRYPTED_PDF_BYTES) {
    throw new Error('Combined encrypted PDFs exceed the 12 MB Cloudflare email limit');
  }
  return attachments;
}

function passwordMessage(password, docName, template) {
  if (template) {
    const rendered = String(template)
      .replace(/\{\{password\}\}/g, password)
      .replace(/\{\{(?:doc_name|filename)\}\}/g, docName);
    if (/<[a-z][\s\S]*>/i.test(rendered)) {
      return {text: `Decryption key: ${password}\nFile: ${docName}`, html: sanitizeEmailHtml(rendered)};
    }
    return {text: rendered, html: rendered.split('\n').map(line => `<p>${htmlEscape(line)}</p>`).join('')};
  }
  return {
    text: `Your password to open ${docName} is:\n\n${password}\n\nKeep this password secure.`,
    html: `<h2>Document decryption key</h2><p>Use this password to open <strong>${htmlEscape(docName)}</strong>:</p><p style="font:700 20px monospace;letter-spacing:2px">${htmlEscape(password)}</p><p>Keep this password secure.</p>`
  };
}

async function dispatch(payload, policy = {}) {
  const smtp = validateSmtp(payload.smtp, policy);
  const recipient = validateEmail(payload.recipient, 'Recipient');
  const password = String(payload.password || '');
  if (password.length < 4) throw new Error('Decryption password must be at least 4 characters');
  const attachments = validateAttachments(payload);
  const pdfFile = attachments[0].filename;
  const attachmentLabel = attachments.length === 1 ? pdfFile : `${attachments.length} encrypted PDF files`;
  const subject = cleanHeader(payload.subject) || attachmentLabel;
  const keyDeliveryMode = cleanHeader(payload.keyDeliveryMode || payload.key_delivery_mode || 'email').toLowerCase();
  if (!['email', 'oob', 'dual'].includes(keyDeliveryMode)) throw new Error('Invalid key delivery mode');
  const client = await openSmtpClient(smtp, policy);
  let firstSent = false;
  try {
    const email1MessageId = `<${crypto.randomUUID()}@squish.app>`;
    const deliveryText = keyDeliveryMode === 'oob'
      ? 'Its password will be delivered through a separate channel.'
      : 'Its password will arrive in a separate email.';
    const plainTextOnly = payload.plainTextOnly === true || payload.plainTextOnly === '1' ||
      payload.email_plain_text_only === true || payload.email_plain_text_only === '1';
    const email1Text = plainTextOnly && payload.htmlBody
      ? String(payload.htmlBody)
      : `${attachmentLabel} attached. ${deliveryText}`;
    const email1Html = plainTextOnly ? null : sanitizeEmailHtml(payload.htmlBody || `<h2>Secure document attached</h2><p><strong>${htmlEscape(attachmentLabel)}</strong> attached. ${htmlEscape(deliveryText)}</p>`);
    await client.sendMail({
      to: recipient,
      subject: `[Secure Document] ${subject}`,
      text: email1Text,
      html: email1Html,
      attachments,
      messageId: email1MessageId
    });
    firstSent = true;
    if (keyDeliveryMode !== 'oob') {
      const configuredDelay = payload.delaySeconds ?? payload.delay_seconds ?? 0.1;
      const delayMs = Math.min(10000, Math.max(0, Number(configuredDelay) * 1000));
      await new Promise(resolve => setTimeout(resolve, delayMs));
      const second = passwordMessage(password, attachmentLabel, payload.email2Body);
      const threadEmails = Boolean(payload.threadEmails || payload.thread_emails);
      let secondSubject;
      if (threadEmails) {
        secondSubject = cleanHeader(payload.email2Subject) || `Re: [Secure Document] ${subject}`;
      } else {
        secondSubject = (cleanHeader(payload.email2Subject) || `[Decryption Key] Password for: ${subject}`)
          .replace(/\{\{(?:doc_name|filename)\}\}/g, attachmentLabel);
      }
      await client.sendMail({
        to: recipient, subject: secondSubject, text: second.text, html: second.html,
        inReplyTo: threadEmails ? email1MessageId : undefined,
        references: threadEmails ? email1MessageId : undefined
      });
    }
    return {
      status: 'success', recipient, pdf_file: pdfFile,
      attachments: attachments.map(item => item.filename),
      step1_sent: true, step2_sent: keyDeliveryMode !== 'oob',
      key_delivery_mode: keyDeliveryMode,
      retries: client.connectRetries || 0,
      oob_required: keyDeliveryMode === 'oob' || keyDeliveryMode === 'dual',
      timestamp: new Date().toISOString()
    };
  } catch (error) {
    if (!firstSent) {
      error.smtpStage ||= client.stage;
      throw error;
    }
    const diagnostic = smtpDiagnostic(error, client);
    return {
      status: 'partial_failure', recipient, pdf_file: pdfFile,
      attachments: attachments.map(item => item.filename),
      step1_sent: true, step2_sent: false, error: diagnostic.error, diagnostic,
      key_delivery_mode: keyDeliveryMode,
      resend: {
        recipient, pdf_filename: pdfFile, subject,
        email2_subject: cleanHeader(payload.email2Subject), email2_body: String(payload.email2Body || '')
      },
      timestamp: new Date().toISOString()
    };
  } finally {
    await client.close();
  }
}

async function resendKey(payload, policy = {}) {
  const smtp = validateSmtp(payload.smtp, policy);
  const recipient = validateEmail(payload.recipient, 'Recipient');
  const password = String(payload.password || '');
  const pdfFile = cleanFilename(payload.pdf_filename || payload.pdfFilename);
  if (password.length < 4) throw new Error('Decryption password is missing');
  const content = passwordMessage(password, pdfFile, payload.email2_body || payload.email2Body);
  const subject = (cleanHeader(payload.email2_subject || payload.email2Subject) || `[Decryption Key] Password for: ${cleanHeader(payload.subject) || pdfFile}`)
    .replace(/\{\{(?:doc_name|filename)\}\}/g, pdfFile);
  const client = await openSmtpClient(smtp, policy);
  try {
    await client.sendMail({to: recipient, subject, text: content.text, html: content.html});
    return {ok: true, recipient, timestamp: new Date().toISOString()};
  } finally {
    await client.close();
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    try {
      const authResponse = await handleAuthRoute(request, env, ctx);
      if (authResponse) return authResponse;
    } catch (error) {
      if (error instanceof AuthError) {
        return json({ok: false, code: error.code, error: error.message}, error.status);
      }
      console.error(JSON.stringify({
        message: 'hosted_auth_route_failed',
        path: url.pathname,
        error: error instanceof Error ? error.message : String(error),
      }));
      return json({ok: false, code: 'auth_service_error', error: 'Hosted account service is temporarily unavailable'}, 500);
    }
    try {
      if (url.pathname.startsWith('/api/')) assertSameOrigin(request);
      if (request.method === 'OPTIONS' && url.pathname.startsWith('/api/')) {
        return new Response(null, {status: 204, headers: {'Allow': 'GET, POST, OPTIONS'}});
      }
      if (url.pathname === '/api/cloudflare-capabilities' && request.method === 'GET') {
        const config = authConfiguration(env);
        const session = await readSession(request, env);
        return json({
          secure_email: true,
          max_encrypted_pdf_mb: 12,
          max_attachments: MAX_ATTACHMENTS,
          auth: 'google',
          auth_configured: config.configured,
          authenticated: Boolean(session),
          cloud_vault: config.vault_configured,
          burner_links: Boolean(env.BURNER_LINKS),
        });
      }
      if (url.pathname === '/api/burner-links' && request.method === 'POST') {
        await requireSession(request, env);
        if (!env.BURNER_LINKS) throw new Error('Burner links are not configured');
        const body = await readJson(request);
        const id = String(body.id || '');
        if (!BURNER_ID_RE.test(id)) throw new Error('Invalid burner-link id');
        const result = await env.BURNER_LINKS.getByName(id).create({
          ciphertext: body.ciphertext,
          iv: body.iv,
          expires_at: body.expires_at,
        });
        return json({ok: true, ...result}, 201);
      }
      if (url.pathname === '/api/burner-links/redeem' && request.method === 'POST') {
        if (!env.BURNER_LINKS) throw new Error('Burner links are not configured');
        const body = await readJson(request);
        const id = String(body.id || '');
        if (!BURNER_ID_RE.test(id)) throw new Error('Invalid burner-link id');
        const envelope = await env.BURNER_LINKS.getByName(id).redeem();
        return envelope
          ? json({ok: true, ...envelope})
          : json({ok: false, code: 'gone', error: 'This link was already opened or has expired'}, 410);
      }
      if (url.pathname === '/api/smtp/test' && request.method === 'POST') {
        await requireSession(request, env);
        const body = await readJson(request);
        let client = null;
        try {
          client = new SmtpClient(body.smtp || body, env);
          await client.open();
          return json({ok: true, message: `Connected and authenticated to ${client.config.server}:${client.config.port}`});
        } catch (error) {
          return json(smtpDiagnostic(error, client || {stage:'configuration'}), 400);
        } finally {
          if (client) await client.close();
        }
      }
      if ((url.pathname === '/api/t/email-secure' || url.pathname === '/api/send-secure-email') && request.method === 'POST') {
        await requireSession(request, env);
        try {
          const receipt = await dispatch(await readJson(request), env);
          return json(receipt, receipt.status === 'partial_failure' ? 207 : 200);
        } catch (error) {
          return json(smtpDiagnostic(error, {stage:error?.smtpStage || 'connection'}), 502);
        }
      }
      if (url.pathname === '/api/smtp/resend-key' && request.method === 'POST') {
        await requireSession(request, env);
        try {
          return json(await resendKey(await readJson(request), env));
        } catch (error) {
          return json(smtpDiagnostic(error, {stage:error?.smtpStage || 'connection'}), 502);
        }
      }
      if (url.pathname.startsWith('/api/')) return json({error: 'Endpoint not found'}, 404);
      if (env.ASSETS) {
        const response = await env.ASSETS.fetch(request);
        if (url.pathname === '/unlock' || url.pathname === '/unlock.html') {
          const headers = new Headers(response.headers);
          headers.set('Cache-Control', 'no-store');
          headers.set('Referrer-Policy', 'no-referrer');
          headers.set('X-Content-Type-Options', 'nosniff');
          headers.set('X-Frame-Options', 'DENY');
          headers.set('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
          return new Response(response.body, {status: response.status, statusText: response.statusText, headers});
        }
        return response;
      }
      return new Response('Not found', {status: 404});
    } catch (error) {
      if (error instanceof AuthError) {
        return json({ok: false, code: error.code, error: error.message}, error.status);
      }
      return json({ok: false, error: error?.message || 'Unexpected email worker error'}, 400);
    }
  }
};
