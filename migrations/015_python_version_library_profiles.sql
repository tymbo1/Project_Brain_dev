-- 015_python_version_library_profiles.sql
-- Phase C1 — Selyrion Python adapter: version + library profile tables.
-- Target DB: ~/selyrioncode.db (lives next to codeunits / fix_pairs / fix_templates).
-- Schema only. No rows inserted. Idempotent.
-- Source: selyrion_python_expertise_schema.md §6.1 (version), §6.2 (library).
-- Closes GAP markers in SELYRION_PYTHON_ADAPTER.md §16.

BEGIN;

-- ── python_version_profiles (§6.1) ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS python_version_profiles (
    version              TEXT PRIMARY KEY,         -- e.g. "3.10", "3.11", "3.12"
    syntax_features      TEXT,                     -- JSON list
    typing_features      TEXT,                     -- JSON list
    stdlib_additions     TEXT,                     -- JSON list
    deprecated_features  TEXT,                     -- JSON list
    removed_features     TEXT,                     -- JSON list
    migration_notes      TEXT,                     -- JSON list
    trust_score          REAL NOT NULL DEFAULT 0.0
                           CHECK (trust_score BETWEEN 0.0 AND 1.0),
    provenance_refs      TEXT,                     -- JSON list
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_py_ver_trust
    ON python_version_profiles (trust_score);

-- ── python_library_profiles (§6.2) ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS python_library_profiles (
    library_id                  TEXT PRIMARY KEY,  -- "py_lib::<name>"
    name                        TEXT NOT NULL,
    versions_known              TEXT,              -- JSON list
    domains                     TEXT,              -- JSON list
    major_objects               TEXT,              -- JSON list
    breaking_changes            TEXT,              -- JSON list
    python_min                  TEXT,
    python_max                  TEXT,
    common_errors               TEXT,              -- JSON list
    docs_refs                   TEXT,              -- JSON list
    freshness_required          INTEGER NOT NULL DEFAULT 0
                                  CHECK (freshness_required IN (0,1)),
    answer_from_memory_allowed  INTEGER NOT NULL DEFAULT 1
                                  CHECK (answer_from_memory_allowed IN (0,1)),
    trust_score                 REAL NOT NULL DEFAULT 0.0
                                  CHECK (trust_score BETWEEN 0.0 AND 1.0),
    created_at                  REAL NOT NULL,
    updated_at                  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_py_lib_name
    ON python_library_profiles (name);
CREATE INDEX IF NOT EXISTS idx_py_lib_freshness
    ON python_library_profiles (freshness_required);
CREATE INDEX IF NOT EXISTS idx_py_lib_trust
    ON python_library_profiles (trust_score);

COMMIT;
