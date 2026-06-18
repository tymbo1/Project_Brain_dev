-- 012_selyrion_python_backbone.sql
-- Selyrion Python specialist substrate — step 1 (per schema §20).
-- Target DB: ~/selyrion_python.db (new isolated DB, never resonance_v11.db).
-- Schema only. No rows inserted into substrate tables.
-- Idempotent: re-running is safe.

BEGIN;

-- ── python_anchors ────────────────────────────────────────────────────────────
-- §4.1 — typed concept anchors for Python.

CREATE TABLE IF NOT EXISTS python_anchors (
    id                    TEXT PRIMARY KEY,
    canonical             TEXT NOT NULL,
    subtype               TEXT NOT NULL
                            CHECK (subtype IN (
                                'keyword','builtin','stdlib_module','protocol',
                                'decorator','descriptor','typing_form','async_primitive',
                                'packaging_concept','runtime_concept','pattern','antipattern',
                                'exception_type','testing_construct','framework_object'
                            )),
    summary               TEXT,
    aliases_json          TEXT,
    py_version_min        TEXT,
    py_version_max        TEXT,
    stability             TEXT
                            CHECK (stability IS NULL OR stability IN (
                                'stable','version_sensitive','deprecated','provisional'
                            )),
    related_subdomains_json TEXT,
    provenance_refs_json  TEXT,
    trust_score           REAL NOT NULL DEFAULT 0.0
                            CHECK (trust_score BETWEEN 0.0 AND 1.0),
    created_at            REAL NOT NULL,
    updated_at            REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_py_anchors_canonical ON python_anchors (canonical);
CREATE INDEX IF NOT EXISTS idx_py_anchors_subtype   ON python_anchors (subtype);
CREATE INDEX IF NOT EXISTS idx_py_anchors_stability ON python_anchors (stability);

-- ── python_code_units ─────────────────────────────────────────────────────────
-- §4.2 — typed code unit (function/class/module/snippet/...).

CREATE TABLE IF NOT EXISTS python_code_units (
    id                TEXT PRIMARY KEY,
    unit_type         TEXT NOT NULL
                        CHECK (unit_type IN (
                            'function','class','method','module','package',
                            'snippet','test_case','config_fragment','migration_step','cli_entry'
                        )),
    title             TEXT,
    code              TEXT NOT NULL,
    docstring         TEXT,
    imports_json      TEXT,
    dependencies_json TEXT,
    interfaces_json   TEXT,
    semantics_json    TEXT,
    quality_json      TEXT,
    provenance_json   TEXT,
    truth_state       TEXT NOT NULL
                        CHECK (truth_state IN (
                            'proposed','plausible','verified_static','verified_runtime',
                            'regression_tested','benchmarked','deprecated',
                            'quarantined','failed'
                        )),
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_py_units_unit_type   ON python_code_units (unit_type);
CREATE INDEX IF NOT EXISTS idx_py_units_truth_state ON python_code_units (truth_state);
CREATE INDEX IF NOT EXISTS idx_py_units_updated_at  ON python_code_units (updated_at);

-- ── python_failure_cases ──────────────────────────────────────────────────────
-- §4.4 — failure records (traceback / symptom / repair lineage).

CREATE TABLE IF NOT EXISTS python_failure_cases (
    id                          TEXT PRIMARY KEY,
    failure_type                TEXT NOT NULL
                                  CHECK (failure_type IN (
                                      'syntax_error','import_error','module_not_found',
                                      'type_error','attribute_error','key_error','index_error',
                                      'value_error','recursion_error','async_misuse','deadlock',
                                      'resource_leak','test_failure','package_conflict',
                                      'performance_regression','logic_bug','security_issue'
                                  )),
    symptom_json                TEXT NOT NULL,
    environment_json            TEXT,
    target_unit_id              TEXT
                                  REFERENCES python_code_units(id) ON DELETE SET NULL,
    root_cause_hypotheses_json  TEXT,
    confirmed_root_cause        TEXT,
    linked_fix_pairs_json       TEXT,
    regression_tests_json       TEXT,
    status                      TEXT NOT NULL
                                  CHECK (status IN (
                                      'open','hypothesized','fixed_unverified',
                                      'fixed_verified','archived'
                                  )),
    created_at                  REAL NOT NULL,
    updated_at                  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_py_fail_failure_type ON python_failure_cases (failure_type);
CREATE INDEX IF NOT EXISTS idx_py_fail_status       ON python_failure_cases (status);
CREATE INDEX IF NOT EXISTS idx_py_fail_target_unit  ON python_failure_cases (target_unit_id);

-- ── python_meta (migration tracking) ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS python_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);

INSERT OR REPLACE INTO python_meta (key, value, updated_at) VALUES
    ('schema_version',           '012',                          strftime('%s','now')),
    ('purpose',                  'selyrion_python_backbone',     strftime('%s','now')),
    ('substrate_writes_allowed', '0',                            strftime('%s','now')),
    ('source_doc',               'selyrion_python_expertise_schema.md §20 step 1',
                                                                  strftime('%s','now')),
    ('scope_step',               '1_of_8',                       strftime('%s','now'));

COMMIT;
