-- Hosted Squish identity metadata and encrypted vault envelopes.
-- SMTP passwords, templates, and dispatch secrets are stored only inside the
-- AES-256-GCM ciphertext column. Google subject IDs are opaque stable IDs.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS oauth_users (
  google_sub TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  picture_url TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_vaults (
  google_sub TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL CHECK (schema_version = 1),
  iv TEXT NOT NULL,
  ciphertext TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (google_sub) REFERENCES oauth_users (google_sub) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_oauth_users_email ON oauth_users (email);

