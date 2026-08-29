/**
 * Cloudflare Worker for Squish Secure Email Dispatch (Zero VPS)
 * Uses cloudflare:sockets to connect directly to SMTP servers via raw TCP.
 */

import { connect } from 'cloudflare:sockets';

export default {
  async fetch(request, env, ctx) {
    // CORS headers
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-API-Key',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    const url = new URL(request.url);

    try {
      if (url.pathname === '/api/smtp/test' && request.method === 'POST') {
        const body = await request.json();
        const testResult = await testSmtpConnection(body.smtp);
        return new Response(JSON.stringify(testResult), {
          headers: { ...corsHeaders, 'Content-Type': 'application/json' },
          status: testResult.ok ? 200 : 400
        });
      }

      if ((url.pathname === '/api/send-secure-email' || url.pathname === '/api/t/email-secure') && request.method === 'POST') {
        const payload = await request.json();
        const dispatchResult = await dispatchDualEmail(payload);
        return new Response(JSON.stringify(dispatchResult), {
          headers: { ...corsHeaders, 'Content-Type': 'application/json' },
          status: dispatchResult.ok ? 200 : 400
        });
      }

      return new Response(JSON.stringify({ error: 'Endpoint not found' }), {
        status: 404,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' }
      });
    } catch (err) {
      return new Response(JSON.stringify({ ok: false, error: err.message }), {
        status: 500,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' }
      });
    }
  }
};

/**
 * Robust SMTP Socket Driver over cloudflare:sockets
 */
class SmtpClient {
  constructor(config) {
    this.host = config.server || config.host;
    this.port = parseInt(config.port, 10) || 587;
    this.username = config.username;
    this.password = config.password;
    this.fromName = config.from_name || config.fromName || '';
    this.security = config.security || (this.port === 465 ? 'ssl' : 'starttls');
    this.socket = null;
    this.reader = null;
    this.writer = null;
    this.encoder = new TextEncoder();
    this.decoder = new TextDecoder();
    this.buffer = '';
  }

  async connect() {
    const isSsl = this.port === 465 || this.security === 'ssl';
    this.socket = connect(
      { hostname: this.host, port: this.port },
      { secureTransport: isSsl ? 'on' : 'off' }
    );
    this.reader = this.socket.readable.getReader();
    this.writer = this.socket.writable.getWriter();

    // Read initial greeting (220)
    const greeting = await this.readResponse();
    if (!greeting.startsWith('220')) {
      throw new Error(`SMTP Greeting failed: ${greeting}`);
    }

    // EHLO
    let ehloResp = await this.sendCommand(`EHLO squish.local`);
    if (!ehloResp.startsWith('250')) {
      ehloResp = await this.sendCommand(`HELO squish.local`);
    }

    // Handle STARTTLS for port 587 or starttls mode
    if (!isSsl && (this.security === 'starttls' || this.port === 587)) {
      const starttlsResp = await this.sendCommand('STARTTLS');
      if (!starttlsResp.startsWith('220')) {
        throw new Error(`STARTTLS rejected by server: ${starttlsResp}`);
      }

      // Upgrade socket to TLS
      this.reader.releaseLock();
      this.writer.releaseLock();
      this.socket = this.socket.startTls();
      this.reader = this.socket.readable.getReader();
      this.writer = this.socket.writable.getWriter();

      // Repeat EHLO after TLS negotiation
      await this.sendCommand(`EHLO squish.local`);
    }

    // Authenticate if credentials provided
    if (this.username && this.password) {
      await this.authenticate();
    }
  }

  async authenticate() {
    // Try AUTH LOGIN
    const authResp = await this.sendCommand('AUTH LOGIN');
    if (authResp.startsWith('334')) {
      // Send base64 username
      const uResp = await this.sendCommand(btoa(this.username));
      if (!uResp.startsWith('334')) {
        throw new Error(`SMTP Auth Username rejected: ${uResp}`);
      }
      // Send base64 password
      const pResp = await this.sendCommand(btoa(this.password));
      if (!pResp.startsWith('235')) {
        throw new Error(`SMTP Auth Password failed: ${pResp}`);
      }
    } else {
      // Fallback AUTH PLAIN
      const rawPlain = `\0${this.username}\0${this.password}`;
      const plainResp = await this.sendCommand(`AUTH PLAIN ${btoa(rawPlain)}`);
      if (!plainResp.startsWith('235')) {
        throw new Error(`SMTP Auth failed: ${plainResp}`);
      }
    }
  }

