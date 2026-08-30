/**
 * Squish Web Crypto Vault
 * Local mode: encrypted localStorage with PBKDF2 + AES-256-GCM.
 * Hosted mode: decrypted state stays in memory and is saved through the
 * authenticated cloud-vault adapter; the Worker encrypts it before D1.
 *
 * Rules:
 * 1. Passwords are never saved in plaintext.
 * 2. Stored encrypted in localStorage['squish_smtp_vault'].
 * 3. In UI / API outputs, passwords are masked ('••••••••') and insert-only.
 * 4. Only MAIL_FROM_NAME and Profile Nickname are editable in place.
 * 5. Max 5 profiles supported.
 * 6. Wipe All completely removes storage and zeroes RAM.
 */

(function(root) {
  'use strict';

  const STORAGE_KEY = 'squish_smtp_vault';
  const SESSION_PIN_KEY = 'squish_vault_session_pin';
  const HISTORY_STORAGE_KEY = 'squish_dispatch_history';
  const MAX_PROFILES = 5;
  const MAX_TEMPLATES = 20;
  const MAX_TEMPLATE_BYTES = 100 * 1024;
  const MAX_HISTORY_RECORDS = 50;
  const PBKDF2_ITERATIONS = 100000;
  const BACKUP_PBKDF2_ITERATIONS = 300000;
  const BACKUP_MAGIC = 'SQUISHVAULT';
  const BACKUP_SCHEMA_VERSION = 1;

  // In-memory decrypted state (ephemeral in RAM only)
  let activeKey = null; // CryptoKey
  let activeProfiles = null; // Array of profile objects
  let activeTemplates = null; // Array of custom email templates
  let activeHistory = null; // Hosted mode only; local mode uses localStorage
  let activeMode = 'local';
  let cloudSaveHandler = null;
  let cloudWipeHandler = null;
  let cloudWriteQueue = Promise.resolve();

  // Helper: Convert buffer to base64
  function buf2b64(buf) {
    const bytes = new Uint8Array(buf);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  }

  // Helper: Convert base64 to buffer
  function b642buf(b64) {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
  }

  // Derive AES-GCM CryptoKey from PIN/Password and salt using PBKDF2
  async function deriveKeyWithIterations(pin, salt, iterations) {
    const enc = new TextEncoder();
    const keyMaterial = await crypto.subtle.importKey(
      'raw',
      enc.encode(pin),
      { name: 'PBKDF2' },
      false,
      ['deriveKey']
    );

    return await crypto.subtle.deriveKey(
      {
        name: 'PBKDF2',
        salt: salt,
        iterations: iterations,
        hash: 'SHA-256'
      },
      keyMaterial,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt']
    );
  }

  async function deriveKey(pin, salt) {
    return deriveKeyWithIterations(pin, salt, PBKDF2_ITERATIONS);
  }

  function vaultPayload(profiles = activeProfiles, templates = activeTemplates) {
    return { schema_version: 2, profiles: profiles || [], templates: templates || [] };
  }

  function cloudHistoryPayload() {
    return (activeHistory || []).map(record => {
      const copy = { ...record };
      const cloudSecret = copy.encrypted_password?.cloud_plaintext;
      delete copy.encrypted_password;
      copy.password = typeof cloudSecret === 'string' ? cloudSecret : '';
      return copy;
    });
  }

  function cloudVaultPayload(profiles = activeProfiles, templates = activeTemplates) {
    return {
      schema_version: 1,
      profiles: (profiles || []).map(profile => ({ ...profile })),
      templates: (templates || []).map(template => ({ ...template })),
      history: cloudHistoryPayload(),
    };
  }

  async function persistCloudVault(profiles = activeProfiles, templates = activeTemplates) {
    if (activeMode !== 'cloud' || typeof cloudSaveHandler !== 'function') {
      throw new Error('Cloud Vault is not connected');
    }
    activeProfiles = profiles;
    activeTemplates = templates;
    const snapshot = cloudVaultPayload(profiles, templates);
    cloudWriteQueue = cloudWriteQueue.catch(() => {}).then(() => cloudSaveHandler(snapshot));
    await cloudWriteQueue;
  }

  function boundedString(value, label, max, required = false) {
    if (value === undefined || value === null) value = '';
    if (typeof value !== 'string') throw new Error(`Backup ${label} must be text`);
    if (required && !value.trim()) throw new Error(`Backup ${label} is required`);
    if (new TextEncoder().encode(value).byteLength > max) throw new Error(`Backup ${label} is too large`);
    return value;
  }

  function safeBackupId(value, prefix) {
    const id = boundedString(value, `${prefix} id`, 120, true);
    if (!/^[A-Za-z0-9_.:-]+$/.test(id)) throw new Error(`Backup ${prefix} id is invalid`);
    return id;
  }

  function validateBackupPayload(payload) {
    if (!payload || payload.schema_version !== BACKUP_SCHEMA_VERSION ||
        !Array.isArray(payload.profiles) || !Array.isArray(payload.templates) || !Array.isArray(payload.history)) {
      throw new Error('Backup payload is incomplete');
    }
    if (payload.profiles.length > MAX_PROFILES || payload.templates.length > MAX_TEMPLATES || payload.history.length > MAX_HISTORY_RECORDS) {
      throw new Error('Backup exceeds Browser Vault limits');
    }
    const unique = (items, label) => {
      const ids = new Set();
      for (const item of items) {
        if (ids.has(item.id)) throw new Error(`Backup contains duplicate ${label} ids`);
        ids.add(item.id);
      }
      return items;
    };
    const profiles = unique(payload.profiles.map(item => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error('Backup SMTP profile is invalid');
      const port = Number(item.port);
      if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Backup SMTP port is invalid');
      const security = boundedString(item.security || 'starttls', 'SMTP security mode', 20);
      if (!['starttls', 'ssl', 'none'].includes(security)) throw new Error('Backup SMTP security mode is invalid');
      return {
        id: safeBackupId(item.id, 'profile'),
        name: boundedString(item.name || 'SMTP Profile', 'profile name', 160),
        server: boundedString(item.server, 'SMTP server', 255, true),
        port,
        username: boundedString(item.username, 'SMTP username', 320, true),
        password: boundedString(item.password, 'SMTP password', 4096, true),
        from_name: boundedString(item.from_name || '', 'sender name', 320),
        security,
        created_at: boundedString(item.created_at || '', 'profile timestamp', 80),
      };
    }), 'profile');
    const templates = unique(payload.templates.map(item => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error('Backup template is invalid');
      const html = boundedString(item.html, 'template HTML', MAX_TEMPLATE_BYTES, true);
      if (/\{\{\s*password\s*\}\}/i.test(html)) throw new Error('Email #1 templates cannot contain {{password}}');
      return {
        id: safeBackupId(item.id, 'template'),
        name: boundedString(item.name, 'template name', 160, true),
        subject: boundedString(item.subject || '', 'template subject', 480),
        html,
        text: boundedString(item.text || '', 'template text', MAX_TEMPLATE_BYTES),
        updated_at: boundedString(item.updated_at || '', 'template timestamp', 80),
      };
    }), 'template');
    const history = unique(payload.history.map(item => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error('Backup history record is invalid');
      const mode = boundedString(item.key_delivery_mode || 'email', 'delivery mode', 10);
      if (!['email', 'oob', 'dual'].includes(mode)) throw new Error('Backup delivery mode is invalid');
      return {
        id: safeBackupId(item.id, 'history'),
        timestamp: boundedString(item.timestamp || '', 'history timestamp', 80),
        recipient: boundedString(item.recipient || 'Unknown', 'history recipient', 640),
        pdf_filename: boundedString(item.pdf_filename || 'document.pdf', 'history filename', 512),
        attachments: Array.isArray(item.attachments)
          ? item.attachments.slice(0, 10).map(name => boundedString(name, 'attachment filename', 512, true))
          : [boundedString(item.pdf_filename || 'document.pdf', 'history filename', 512)],
        subject: boundedString(item.subject || '', 'history subject', 480),
        status: boundedString(item.status || 'success', 'history status', 40),
        phone: boundedString(item.phone || '', 'history phone', 32),
        pages: boundedString(item.pages || '', 'history pages', 2048),
        key_delivery_mode: mode,
        signed: !!item.signed,
        message: boundedString(item.message || '', 'history message', 4096),
        error: boundedString(item.error || '', 'history error', 4096),
        password: boundedString(item.password || '', 'history password', 4096),
        has_password: !!item.password,
      };
    }), 'history');
    return { profiles, templates, history };
  }

  // Encrypt JSON data and commit to localStorage
  async function commitVault(profiles = activeProfiles, templates = activeTemplates) {
    if (activeMode === 'cloud') {
      await persistCloudVault(profiles, templates);
      return;
    }
    if (!activeKey) throw new Error('Vault is locked');
    const enc = new TextEncoder();
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const plaintext = enc.encode(JSON.stringify(vaultPayload(profiles, templates)));

    const ciphertext = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv: iv },
      activeKey,
      plaintext
    );

    const vaultRaw = localStorage.getItem(STORAGE_KEY);
    const vaultObj = vaultRaw ? JSON.parse(vaultRaw) : {};

    vaultObj.iv = buf2b64(iv);
    vaultObj.ciphertext = buf2b64(ciphertext);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(vaultObj));
    activeProfiles = profiles;
    activeTemplates = templates;
  }

  const Vault = {
    isConfigured() {
      if (activeMode === 'cloud') return true;
      return !!localStorage.getItem(STORAGE_KEY);
    },

    isUnlocked() {
      return activeProfiles !== null && (activeMode === 'cloud' || activeKey !== null);
    },

    isCloudMode() {
      return activeMode === 'cloud';
    },

    activateCloudVault(payload, saveHandler, wipeHandler) {
      const source = payload && typeof payload === 'object' ? payload : {};
      activeMode = 'cloud';
      activeKey = null;
      activeProfiles = Array.isArray(source.profiles) ? source.profiles.map(item => ({ ...item })) : [];
      activeTemplates = Array.isArray(source.templates) ? source.templates.map(item => ({ ...item })) : [];
      activeHistory = Array.isArray(source.history) ? source.history.map(item => {
        const copy = { ...item };
        const password = typeof copy.password === 'string' ? copy.password : '';
        delete copy.password;
        copy.encrypted_password = password ? { cloud_plaintext: password } : null;
        copy.has_password = !!password || !!copy.has_password;
        return copy;
      }) : [];
      cloudSaveHandler = saveHandler;
      cloudWipeHandler = wipeHandler;
      cloudWriteQueue = Promise.resolve();
      return true;
    },

    deactivateCloudVault() {
      activeMode = 'local';
      activeKey = null;
      activeProfiles = null;
      activeTemplates = null;
      activeHistory = null;
      cloudSaveHandler = null;
      cloudWipeHandler = null;
      cloudWriteQueue = Promise.resolve();
    },

    async tryAutoUnlock() {
      if (activeMode === 'cloud') return this.isUnlocked();
      if (this.isUnlocked()) return true;
      if (!this.isConfigured()) return false;
      try {
        if (typeof sessionStorage === 'undefined') return false;
        const savedPin = sessionStorage.getItem(SESSION_PIN_KEY);
        if (savedPin) {
          await this.unlockVault(savedPin);
          return true;
        }
      } catch {
        try { sessionStorage.removeItem(SESSION_PIN_KEY); } catch {}
      }
      return false;
    },

    async initVault(masterPin) {
      activeMode = 'local';
      if (!masterPin || masterPin.length < 4) {
        throw new Error('Master PIN must be at least 4 characters');
      }
      const salt = crypto.getRandomValues(new Uint8Array(16));
      activeKey = await deriveKey(masterPin, salt);
      activeProfiles = [];
      activeTemplates = [];

      const iv = crypto.getRandomValues(new Uint8Array(12));
      const enc = new TextEncoder();
      const ciphertext = await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv: iv },
        activeKey,
        enc.encode(JSON.stringify(vaultPayload([], [])))
      );

      const vaultObj = {
        salt: buf2b64(salt),
        iv: buf2b64(iv),
        ciphertext: buf2b64(ciphertext),
        created_at: new Date().toISOString()
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(vaultObj));
      try {
        if (typeof sessionStorage !== 'undefined') {
          sessionStorage.setItem(SESSION_PIN_KEY, masterPin);
        }
      } catch {}
      return true;
    },

    async unlockVault(masterPin) {
      activeMode = 'local';
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) throw new Error('No vault configured');
      const vaultObj = JSON.parse(raw);
      const salt = b642buf(vaultObj.salt);
      const iv = b642buf(vaultObj.iv);
      const ciphertext = b642buf(vaultObj.ciphertext);

      try {
        const key = await deriveKey(masterPin, salt);
        const decrypted = await crypto.subtle.decrypt(
          { name: 'AES-GCM', iv: new Uint8Array(iv) },
          key,
          ciphertext
        );
        const dec = new TextDecoder();
        const payload = JSON.parse(dec.decode(decrypted));
        if (Array.isArray(payload)) {
          activeProfiles = payload;
          activeTemplates = [];
        } else if (payload && Array.isArray(payload.profiles)) {
          activeProfiles = payload.profiles;
          activeTemplates = Array.isArray(payload.templates) ? payload.templates : [];
        } else {
          throw new Error('Unsupported vault schema');
        }
        activeKey = key;
        try {
          if (typeof sessionStorage !== 'undefined') {
            sessionStorage.setItem(SESSION_PIN_KEY, masterPin);
          }
        } catch {}
        return true;
      } catch (err) {
        activeKey = null;
        activeProfiles = null;
        activeTemplates = null;
        try {
          if (typeof sessionStorage !== 'undefined') {
            sessionStorage.removeItem(SESSION_PIN_KEY);
          }
        } catch {}
        throw new Error('Incorrect Master PIN');
      }
    },

    lockVault() {
      if (activeMode === 'cloud') {
        this.deactivateCloudVault();
        return;
      }
      activeKey = null;
      activeProfiles = null;
      activeTemplates = null;
      try {
        if (typeof sessionStorage !== 'undefined') {
          sessionStorage.removeItem(SESSION_PIN_KEY);
        }
      } catch {}
    },

    wipeAll() {
      if (activeMode === 'cloud') {
        throw new Error('Use wipeCloudVault() for a hosted vault');
      }
      localStorage.removeItem(STORAGE_KEY);
      activeKey = null;
      activeProfiles = null;
      activeTemplates = null;
      try {
        if (typeof sessionStorage !== 'undefined') {
          sessionStorage.removeItem(SESSION_PIN_KEY);
        }
      } catch {}
    },

    async wipeCloudVault() {
      if (activeMode !== 'cloud' || typeof cloudWipeHandler !== 'function') {
        throw new Error('Cloud Vault is not connected');
      }
      await cloudWipeHandler();
      activeProfiles = [];
      activeTemplates = [];
      activeHistory = [];
      return true;
    },

    getProfilesCount() {
      if (!activeProfiles) return 0;
      return activeProfiles.length;
    },

    // Returns masked profiles for display in the UI (passwords never exposed)
    getMaskedProfiles() {
      if (!activeProfiles) return [];
      return activeProfiles.map(p => ({
        id: p.id,
        name: p.name || 'SMTP Profile',
        server: p.server,
        port: p.port,
        username: p.username,
        password: '••••••••',
        from_name: p.from_name || '',
        security: p.security || 'starttls',
        created_at: p.created_at
      }));
    },

    // Get raw profile (with password) for dispatching an email
    getProfileForDispatch(profileId) {
      if (!activeProfiles) throw new Error('Vault is locked');
      const p = activeProfiles.find(x => x.id === profileId);
      if (!p) throw new Error('Profile not found');
      return { ...p };
    },

    async addProfile(profileData) {
      if (!activeProfiles) throw new Error('Vault is locked');
      if (activeProfiles.length >= MAX_PROFILES) {
        throw new Error(`Maximum limit of ${MAX_PROFILES} profiles reached.`);
      }

      if (!profileData.server || !profileData.port || !profileData.username || !profileData.password) {
        throw new Error('Server, port, username, and password are required.');
      }

      const id = 'prof_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      const newProf = {
        id: id,
        name: profileData.name?.trim() || `${profileData.username}`,
        server: profileData.server.trim(),
        port: parseInt(profileData.port, 10) || 587,
        username: profileData.username.trim(),
        password: profileData.password, // Stored encrypted inside the blob
        from_name: profileData.from_name?.trim() || '',
        security: profileData.security || (parseInt(profileData.port, 10) === 465 ? 'ssl' : 'starttls'),
        created_at: new Date().toISOString()
      };

      const updated = [...activeProfiles, newProf];
      await commitVault(updated);
      return newProf.id;
    },

    // Update ONLY non-secret metadata (from_name, nickname)
    async updateProfileMeta(profileId, { name, from_name }) {
      if (!activeProfiles) throw new Error('Vault is locked');
      const idx = activeProfiles.findIndex(p => p.id === profileId);
      if (idx === -1) throw new Error('Profile not found');

      if (name !== undefined) activeProfiles[idx].name = name.trim();
      if (from_name !== undefined) activeProfiles[idx].from_name = from_name.trim();

      await commitVault(activeProfiles);
      return true;
    },

    // Replace password explicitly
    async replacePassword(profileId, newPassword) {
      if (!activeProfiles) throw new Error('Vault is locked');
      if (!newPassword) throw new Error('Password cannot be empty');
      const idx = activeProfiles.findIndex(p => p.id === profileId);
      if (idx === -1) throw new Error('Profile not found');

      activeProfiles[idx].password = newPassword;
      await commitVault(activeProfiles);
      return true;
    },

    async deleteProfile(profileId) {
      if (!activeProfiles) throw new Error('Vault is locked');
      const updated = activeProfiles.filter(p => p.id !== profileId);
      await commitVault(updated);
      return true;
    },

    async lintDomainDeliverability(smtpConfig) {
      if (!smtpConfig) return { ok: false, error: 'No SMTP configuration provided' };
      const sender = String(smtpConfig.username || smtpConfig.sender || '').trim();
      const domain = sender.includes('@') ? sender.split('@')[1] : String(smtpConfig.server || smtpConfig.host || '').trim();
      if (!domain || !domain.includes('.')) {
        return { ok: false, error: 'Invalid domain name for DNS lookup' };
      }
      try {
        const dohBase = 'https://cloudflare-dns.com/dns-query';
        const spfRes = await fetch(`${dohBase}?name=${encodeURIComponent(domain)}&type=TXT`, {
          headers: { 'Accept': 'application/dns-json' },
          cache: 'no-store'
        });
        const spfData = spfRes.ok ? await spfRes.json() : null;
        const txtAnswers = (spfData?.Answer || []).filter(a => a.type === 16).map(a => a.data ? a.data.replace(/^"|"$/g, '') : '');
        const spfRecord = txtAnswers.find(t => t.startsWith('v=spf1')) || null;
        
        const dmarcRes = await fetch(`${dohBase}?name=${encodeURIComponent('_dmarc.' + domain)}&type=TXT`, {
          headers: { 'Accept': 'application/dns-json' },
          cache: 'no-store'
        });
        const dmarcData = dmarcRes.ok ? await dmarcRes.json() : null;
        const dmarcAnswers = (dmarcData?.Answer || []).filter(a => a.type === 16).map(a => a.data ? a.data.replace(/^"|"$/g, '') : '');
        const dmarcRecord = dmarcAnswers.find(t => t.startsWith('v=DMARC1')) || null;
        
        let dmarcPolicy = 'none';
        if (dmarcRecord) {
          const pm = dmarcRecord.match(/p=([a-zA-Z]+)/);
          if (pm) dmarcPolicy = pm[1].toLowerCase();
        }

        const relayHost = String(smtpConfig.server || smtpConfig.host || '').toLowerCase();
        let spfAligned = false;
        if (spfRecord) {
          const lowerSpf = spfRecord.toLowerCase();
          if (relayHost.includes('google') || relayHost.includes('gmail')) {
            spfAligned = lowerSpf.includes('_spf.google.com') || lowerSpf.includes('include:_spf');
          } else if (relayHost.includes('sendgrid')) {
            spfAligned = lowerSpf.includes('sendgrid.net');
          } else if (relayHost.includes('mailgun')) {
            spfAligned = lowerSpf.includes('mailgun.org');
          } else {
            spfAligned = lowerSpf.includes(domain) || lowerSpf.includes('mx') || lowerSpf.includes('a') || lowerSpf.includes('include:');
          }
        }

        const warnings = [];
        if (!spfRecord) warnings.push(`No SPF (v=spf1) record found for ${domain}`);
        if (dmarcPolicy === 'reject' && !spfAligned) {
          warnings.push(`Strict DMARC policy (p=reject) with unaligned SPF may cause emails sent via ${relayHost} to bounce.`);
        } else if (dmarcPolicy === 'quarantine' && !spfAligned) {
          warnings.push(`DMARC policy (p=quarantine) may route emails to spam.`);
        }

        return {
          ok: true,
          domain,
          spf_record: spfRecord,
          spf_aligned: spfAligned,
          dmarc_policy: dmarcPolicy,
          has_dmarc: !!dmarcRecord,
          warnings
        };
      } catch (err) {
        return { ok: false, error: err.message || 'DNS-over-HTTPS request failed', offline: true };
      }
    },

    getTemplates() {
      if (!activeTemplates) return [];
      return activeTemplates.map(t => ({ ...t }));
    },

    async saveTemplate(templateData) {
      if (!activeTemplates) throw new Error('Vault is locked');
      const name = String(templateData?.name || '').trim();
      const html = String(templateData?.html || '');
      const text = String(templateData?.text || '');
      const subject = String(templateData?.subject || '');
      if (!name || !html) throw new Error('Template name and HTML are required');
      if (/\{\{\s*password\s*\}\}/i.test(html)) {
        throw new Error('Email #1 templates cannot contain {{password}}');
      }
      if (new TextEncoder().encode(html).byteLength > MAX_TEMPLATE_BYTES) {
        throw new Error('Template exceeds the 100 KB limit');
      }
      const existingId = String(templateData?.id || '');
      const id = existingId || ('tmpl_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6));
      const entry = { id, name: name.slice(0, 80), subject: subject.slice(0, 240), html, text, updated_at: new Date().toISOString() };
      const index = activeTemplates.findIndex(t => t.id === id);
      const updated = [...activeTemplates];
      if (index >= 0) updated[index] = entry;
      else {
        if (updated.length >= MAX_TEMPLATES) throw new Error(`Maximum limit of ${MAX_TEMPLATES} templates reached`);
        updated.push(entry);
      }
      await commitVault(activeProfiles, updated);
      return id;
    },

    async deleteTemplate(templateId) {
      if (!activeTemplates) throw new Error('Vault is locked');
      await commitVault(activeProfiles, activeTemplates.filter(t => t.id !== templateId));
      return true;
    },

    async exportBackup(passphrase) {
      if (!this.isUnlocked()) throw new Error('Vault must be unlocked before export');
      if (!passphrase || passphrase.length < 12) throw new Error('Backup passphrase must be at least 12 characters');
      const history = [];
      for (const record of this.getDispatchHistory()) {
        const copy = { ...record };
        if (record.encrypted_password) {
          try { copy.password = await this.decryptSecret(record.encrypted_password); } catch {}
        }
        delete copy.encrypted_password;
        history.push(copy);
      }
      const payload = {
        schema_version: BACKUP_SCHEMA_VERSION,
        exported_at: new Date().toISOString(),
        profiles: activeProfiles.map(p => ({ ...p })),
        templates: activeTemplates.map(t => ({ ...t })),
        history,
      };
      const salt = crypto.getRandomValues(new Uint8Array(16));
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const key = await deriveKeyWithIterations(passphrase, salt, BACKUP_PBKDF2_ITERATIONS);
      const ciphertext = await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv }, key,
        new TextEncoder().encode(JSON.stringify(payload))
      );
      return JSON.stringify({
        magic: BACKUP_MAGIC,
        schema_version: BACKUP_SCHEMA_VERSION,
        cipher: 'AES-256-GCM',
        kdf: { name: 'PBKDF2-SHA256', iterations: BACKUP_PBKDF2_ITERATIONS, salt: buf2b64(salt) },
        iv: buf2b64(iv),
        ciphertext: buf2b64(ciphertext),
      }, null, 2);
    },

    async importBackup(serialized, passphrase, mode = 'merge') {
      if (!this.isUnlocked()) throw new Error('Unlock the vault before import');
      if (!passphrase || passphrase.length < 12) throw new Error('Backup passphrase must be at least 12 characters');
      if (!['merge', 'replace'].includes(mode)) throw new Error('Import mode must be merge or replace');
      if (String(serialized || '').length > 5 * 1024 * 1024) throw new Error('Backup file exceeds the 5 MB limit');
      let envelope;
      try { envelope = JSON.parse(serialized); } catch { throw new Error('Backup is not valid JSON'); }
      if (envelope?.magic !== BACKUP_MAGIC || envelope?.schema_version !== BACKUP_SCHEMA_VERSION) {
        throw new Error('Unsupported .squishvault format');
      }
      if (envelope?.kdf?.name !== 'PBKDF2-SHA256' || envelope?.kdf?.iterations !== BACKUP_PBKDF2_ITERATIONS) {
        throw new Error('Unsupported backup key derivation settings');
      }
      let payload;
      try {
        const key = await deriveKeyWithIterations(
          passphrase, new Uint8Array(b642buf(envelope.kdf.salt)), envelope.kdf.iterations);
        const plaintext = await crypto.subtle.decrypt(
          { name: 'AES-GCM', iv: new Uint8Array(b642buf(envelope.iv)) },
          key, b642buf(envelope.ciphertext));
        payload = JSON.parse(new TextDecoder().decode(plaintext));
      } catch {
        throw new Error('Incorrect backup passphrase or tampered backup');
      }
      const validated = validateBackupPayload(payload);
      const mergeById = (current, incoming, limit) => {
        const result = new Map(current.map(item => [item.id, { ...item }]));
        incoming.forEach(item => result.set(item.id, { ...item }));
        return [...result.values()].slice(0, limit);
      };
      const nextProfiles = mode === 'replace' ? validated.profiles : mergeById(activeProfiles, validated.profiles, MAX_PROFILES);
      const nextTemplates = mode === 'replace' ? validated.templates : mergeById(activeTemplates, validated.templates, MAX_TEMPLATES);
      const importedHistory = [];
      for (const record of validated.history) {
        const copy = { ...record };
        delete copy.password;
        copy.encrypted_password = record.password ? await this.encryptSecret(String(record.password)) : null;
        importedHistory.push(copy);
      }
      const finalHistory = mode === 'replace'
        ? importedHistory
        : mergeById(this.getDispatchHistory(), importedHistory, MAX_HISTORY_RECORDS);
      const priorVault = localStorage.getItem(STORAGE_KEY);
      const priorHistory = localStorage.getItem(HISTORY_STORAGE_KEY);
      const priorProfiles = activeProfiles;
      const priorTemplates = activeTemplates;
      const priorActiveHistory = activeHistory;
      try {
        if (activeMode === 'cloud') {
          activeHistory = finalHistory.slice(0, MAX_HISTORY_RECORDS);
          await commitVault(nextProfiles, nextTemplates);
        } else {
          await commitVault(nextProfiles, nextTemplates);
          localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(finalHistory.slice(0, MAX_HISTORY_RECORDS)));
        }
      } catch (err) {
        if (activeMode !== 'cloud') {
          if (priorVault === null) localStorage.removeItem(STORAGE_KEY); else localStorage.setItem(STORAGE_KEY, priorVault);
          if (priorHistory === null) localStorage.removeItem(HISTORY_STORAGE_KEY); else localStorage.setItem(HISTORY_STORAGE_KEY, priorHistory);
        }
        activeProfiles = priorProfiles;
        activeTemplates = priorTemplates;
        activeHistory = priorActiveHistory;
        throw new Error(`Backup import could not be committed: ${err.message || err}`);
      }
      return { profiles: nextProfiles.length, templates: nextTemplates.length, history: finalHistory.length };
    },

    /* ------------------------------------------- Dispatch History & Password Security --- */
    async encryptSecret(plaintext) {
      if (activeMode === 'cloud') return { cloud_plaintext: String(plaintext) };
      if (!activeKey) return null;
      const enc = new TextEncoder();
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const ciphertext = await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv: iv },
        activeKey,
        enc.encode(plaintext)
      );
      return {
        iv: buf2b64(iv),
        ciphertext: buf2b64(ciphertext)
      };
    },

    async decryptSecret(encryptedObj) {
      if (activeMode === 'cloud' && typeof encryptedObj?.cloud_plaintext === 'string') {
        return encryptedObj.cloud_plaintext;
      }
      if (!activeKey || !encryptedObj || !encryptedObj.iv || !encryptedObj.ciphertext) {
        throw new Error('Vault is locked. Unlock vault to view password.');
      }
      const iv = b642buf(encryptedObj.iv);
      const ciphertext = b642buf(encryptedObj.ciphertext);
      const decrypted = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: new Uint8Array(iv) },
        activeKey,
        ciphertext
      );
      const dec = new TextDecoder();
      return dec.decode(decrypted);
    },

    getDispatchHistory() {
      if (activeMode === 'cloud') return (activeHistory || []).map(record => ({ ...record }));
      try {
        const raw = localStorage.getItem(HISTORY_STORAGE_KEY);
        return raw ? JSON.parse(raw) : [];
      } catch {
        return [];
      }
    },

    async addDispatchRecord({ recipient, pdf_filename, attachments, subject, status, password, message, error, phone, pages, key_delivery_mode, signed }) {
      const history = this.getDispatchHistory();
      let encryptedPassword = null;
      if (password && this.isUnlocked()) {
        try {
          encryptedPassword = await this.encryptSecret(password);
        } catch {}
      }

      const record = {
        id: 'disp_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        timestamp: new Date().toISOString(),
        recipient: recipient || 'Unknown',
        pdf_filename: pdf_filename || 'document.pdf',
        attachments: Array.isArray(attachments) ? attachments.slice(0, 10) : [pdf_filename || 'document.pdf'],
        subject: subject || 'Secure Document',
        status: status || 'success',
        phone: phone || '',
        pages: pages || '',
        key_delivery_mode: key_delivery_mode || 'email',
        signed: !!signed,
        message: message || '',
        error: error || '',
        encrypted_password: encryptedPassword,
        has_password: !!password
      };

      const updated = [record, ...history].slice(0, MAX_HISTORY_RECORDS);
      if (activeMode === 'cloud') {
        activeHistory = updated;
        await persistCloudVault();
        return record;
      }
      try {
        localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(updated));
      } catch {}
      return record;
    },

    async revealDispatchPassword(recordId) {
      if (!this.isUnlocked()) {
        throw new Error('Vault is locked. Enter Master PIN to view password.');
      }
      const history = this.getDispatchHistory();
      const rec = history.find(r => r.id === recordId);
      if (!rec) throw new Error('Record not found');
      if (!rec.encrypted_password) throw new Error('No encrypted password recorded for this dispatch');
      return await this.decryptSecret(rec.encrypted_password);
    },

    async clearDispatchHistory() {
      if (activeMode === 'cloud') {
        activeHistory = [];
        await persistCloudVault();
        return true;
      }
      localStorage.removeItem(HISTORY_STORAGE_KEY);
      return true;
    }
  };

  if (typeof window !== 'undefined') window.SquishVault = Vault;
  if (typeof globalThis !== 'undefined') globalThis.SquishVault = Vault;
  if (typeof module !== 'undefined' && module.exports) module.exports = Vault;
})(typeof globalThis !== 'undefined' ? globalThis : (typeof window !== 'undefined' ? window : this));
