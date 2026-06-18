-- 013_selyrioncode_taxonomy_backfill.sql
-- Phase A2 — Selyrion Python adapter taxonomy backfill.
-- Target DB: ~/selyrioncode.db (live coding substrate).
-- Adds two TEXT columns + indices + backfills from existing signals.
-- Idempotent: ALTER guarded by 013_apply.py (SQLite cannot guard ADD COLUMN inline).
-- Reversible: drop columns via table-rebuild snapshot stored in claudecode.db.
-- CHECK constraints NOT applied (SQLite ALTER limitation).
-- Enum reference: SELYRION_PYTHON_ADAPTER.md §3.

BEGIN;

-- ── codeunits.truth_state ─────────────────────────────────────────────────────

ALTER TABLE codeunits ADD COLUMN truth_state TEXT DEFAULT 'proposed';

UPDATE codeunits SET truth_state = 'verified_runtime' WHERE status = 'working';
UPDATE codeunits SET truth_state = 'failed'           WHERE status = 'broken';
-- 'untested' and 'unknown' rows stay at DEFAULT 'proposed'.

CREATE INDEX IF NOT EXISTS idx_codeunits_truth_state ON codeunits (truth_state);

-- ── fix_pairs.repair_class ────────────────────────────────────────────────────

ALTER TABLE fix_pairs ADD COLUMN repair_class TEXT;

-- Backfill via codeunits.error_class (pattern_id is a self-link between
-- similar codeunits, NOT a foreign key into fix_templates).
-- Only update rows currently NULL (idempotent under re-application).

UPDATE fix_pairs SET repair_class = 'syntax_patch'
 WHERE repair_class IS NULL
   AND unit_id IN (
     SELECT id FROM codeunits
     WHERE error_class IN ('IndentationError', 'TabError', 'SyntaxError', 'syntax')
        OR error_class LIKE 'SyntaxError%'
   );

UPDATE fix_pairs SET repair_class = 'import_fix'
 WHERE repair_class IS NULL
   AND unit_id IN (
     SELECT id FROM codeunits
     WHERE error_class IN ('ModuleNotFoundError', 'ImportError')
        OR (error_class = 'runtime' AND subtype IN ('missing_module', 'missing_command'))
   );

UPDATE fix_pairs SET repair_class = 'api_alignment'
 WHERE repair_class IS NULL
   AND unit_id IN (
     SELECT id FROM codeunits
     WHERE error_class IN ('NameError', 'AttributeError')
        OR (error_class = 'runtime' AND subtype IN ('undefined_name', 'missing_attribute'))
   );

UPDATE fix_pairs SET repair_class = 'type_fix'
 WHERE repair_class IS NULL
   AND unit_id IN (
     SELECT id FROM codeunits
     WHERE error_class IN ('TypeError', 'ValueError')
        OR (error_class = 'runtime' AND subtype IN ('type_mismatch', 'value_mismatch'))
   );

UPDATE fix_pairs SET repair_class = 'boundary_fix'
 WHERE repair_class IS NULL
   AND unit_id IN (
     SELECT id FROM codeunits
     WHERE error_class IN ('KeyError', 'IndexError', 'ZeroDivisionError')
        OR (error_class = 'runtime' AND subtype IN ('missing_key', 'index_out_of_range', 'zero_division'))
   );

UPDATE fix_pairs SET repair_class = 'resource_fix'
 WHERE repair_class IS NULL
   AND unit_id IN (
     SELECT id FROM codeunits
     WHERE error_class IN ('FileNotFoundError', 'PermissionError')
        OR (error_class = 'runtime' AND subtype IN ('missing_file', 'permission_denied'))
   );

UPDATE fix_pairs SET repair_class = 'control_flow_fix'
 WHERE repair_class IS NULL
   AND unit_id IN (
     SELECT id FROM codeunits
     WHERE error_class = 'RecursionError'
        OR (error_class = 'runtime' AND subtype = 'recursion')
   );

UPDATE fix_pairs SET repair_class = 'retry_fix'
 WHERE repair_class IS NULL
   AND unit_id IN (
     SELECT id FROM codeunits
     WHERE error_class IN ('ConnectionError', 'TimeoutError')
        OR (error_class = 'runtime' AND subtype = 'network_error')
   );

-- error_class='none' / 'unknown' / 'core' stay NULL (no clear repair class).

CREATE INDEX IF NOT EXISTS idx_fix_pairs_repair_class ON fix_pairs (repair_class);

COMMIT;
