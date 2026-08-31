-- Keep the key version beside each envelope so SESSION_SECRET and Cloud Vault
-- keys can rotate independently. Existing rows remain version 1 and are
-- re-wrapped after a successful read when CLOUD_VAULT_KEY_CURRENT advances.

ALTER TABLE oauth_vaults ADD COLUMN key_version INTEGER NOT NULL DEFAULT 1;

