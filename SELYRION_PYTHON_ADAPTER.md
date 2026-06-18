# Selyrion Python Specialist — Adapter Mapping

Bridges the schema in `selyrion_python_expertise_schema.md` (§16) onto the
already-live coding substrate. **No new substrate DB**. The `~/selyrion_python.db`
created by migration 012 is frozen as an audit artifact — it is not the
source of truth.

Locked 2026-06-18 per Tim'aerion Phase A1.

---

## §16 schema name → live store

| §16 surface | Live source | Notes |
|---|---|---|
| `python_anchors` | `~/resonance_v11.db` :: `anchors WHERE anchor_type IN (...)` | See §1 below |
| `python_code_units` | `~/selyrioncode.db` :: `codeunits` | 7,409 rows |
| `python_pattern_clusters` | `~/selyrioncode.db` :: `fix_templates` | 51 rows; carries `success_count`/`fail_count` for promotion policy (§15) |
| `python_failure_cases` | `~/selyrioncode.db` :: `codeunits` WHERE `status='broken'` + `~/claudecode.db` :: `failures` | symptom_json reconstructed at read-time from `codeunits.raw_input` + `error_class` + `subtype` |
| `python_fix_pairs` | `~/selyrioncode.db` :: `fix_pairs` | 3,429 rows |
| `python_relations` | `~/resonance_v11.db` :: `relations_aggregated` WHERE subject ∈ coding anchors | 4,640 edges |
| `python_benchmark_results` | `~/claudecode.db` :: `programming_benchmark_runs` | retrieval-only at present (§10 needs execution suites added in Phase D2) |
| `python_verification_bundle` | `~/claudecode.db` :: `execution_traces` | Migration 016 added 14 §9 verdict cols (parse_ok/lint_ok/typecheck_ok/import_resolution_ok + runtime_executed/exit_code/exception_type + tests_present/run/passed/failed + memory_mb + risks_detected_json + verdict). Pre-existing rows NULL; verdict enum enforced in write path. |
| `python_version_profile` | `~/selyrioncode.db` :: `python_version_profiles` | Added by migration 015. Empty until ingested. |
| `python_library_profile` | `~/selyrioncode.db` :: `python_library_profiles` | Added by migration 015. Empty until ingested. |
| `python_project_profile` | — | GAP. Deferred (Phase D+). |
| `python_explanation_plan` | runtime construct in `language_cognition/` pipeline | not persisted; computed per response |
| `python_security_profile` | `codeops/sandbox.py` :: `BLOCKED_PATTERNS` + AST scan | static; no per-unit persistence yet |
| **language_expression capsules for code register** | — | GAP. Phase C3. LC currently has zero code-domain capsules (capsules.domain has history/science/philosophy/linguistics/etc., but no `computer science` or `programming`). |

---

## §1. Coding anchor types in resonance_v11.db

Total: 1,566 anchors. Distribution:

| anchor_type | count | §4.1 mapping |
|---|---|---|
| `prog_concept` | 914 | `python_concept` (generic concept) |
| `prog_rule` | 316 | `python_concept` (rule subkind) |
| `prog_construct` | 99 | `keyword`, `typing_form`, `async_primitive` |
| `prog_protocol` | 96 | `protocol` |
| `code_entity` | 33 | `builtin`, `stdlib_module`, `exception_type` |
| `prog_type` | 32 | `typing_form` |
| `prog_pattern` | 25 | `pattern` |
| `code_concept` | 21 | (generic) |
| `fix_strategy` | 13 | `pattern` (repair-side) |
| `prog_library` | 7 | `packaging_concept`, `framework_object` |
| `prog_error` | 5 | `exception_type` |
| `code_construct` | 5 | `keyword`, `decorator`, `descriptor` |

**domain_tags** filter: `programming,python` (1,266) / `programming,javascript` (203) / `programming` (25).

---

## §2. Field-level mapping

### python_code_units (§4.2) → selyrioncode.codeunits

| §4.2 field | live column | adapter notes |
|---|---|---|
| `id` | `id` | TEXT, identical |
| `unit_type` | derived from `pattern_id` + `source` | not currently typed; default `'snippet'`. Backfill in Phase C optional. |
| `title` | (none) | use `id` head or first line of `parsed_code` |
| `code` | `parsed_code` | direct |
| `docstring` | extract at read-time from `parsed_code` via AST | not persisted |
| `imports_json` | extract at read-time from `parsed_code` via AST | not persisted |
| `dependencies_json` | `environment` | TEXT; reformat as needed |
| `interfaces_json` | derive at read-time via AST | not persisted |
| `semantics_json.primary_intent` | derive from `error_class` + `subtype` + `pattern_id` | runtime construct |
| `quality_json.parse_ok` | implicit from `status` (`'broken'` ⇒ false) | richer in Phase C2 |
| `quality_json.runtime_ok` | `status='working'` | |
| `provenance.source_type` | `source` (TEXT) | direct; values: archaeologist/gpt_ingest/python_fundamentals/phone_ingest/build_log/etc. |
| `truth_state` | **NEW column added by migration 013** | see §3 below |
| `created_at` | `created_at` REAL | direct |
| `updated_at` | (none — uses `created_at`) | add in Phase C if needed |

### python_fix_pairs (§4.5) → selyrioncode.fix_pairs

