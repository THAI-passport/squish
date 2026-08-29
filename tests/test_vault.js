/**
 * Unit tests for Web Crypto Vault (Pattern A)
 * Runs under Node.js 18+ with native Web Crypto and localStorage mock
 */

const assert = require('assert');

// Mock localStorage
const storage = {};
global.localStorage = {
  getItem(k) { return storage[k] || null; },
  setItem(k, v) { storage[k] = String(v); },
  removeItem(k) { delete storage[k]; },
  clear() { for(let k in storage) delete storage[k]; }
};

// Import vault
require('../backend/static/vault.js');
const Vault = global.SquishVault;

async function runTests() {
  console.log('Running Web Crypto Vault tests...');

  // 1. Initial state
  assert.strictEqual(Vault.isConfigured(), false, 'Vault should not be configured initially');
  assert.strictEqual(Vault.isUnlocked(), false, 'Vault should not be unlocked initially');

  // 2. Initialize with PIN
  await Vault.initVault('4829');
  assert.strictEqual(Vault.isConfigured(), true, 'Vault is now configured');
  assert.strictEqual(Vault.isUnlocked(), true, 'Vault is unlocked after init');
  assert.strictEqual(Vault.getProfilesCount(), 0, 'Profiles count should be 0');

  // 3. Add profile
  const id1 = await Vault.addProfile({
    name: 'Work O365',
    server: 'smtp.office365.com',
    port: 587,
    username: 'alice@company.com',
    password: 'SuperSecretPassword123!',
    from_name: 'Alice Finance',
    security: 'starttls'
  });
  assert.ok(id1.startsWith('prof_'), 'Valid profile ID generated');
  assert.strictEqual(Vault.getProfilesCount(), 1, 'Profiles count is now 1');

  // 4. Verify Masked Profiles (Never returns raw password)
  const masked = Vault.getMaskedProfiles();
  assert.strictEqual(masked.length, 1);
  assert.strictEqual(masked[0].name, 'Work O365');
  assert.strictEqual(masked[0].server, 'smtp.office365.com');
  assert.strictEqual(masked[0].username, 'alice@company.com');
  assert.strictEqual(masked[0].password, '••••••••', 'Password MUST be masked');
  assert.strictEqual(masked[0].from_name, 'Alice Finance');

  // 5. Verify Dispatch Profile (Retrieves password internally for transmission)
  const raw = Vault.getProfileForDispatch(id1);
  assert.strictEqual(raw.password, 'SuperSecretPassword123!', 'Raw password available for dispatch');

  // 6. Update ONLY editable metadata (MAIL_FROM_NAME & name)
  await Vault.updateProfileMeta(id1, { from_name: 'Alice Accounts Receivable' });
  const updatedMasked = Vault.getMaskedProfiles();
  assert.strictEqual(updatedMasked[0].from_name, 'Alice Accounts Receivable');

  // 7. Lock and Unlock with correct vs incorrect PIN
  Vault.lockVault();
  assert.strictEqual(Vault.isUnlocked(), false, 'Vault is locked');
  assert.strictEqual(Vault.getProfilesCount(), 0, 'Returns 0 when vault is locked');

  // Incorrect PIN fails
  let failed = false;
  try {
    await Vault.unlockVault('9999');
  } catch (err) {
    failed = true;
    assert.strictEqual(err.message, 'Incorrect Master PIN');
  }
  assert.strictEqual(failed, true, 'Incorrect PIN rejected');

  // Correct PIN succeeds
  await Vault.unlockVault('4829');
  assert.strictEqual(Vault.isUnlocked(), true, 'Vault unlocked with correct PIN');
  assert.strictEqual(Vault.getProfilesCount(), 1, 'Restored profiles count');
  assert.strictEqual(Vault.getMaskedProfiles()[0].from_name, 'Alice Accounts Receivable');

  // 8. Max 5 profiles limit
  for (let i = 2; i <= 5; i++) {
    await Vault.addProfile({
      name: `Profile ${i}`,
      server: `smtp${i}.example.com`,
      port: 587,
      username: `user${i}@example.com`,
      password: `pass${i}`
    });
  }
  assert.strictEqual(Vault.getProfilesCount(), 5);

  let maxExceeded = false;
  try {
    await Vault.addProfile({
      name: 'Profile 6',
      server: 'smtp6.example.com',
      port: 587,
      username: 'user6@example.com',
      password: 'pass6'
    });
  } catch (err) {
    maxExceeded = true;
    assert.ok(err.message.includes('Maximum limit of 5 profiles reached'));
  }
  assert.strictEqual(maxExceeded, true, 'Exceeding 5 profiles blocked');

  // 9. Delete Profile
  await Vault.deleteProfile(id1);
  assert.strictEqual(Vault.getProfilesCount(), 4);

  // 10. Dispatch History & Encrypted Password Reveal
  const rec1 = await Vault.addDispatchRecord({
    recipient: 'client@example.com',
    pdf_filename: 'Invoice_101_protected.pdf',
    subject: 'Quarterly Invoice',
    status: 'success',
    password: 'ClientSecretPassword789!'
  });
  assert.ok(rec1.id.startsWith('disp_'));
  assert.strictEqual(rec1.recipient, 'client@example.com');
  assert.ok(rec1.encrypted_password !== null, 'Password is encrypted');
  assert.notStrictEqual(rec1.encrypted_password.ciphertext, 'ClientSecretPassword789!', 'Password never stored in plaintext');

  // Reveal password when unlocked
  const revealed = await Vault.revealDispatchPassword(rec1.id);
  assert.strictEqual(revealed, 'ClientSecretPassword789!', 'Password decrypted accurately');

  // Lock vault and verify password cannot be revealed without PIN
  Vault.lockVault();
  assert.strictEqual(Vault.isUnlocked(), false);
  let revealBlocked = false;
  try {
    await Vault.revealDispatchPassword(rec1.id);
  } catch (err) {
    revealBlocked = true;
    assert.ok(err.message.includes('locked') || err.message.includes('Master PIN'));
  }
  assert.strictEqual(revealBlocked, true, 'Locked vault blocks password decryption');

  // History list is still readable in plain for metadata
  const hist = Vault.getDispatchHistory();
  assert.strictEqual(hist.length, 1);
  assert.strictEqual(hist[0].recipient, 'client@example.com');
  assert.strictEqual(hist[0].pdf_filename, 'Invoice_101_protected.pdf');

  // 11. Wipe All
  Vault.wipeAll();
  assert.strictEqual(Vault.isConfigured(), false, 'Vault wiped from storage');
  assert.strictEqual(Vault.isUnlocked(), false, 'Vault cleared from RAM');
  assert.strictEqual(global.localStorage.getItem('squish_smtp_vault'), null, 'localStorage clean');

  console.log('✓ All Web Crypto Vault tests passed successfully!');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
