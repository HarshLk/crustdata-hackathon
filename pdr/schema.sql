-- State database (INTERFACES 6). Every statement is idempotent; applied on every open.
CREATE TABLE IF NOT EXISTS entities   (kind TEXT, id TEXT, client_id TEXT, k1 TEXT, k2 TEXT, body TEXT NOT NULL, created_at TEXT, PRIMARY KEY(kind,id));
CREATE INDEX IF NOT EXISTS entities_k ON entities(kind, client_id, k1, k2);
CREATE INDEX IF NOT EXISTS entities_k2 ON entities(kind, k2);
CREATE TABLE IF NOT EXISTS stage_results (dispute_key TEXT, stage TEXT, inputs_hash TEXT, results TEXT NOT NULL, settings_version TEXT, saved_at TEXT, PRIMARY KEY(dispute_key, stage, inputs_hash));
CREATE TABLE IF NOT EXISTS snapshots     (content_hash TEXT PRIMARY KEY, url TEXT, text TEXT, html_path TEXT, stored_at TEXT);
CREATE TABLE IF NOT EXISTS schema_maps   (header_hash TEXT PRIMARY KEY, body TEXT NOT NULL, confirmed_at TEXT);
CREATE TABLE IF NOT EXISTS audit_trail   (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, event TEXT, actor TEXT, subject_id TEXT, detail TEXT, prev_hash TEXT, row_hash TEXT);
CREATE TABLE IF NOT EXISTS call_intents  (cache_key TEXT PRIMARY KEY, provider TEXT, endpoint TEXT, request TEXT, status TEXT, run_id TEXT, dispute_key TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS call_cache    (cache_key TEXT PRIMARY KEY, status INTEGER, headers TEXT, body TEXT, stored_at TEXT);
CREATE TABLE IF NOT EXISTS ledger        (entry_id TEXT PRIMARY KEY, run_id TEXT, dispute_key TEXT, cache_key TEXT, body TEXT NOT NULL, cost_inr TEXT, cache_hit INTEGER, started_at TEXT);
CREATE TABLE IF NOT EXISTS throttle      (provider TEXT, at REAL);
CREATE TABLE IF NOT EXISTS runs          (run_id TEXT PRIMARY KEY, settings_version TEXT, started_at TEXT, ended_at TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS settings_versions (settings_version TEXT PRIMARY KEY, body TEXT, loaded_at TEXT);
CREATE TABLE IF NOT EXISTS pii_access_log (id INTEGER PRIMARY KEY, at TEXT, who TEXT, trip_id TEXT, purpose TEXT);
CREATE TABLE IF NOT EXISTS alert_outbox  (alert_key TEXT PRIMARY KEY, dispute_key TEXT, event TEXT, version TEXT, roles TEXT, message TEXT, status TEXT, attempts INTEGER DEFAULT 0, next_attempt_at TEXT, last_error TEXT, created_at TEXT, updated_at TEXT);
