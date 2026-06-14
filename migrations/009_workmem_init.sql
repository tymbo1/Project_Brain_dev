-- 009_workmem_init.sql
-- Phase 2.0.2 — selyrion_workmem.db initial schema.
-- Locked decisions:
--   Q-2.0.G working sets live in this DB only (never resonance_v11.db).
--   Q-2.0.H TTL is a correctness boundary; expired reads fail closed.
--   Q-2.0.I parent_set is a strict tree (single parent, no cycles).
-- This migration only creates schema; no rows inserted.

BEGIN;

CREATE TABLE IF NOT EXISTS working_sets (
    id           TEXT PRIMARY KEY,
    purpose      TEXT NOT NULL,
    query        TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    status       TEXT NOT NULL
                  CHECK (status IN ('open','sealed','expired','deleted')),
    expires_at   INTEGER NOT NULL,
    parent_set   TEXT
                  REFERENCES working_sets(id) ON DELETE RESTRICT,
    created_at   INTEGER NOT NULL,
    CHECK (parent_set IS NULL OR parent_set <> id)
);

CREATE INDEX IF NOT EXISTS idx_ws_status     ON working_sets (status);
CREATE INDEX IF NOT EXISTS idx_ws_expires_at ON working_sets (expires_at);
CREATE INDEX IF NOT EXISTS idx_ws_parent     ON working_sets (parent_set);

CREATE TABLE IF NOT EXISTS working_set_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    working_set_id  TEXT NOT NULL
                     REFERENCES working_sets(id) ON DELETE CASCADE,
    item_type       TEXT NOT NULL
                     CHECK (item_type IN
                            ('anchor','relation','hypothesis','constraint','binding')),
    item_ref        TEXT NOT NULL,
    local_score     REAL,
    state           TEXT NOT NULL DEFAULT 'candidate'
                     CHECK (state IN
                            ('candidate','active','rejected','committed')),
    provenance      TEXT
);

CREATE INDEX IF NOT EXISTS idx_wsi_ws    ON working_set_items (working_set_id);
CREATE INDEX IF NOT EXISTS idx_wsi_type  ON working_set_items (item_type);
CREATE INDEX IF NOT EXISTS idx_wsi_state ON working_set_items (state);

CREATE TABLE IF NOT EXISTS working_set_edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    working_set_id  TEXT NOT NULL
                     REFERENCES working_sets(id) ON DELETE CASCADE,
    subject_id      INTEGER NOT NULL,
    predicate       TEXT NOT NULL,
    object_id       INTEGER NOT NULL,
    local_truth     TEXT,
    local_confidence REAL,
    provenance      TEXT
);

CREATE INDEX IF NOT EXISTS idx_wse_ws    ON working_set_edges (working_set_id);
CREATE INDEX IF NOT EXISTS idx_wse_subj  ON working_set_edges (subject_id);
CREATE INDEX IF NOT EXISTS idx_wse_obj   ON working_set_edges (object_id);
CREATE INDEX IF NOT EXISTS idx_wse_pred  ON working_set_edges (predicate);

-- meta table for migration tracking
CREATE TABLE IF NOT EXISTS workmem_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);

INSERT OR REPLACE INTO workmem_meta (key, value, updated_at)
VALUES ('schema_version', '009', strftime('%s','now')),
       ('purpose', 'phase_2_0_2_scratch_space', strftime('%s','now')),
       ('substrate_writes_allowed', '0', strftime('%s','now'));

COMMIT;