  async sendCommand(cmd) {
    await this.writer.write(this.encoder.encode(cmd + '\r\n'));
    return await this.readResponse();
  }

  async readResponse() {
    while (true) {
      const lineEnd = this.buffer.indexOf('\r\n');
      if (lineEnd !== -1) {
        const line = this.buffer.slice(0, lineEnd);
        this.buffer = this.buffer.slice(lineEnd + 2);
        // Multiline response check e.g. "250-..." vs "250 ..."
        if (line.length >= 4 && line[3] === '-') {
          continue; // keep reading until last line
        }
        return line;
      }
      const { value, done } = await this.reader.read();
      if (done) break;
      this.buffer += this.decoder.decode(value, { stream: true });
    }
    return this.buffer;
  }

  async sendMail({ from, to, subject, text, html, attachments }) {
    const sender = this.username || from;
    const fromHeader = this.fromName ? `"${this.fromName}" <${sender}>` : sender;

    const mFrom = await this.sendCommand(`MAIL FROM:<${sender}>`);
    if (!mFrom.startsWith('250')) throw new Error(`MAIL FROM failed: ${mFrom}`);

    const mTo = await this.sendCommand(`RCPT TO:<${to}>`);
    if (!mTo.startsWith('250')) throw new Error(`RCPT TO rejected (${to}): ${mTo}`);

    const mData = await this.sendCommand('DATA');
    if (!mData.startsWith('354')) throw new Error(`DATA rejected: ${mData}`);

    const boundary = '===Squish_Boundary_' + Math.random().toString(36).slice(2) + '===';
    const altBoundary = '===Squish_Alt_' + Math.random().toString(36).slice(2) + '===';

    const headers = [
      `From: ${fromHeader}`,
      `To: <${to}>`,
      `Subject: ${subject}`,
      `Date: ${new Date().toUTCString()}`,
      `Message-ID: <${Date.now()}.${Math.random().toString(36).slice(2)}@squish.app>`,
      `MIME-Version: 1.0`,
      `Content-Type: multipart/mixed; boundary="${boundary}"`,
      ''
    ];

    let body = headers.join('\r\n') + '\r\n';

    // Body content (Alternative: text / html)
    body += `--${boundary}\r\n`;
    body += `Content-Type: multipart/alternative; boundary="${altBoundary}"\r\n\r\n`;

    if (text) {
      body += `--${altBoundary}\r\n`;
      body += `Content-Type: text/plain; charset="utf-8"\r\n`;
      body += `Content-Transfer-Encoding: 7bit\r\n\r\n`;
      body += `${text}\r\n\r\n`;
    }

    if (html) {
      body += `--${altBoundary}\r\n`;
      body += `Content-Type: text/html; charset="utf-8"\r\n`;
      body += `Content-Transfer-Encoding: 7bit\r\n\r\n`;
      body += `${html}\r\n\r\n`;
    }

    body += `--${altBoundary}--\r\n\r\n`;

    // Attachments
    if (attachments && attachments.length > 0) {
      for (const att of attachments) {
        body += `--${boundary}\r\n`;
        body += `Content-Type: ${att.contentType || 'application/pdf'}; name="${att.filename}"\r\n`;
        body += `Content-Disposition: attachment; filename="${att.filename}"\r\n`;
        body += `Content-Transfer-Encoding: base64\r\n\r\n`;
        
        // Chunk base64 to 76 chars per line
        const b64 = att.content;
        for (let i = 0; i < b64.length; i += 76) {
          body += b64.slice(i, i + 76) + '\r\n';
        }
        body += '\r\n';
      }
    }

    body += `--${boundary}--\r\n.\r\n`;

    await this.writer.write(this.encoder.encode(body));
    const result = await this.readResponse();
    if (!result.startsWith('250')) {
      throw new Error(`Failed to deliver message: ${result}`);
    }
  }

  async close() {
    try {
      if (this.writer) {
        await this.sendCommand('QUIT');
      }
    } catch {}
    try {
      if (this.socket) {
        this.socket.close();
      }
    } catch {}
  }
}

async function testSmtpConnection(smtpConfig) {
  const client = new SmtpClient(smtpConfig);
  try {
    await client.connect();
    await client.close();
    return { ok: true, message: `Connected and authenticated successfully to ${smtpConfig.server}:${smtpConfig.port}` };
  } catch (err) {
    await client.close();
    return { ok: false, error: err.message };
  }
}