| §4.5 field | live column | adapter notes |
|---|---|---|
| `id` | `id` | direct |
| `before_unit_id` | `unit_id` | direct |
| `after_unit_id` | (none) | `fix` text not promoted to a unit; derive at read-time |
| `failure_case_id` | `unit_id` (when `codeunits.status='broken'`) | same id space |
| `repair_class` | **NEW column added by migration 013** | enum from §4.5 (12 classes) |
| `evidence_json.test_delta` | `verified` (0/1) | minimal |
| `evidence_json.runtime_delta` | `fix_status` | TEXT: failed/verified/unknown/proposed |
| `generalizability` | derive from `fix_templates.success_count`/`fail_count` lookup | not stored per-pair |

### python_pattern_clusters (§4.3) → selyrioncode.fix_templates

| §4.3 field | live column | adapter notes |
|---|---|---|
| `id` | `id` | direct |
| `name` | `strategy` | TEXT; e.g. `normalize_indent`, `wrap_orphan_branch` |
| `category` | derive from `error_class` | maps: SyntaxError→`error_handling`, IndentationError→`error_handling`, ImportError→`error_handling`, etc. |
| `abstraction_level` | infer from `strategy` | `normalize_indent`/`dedent_body` → micro; `add_import`/`balance_parens` → micro; `classify_pseudocode` → macro |
| `canonical_shape` | `example_in` + `example_out` | TEXT pair |
| `representative_units` | `JOIN codeunits ON pattern_id` | runtime query |
| `trust_score` | derive: `success_count / (success_count + fail_count)` | computed |

### python_relations (§5) → relations_aggregated (CMS)

| §5 field | live column | adapter notes |
|---|---|---|
| `subject_id` / `predicate` / `object_id` | identical | direct |
| `strength` | `confidence` REAL + `edge_weight` REAL | use `edge_weight` |
| `evidence_type` | `edge_type` | TEXT; need taxonomy alignment in Phase C |
| `truth_state` | `relations_aggregated.truth_status` (per memory entry on migration 005) | direct |
| `scope.python_version` / `package` | encoded in `domain_tags` | TEXT comma-list; parse at read-time |

### python_failure_cases (§4.4) → codeunits + claudecode.failures

Read-time composition:

```sql
-- For a broken codeunit:
SELECT
  cu.id                                    AS id,
  COALESCE(cu.error_class, 'logic_bug')    AS failure_type,
  json_object(
    'traceback',          cu.raw_input,
    'observed_behavior',  cu.subtype
  )                                        AS symptom_json,
  cu.environment                           AS environment_json,
  cu.id                                    AS target_unit_id,
  cu.fix_text                              AS confirmed_root_cause,
  cu.status                                AS status
FROM codeunits cu
WHERE cu.status = 'broken';
```

For session-level failures, join `~/claudecode.db :: failures` on tag patterns.

---

## §3. Phase A2 migration 013 — taxonomy backfill

Adds two TEXT columns to `~/selyrioncode.db`:

1. `codeunits.truth_state` (default `'proposed'`)
2. `fix_pairs.repair_class` (default NULL)

Backfill `truth_state` from existing `status`:

| existing status | backfilled truth_state | rows |
|---|---|---|
| `working` | `verified_runtime` | 6,569 |
| `broken` | `failed` | 779 |
| `untested` | `proposed` | 16 |
| `unknown` | `proposed` | 45 |

Backfill `fix_pairs.repair_class` from existing `fix_templates.strategy` (joined via `codeunits.pattern_id`):

| strategy | repair_class |
|---|---|
| `normalize_indent` / `dedent_body` | `syntax_patch` |
| `wrap_orphan_branch` / `add_pass_to_empty_block` / `complete_truncated_try` | `control_flow_fix` |
| `add_import` | `import_fix` |
| `balance_parens` | `syntax_patch` |
| `add_none_default` | `api_alignment` |
| `timeout_retry` | `retry_fix` |
| `classify_pseudocode` | (NULL — meta-classifier, not a fix) |

CHECK constraints **not** enforced at SQLite level (ALTER TABLE limitation) — validation lives in the write path. Enum values documented here as the canonical reference.

### truth_state enum (§4.2)

`proposed`, `plausible`, `verified_static`, `verified_runtime`,
`regression_tested`, `benchmarked`, `deprecated`, `quarantined`, `failed`

### repair_class enum (§4.5)

`syntax_patch`, `import_fix`, `type_fix`, `api_alignment`, `boundary_fix`,
`async_fix`, `resource_fix`, `control_flow_fix`, `exception_fix`,
`retry_fix`, `security_fix`, `performance_fix`

---

## §4. What this adapter explicitly does NOT do

- Does not move data between stores.
- Does not write to `~/resonance_v11.db` (CMS read-only from this seam).
- Does not change `codeops/` behavior. Operator wiring is Phase B.
- Does not deprecate `~/selyrion_python.db`. It stays as a frozen audit artifact until Phase E.
- Does not add CHECK constraints (SQLite ALTER TABLE limitation). Enums enforced in code.

---

## §5. Phase sequence reminder

A1 (this doc) → A2 (migration 013) → B (operator registration) → C1 (version/library profiles) → C3 (coding capsules) → C2 (verification bundle) → D (promotion + benchmarks) → E (retire selyrion_python.db).
