-- 016_execution_traces_verification_bundle.sql
-- Phase C2 — Selyrion Python adapter: verification bundle columns on execution_traces.
-- Target DB: ~/claudecode.db.
-- Source: selyrion_python_expertise_schema.md §9 (python_verification_bundle).
-- Closes adapter §16 "partial; Phase C2 extends" note.
-- 14 nullable columns + 1 index. Existing rows keep NULL on new columns.
-- Idempotent: ALTER guards live in 016_apply.py (SQLite ALTER limitation).
-- CHECK constraints NOT applied — verdict enum enforced in write path.

BEGIN;

-- ── static checks (§9 static_checks) ──────────────────────────────────────────

ALTER TABLE execution_traces ADD COLUMN parse_ok                INTEGER;
ALTER TABLE execution_traces ADD COLUMN lint_ok                 INTEGER;
ALTER TABLE execution_traces ADD COLUMN typecheck_ok            INTEGER;
ALTER TABLE execution_traces ADD COLUMN import_resolution_ok    INTEGER;

-- ── runtime checks (§9 runtime_checks; stdout/stderr/wall_ms already covered) ─

ALTER TABLE execution_traces ADD COLUMN runtime_executed        INTEGER;
ALTER TABLE execution_traces ADD COLUMN runtime_exit_code       INTEGER;
ALTER TABLE execution_traces ADD COLUMN runtime_exception_type  TEXT;

-- ── tests (§9 tests) ─────────────────────────────────────────────────────────

ALTER TABLE execution_traces ADD COLUMN tests_present           INTEGER;
ALTER TABLE execution_traces ADD COLUMN tests_run               INTEGER;
ALTER TABLE execution_traces ADD COLUMN tests_passed            INTEGER;
ALTER TABLE execution_traces ADD COLUMN tests_failed            INTEGER;

-- ── performance + security (§9 performance, security) ────────────────────────

ALTER TABLE execution_traces ADD COLUMN memory_mb               REAL;
ALTER TABLE execution_traces ADD COLUMN risks_detected_json     TEXT;

-- ── verdict (§9 verdict enum: failed_parse | failed_static | failed_runtime |
-- ──           passed_minimal | passed_verified | passed_benchmarked) ─────────

ALTER TABLE execution_traces ADD COLUMN verdict                 TEXT;

CREATE INDEX IF NOT EXISTS idx_traces_verdict
    ON execution_traces (verdict);

COMMIT;