async function dispatchDualEmail(payload) {
  const { smtp, recipient, subject, pdfBase64, pdfFilename, htmlBody, password, delaySeconds } = payload;

  if (!recipient || !password || !pdfBase64) {
    return { ok: false, error: 'Recipient email, password, and PDF data are required' };
  }

  const client = new SmtpClient(smtp);
  try {
    await client.connect();

    // EMAIL 1: Document Attached
    const email1Subject = subject ? `[Secure Document] ${subject}` : `[Secure Document] Attached: ${pdfFilename || 'document.pdf'}`;
    const email1Html = htmlBody || `
      <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e5e3dc; border-radius: 8px; background: #ffffff;">
        <h2 style="color: #1b1d23; margin-top: 0;">Secure Document Attached</h2>
        <p style="color: #33363f; line-height: 1.5;">You have received an encrypted PDF document: <strong>${pdfFilename || 'document_protected.pdf'}</strong>.</p>
        <div style="background: #f7f6f3; padding: 14px 18px; border-radius: 6px; margin: 18px 0; border-left: 4px solid #2f5fd8;">
          <p style="margin: 0; color: #1b1d23; font-size: 14px;"><strong>Note:</strong> This document is password-protected for your security. The decryption key is being transmitted in a separate message shortly.</p>
        </div>
        <p style="color: #5c6070; font-size: 13px;">Dispatched via Squish Secure Dispatch.</p>
      </div>
    `;

    await client.sendMail({
      to: recipient,
      subject: email1Subject,
      text: `You have received an encrypted PDF document: ${pdfFilename || 'document.pdf'}.\n\nThis file is password-protected. The decryption password will arrive in a separate email shortly.`,
      html: email1Html,
      attachments: [{
        filename: pdfFilename || 'document_protected.pdf',
        contentType: 'application/pdf',
        content: pdfBase64
      }]
    });

    // Automated delay (default 2.5s)
    const delay = Math.max(500, (delaySeconds || 2.5) * 1000);
    await new Promise(r => setTimeout(r, delay));

    // EMAIL 2: Decryption Password
    const docName = pdfFilename || 'document.pdf';
    const email2Subject = (data.email2Subject || (subject ? `[Decryption Key] Password for: ${subject}` : `[Decryption Key] Password for: ${docName}`))
      .replace('{{doc_name}}', docName)
      .replace('{{filename}}', docName);

    let email2Html, email2Text;
    if (data.email2Body) {
      const rendered = data.email2Body.replace(/\{\{password\}\}/g, password).replace(/\{\{doc_name\}\}/g, docName).replace(/\{\{filename\}\}/g, docName);
      if (rendered.includes('<') && rendered.includes('>')) {
        email2Html = rendered;
        email2Text = `Decryption Key: ${password}\nFile: ${docName}`;
      } else {
        const paras = rendered.split('\n').filter(Boolean).map(l => `<p>${l}</p>`).join('');
        email2Html = `<div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e5e3dc; border-radius: 8px; background: #ffffff;">${paras}</div>`;
        email2Text = rendered;
      }
    } else {
      email2Html = `
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e5e3dc; border-radius: 8px; background: #ffffff;">
          <h2 style="color: #1b1d23; margin-top: 0;">Your Document Decryption Key</h2>
          <p style="color: #33363f; line-height: 1.5;">Use the password below to open the encrypted PDF document you recently received:</p>
          <div style="background: #efeee9; padding: 16px 20px; border-radius: 6px; margin: 20px 0; text-align: center; border: 1px solid #d4d1c8;">
            <span style="font-family: monospace; font-size: 20px; font-weight: 700; color: #1b1d23; letter-spacing: 2px;">${password}</span>
          </div>
          <p style="color: #5c6070; font-size: 13px;">Please keep this password secure and do not forward it alongside the encrypted document.</p>
        </div>
      `;
      email2Text = `Your password to open the protected PDF (${docName}) is:\n\n${password}\n\nPlease keep this password secure.`;
    }

    await client.sendMail({
      to: recipient,
      subject: email2Subject,
      text: email2Text,
      html: email2Html
    });

    await client.close();
    return {
      ok: true,
      message: `Successfully delivered dual secure emails to ${recipient}`,
      timestamp: new Date().toISOString()
    };
  } catch (err) {
    await client.close();
    return { ok: false, error: err.message };
  }
}
