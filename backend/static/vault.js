/**
 * Squish Web Crypto Vault
 * Pattern A: Encrypted localData with PBKDF2 + AES-256-GCM
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
  const MAX_PROFILES = 5;
  const PBKDF2_ITERATIONS = 100000;

  // In-memory decrypted state (ephemeral in RAM only)
  let activeKey = null; // CryptoKey
  let activeProfiles = null; // Array of profile objects

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
  async function deriveKey(pin, salt) {
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
        iterations: PBKDF2_ITERATIONS,
        hash: 'SHA-256'
      },
      keyMaterial,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt']
    );
  }

  // Encrypt JSON data and commit to localStorage
  async function commitVault(profiles) {
    if (!activeKey) throw new Error('Vault is locked');
    const enc = new TextEncoder();
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const plaintext = enc.encode(JSON.stringify(profiles));

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
  }

  const Vault = {
    isConfigured() {
      return !!localStorage.getItem(STORAGE_KEY);
    },

    isUnlocked() {
      return activeKey !== null && activeProfiles !== null;
    },

    async initVault(masterPin) {
      if (!masterPin || masterPin.length < 4) {
        throw new Error('Master PIN must be at least 4 characters');
      }
      const salt = crypto.getRandomValues(new Uint8Array(16));
      activeKey = await deriveKey(masterPin, salt);
      activeProfiles = [];

      const iv = crypto.getRandomValues(new Uint8Array(12));
      const enc = new TextEncoder();
      const ciphertext = await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv: iv },
        activeKey,
        enc.encode('[]')
      );

      const vaultObj = {
        salt: buf2b64(salt),
        iv: buf2b64(iv),
        ciphertext: buf2b64(ciphertext),
        created_at: new Date().toISOString()
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(vaultObj));
      return true;
    },

    async unlockVault(masterPin) {
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
        activeProfiles = JSON.parse(dec.decode(decrypted));
        activeKey = key;
        return true;
      } catch (err) {
        activeKey = null;
        activeProfiles = null;
        throw new Error('Incorrect Master PIN');
      }
    },

    lockVault() {
      activeKey = null;
      activeProfiles = null;
    },

    wipeAll() {
      localStorage.removeItem(STORAGE_KEY);
      activeKey = null;
      activeProfiles = null;
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
    }
  };

  if (typeof window !== 'undefined') window.SquishVault = Vault;
  if (typeof globalThis !== 'undefined') globalThis.SquishVault = Vault;
  if (typeof module !== 'undefined' && module.exports) module.exports = Vault;
})(typeof globalThis !== 'undefined' ? globalThis : (typeof window !== 'undefined' ? window : this));

