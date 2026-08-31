import assert from 'node:assert/strict';
import {
  decryptCloudVault,
  encryptCloudVault,
} from '../backend/static/squish-auth.mjs';

const subject = 'google-subject-1';
const legacySecret = 's'.repeat(48);
const legacyEnv = {SESSION_SECRET: legacySecret};

const legacyEnvelope = await encryptCloudVault({
  history: [{id: 'history-1', password: 'must-not-survive'}],
}, legacyEnv, subject);

assert.equal(legacyEnvelope.key_version, 1);
const safeVault = await decryptCloudVault(legacyEnvelope, legacyEnv, subject);
assert.equal(safeVault.history[0].password, undefined);
assert.equal(safeVault.history[0].has_password, true);

const rotatedEnv = {
  SESSION_SECRET: legacySecret,
  CLOUD_VAULT_KEY_CURRENT: '2',
  CLOUD_VAULT_KEY_V1: legacySecret,
  CLOUD_VAULT_KEY_V2: 'n'.repeat(48),
};

// A rotation must keep old envelopes readable while writing new versioned
// envelopes with the current key.
assert.deepEqual(await decryptCloudVault(legacyEnvelope, rotatedEnv, subject), safeVault);
const rotatedEnvelope = await encryptCloudVault(safeVault, rotatedEnv, subject);
assert.equal(rotatedEnvelope.key_version, 2);
assert.deepEqual(await decryptCloudVault(rotatedEnvelope, rotatedEnv, subject), safeVault);

console.log('All Cloud Vault auth tests passed successfully!');
