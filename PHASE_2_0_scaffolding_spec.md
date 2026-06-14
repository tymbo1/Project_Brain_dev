# Phase 2.0 — Symbolic Execution Core Scaffolding Spec

**Status:** DRAFT — scaffold only, no code yet.
**Authored:** 2026-06-14 (post-1.4b graduation).
**Pairs with:** `project_symbolic_computation_implementation.md` (the spine), `project_soce_architecture.md` (SOCE packet/operator contracts).
**Doctrine carried from Phase 1:** rank-only retrieval invariant; kill switches retained as operational safety; W24 (no global penalty, bridges sacred); single change per merge; HITL gates before behavior flips; substrate writes only through approved pipelines.

---

## 0. Why now

1.4b graduated. Substrate is more stable without being more reckless. Residual cleanup (1.5b NULL anchor_type, derive_domain_compat.py) is still useful but no longer the highest-leverage frontier. The bigger unlock is to start making the substrate **computable**, not just cleaner.

Phase 2.0 is the **scaffold for symbolic execution** — the bones that later phases (causal, contradiction, hierarchy, analogy, planning, …) hang off. The five surfaces in scope are precisely the ones that turn retrieval into computation.

---

## 1. Scope — five surfaces

| # | surface                              | one-line purpose                                                                    |
|---|--------------------------------------|-------------------------------------------------------------------------------------|
| 1 | operator registry                    | typed, declared, enable-gated table of operators with input/output/truth contracts |
| 2 | unification / pattern-binding core   | variable binding + structural template match over typed substrate slices            |
| 3 | working-memory scratch layer         | ephemeral graph isolated from `relations_aggregated`; episode-scoped                |
| 4 | operator trace format                | replayable, inspectable, score-attributable execution record                        |
| 5 | first benchmark suite                | direct symbolic operator tests — referent stability, unification, trace replay      |

**Out of scope for 2.0:**
- causal / contradiction / analogy operator *implementations* (those are Phase 3)
- commit pipeline writes into `relations_aggregated` (deferred to 2.x once 2.0 stable)
- language → symbolic transforms (Phase 5)
- truth-state algebra **extensions** (Phase 1.3 substrate is sufficient for 2.0)
- contradiction worker (firmly gated, independent evidence cycle required)

---

## 2. Substrate dependencies (must hold before 2.0 builds)

These were established in Phase 1 and must remain authoritative:

- `predicates` table typed (Phase 1.2, 1.2b)
- `relations_aggregated.{domain_purity, truth_status, predicate_layer, source_type}` populated (1.3, 1.4)
- `anchors.anchor_type` largely populated (1.1, residual ~132k NULL OK for 2.0)
- 1.4b retrieval policy is stable default (rank-only invariant; kill switch retained)
- Contradiction worker stays gated

**Invariant carried from 1.4b:** no symbolic operator in 2.0 may modify retrieval ranking or mutate stored confidence. Symbolic execution runs against retrieved slices or working sets, not against the live substrate.

---

## 3. Surface 1 — Operator registry

### 3.1 Purpose
Make operators **declared, typed, and gated** — never ad-hoc Python imports.

### 3.2 Schema (new table `operator_registry`)
```
name TEXT PRIMARY KEY,
category TEXT NOT NULL,            -- match|infer|compose|constrain|...
input_schema JSON NOT NULL,        -- typed args
output_schema JSON NOT NULL,       -- typed result
truth_policy TEXT NOT NULL,        -- proposes|asserts|never_writes
cost_class TEXT NOT NULL,          -- O(1)|O(k)|O(n)|bounded|unbounded
grounding TEXT NOT NULL,           -- py:module.callable
enabled INTEGER NOT NULL DEFAULT 0,
created_at INTEGER, updated_at INTEGER
```

### 3.3 Initial rows (DECLARATIONS ONLY — no implementations in 2.0)
- `noop_passthrough` (sanity probe, always enabled)
- `unify_pattern` (Surface 2 reference)
- `working_set_create` (Surface 3 reference)
- placeholder declarations for Phase 3 operators (`causal_chain`, `contradiction_scan`, etc.) — `enabled=0`

### 3.4 Decision points (HITL required before merge)
- **Q-2.0.A:** registry storage — `claudecode.db` (lightweight, session-scoped) vs `resonance_v11.db` (substrate-scoped) vs new `selyrion_ops.db`?
- **Q-2.0.B:** truth_policy enum — three classes sufficient (`proposes|asserts|never_writes`) or do we need `observed`/`tentative` as first-class too?
- **Q-2.0.C:** does `enabled=0` block execution at the dispatcher, or at the call site?

---

## 4. Surface 2 — Unification / pattern-binding core

### 4.1 Purpose
Pattern matching with **variable binding** over typed substrate slices. Without unification, "symbolic" is just search.

### 4.2 New module: `inference/unify.py`
**API (declared, not yet implemented):**
```
unify(pattern, graph_slice, type_env=None, constraints=None) -> list[BindingSet]
```

`pattern`: list of typed edge templates with variables, e.g. `[(?X, causes, ?Y), (?Y, leads_to, ?Z)]`.
`graph_slice`: a `WorkingSet` or a retrieved candidate set (NOT the live substrate).
`type_env`: maps variable names to anchor_type / predicate_layer constraints.
`constraints`: side-conditions (domain_scope, truth_status floor, etc.).

`BindingSet`: variable assignments, matched edges, confidence aggregate, violated constraints, provenance refs.

### 4.3 Required capabilities for 2.0
- variable binding (single + multi-pattern)
- typed wildcard (`?X:anchor_type=concept`)
- domain-bounded matching (respects 1.4 domain_purity)
- bridge-sacred behavior carried from 1.4b — bridge edges never excluded by purity
- multi-hop chained patterns (≤ k hops, k configurable)

### 4.4 Out of scope for 2.0
- soft / fuzzy unification (Phase 3)
- role-consistent mapping for analogies (Phase 3.4)
- partial graph isomorphism beyond k-hop chains

### 4.5 Decision points
- **Q-2.0.D:** binding semantics — first-match, all-matches, or top-k-by-score?
- **Q-2.0.E:** does unification consult `relations_aggregated.domain_purity` directly, or only via the retrieval slice that produced the working set?
- **Q-2.0.F:** how are bridge edges flagged into the matcher — pre-tagged by retrieval, or queried inline?

---

## 5. Surface 3 — Working-memory scratch layer

### 5.1 Purpose
**Never reason on the 97GB live substrate.** Every symbolic episode runs in an isolated, ephemeral graph.

### 5.2 Schema (new tables)
```
working_sets(
  id TEXT PRIMARY KEY,
  purpose TEXT,                    -- query|operator_chain|hypothesis_test
  query TEXT,                      -- originating user/operator query
  created_by TEXT,                 -- session_id / operator name
  status TEXT,                     -- open|sealed|expired
  expires_at INTEGER,
  parent_set TEXT,                 -- nesting
  created_at INTEGER
)

working_set_items(
  working_set_id TEXT,
  item_type TEXT,                  -- anchor|relation|hypothesis|constraint|binding
  item_ref TEXT,                   -- id in source table or local id
  local_score REAL,
  state TEXT,                      -- candidate|active|rejected|committed
  provenance JSON
)

working_set_edges(
  working_set_id TEXT,
  subject_id INTEGER,
  predicate TEXT,
  object_id INTEGER,
  local_truth TEXT,
  local_confidence REAL,
  provenance JSON
)
```

### 5.3 Lifecycle
```
create(purpose, query) -> ws_id
load_candidates(ws_id, retrieval_result)
run_operator(ws_id, op, args) -> updates items/edges
score_episode(ws_id) -> bool accept
seal(ws_id)  -- prevents further mutation; remains queryable
```

### 5.4 Invariants
- working sets are **scratch** — nothing in 2.0 promotes from working sets to `relations_aggregated`
- working sets are **isolated** — operators may read substrate but write only to their working set
- working sets expire — default TTL 24h, configurable per purpose
- working sets are **inspectable** — replay must reconstruct the same final state from the operator trace

### 5.5 Decision points
- **Q-2.0.G:** storage location — `claudecode.db` (session-aligned), `resonance_v11.db` (alongside substrate), or new `selyrion_workmem.db` (isolation)?
- **Q-2.0.H:** TTL enforcement — passive (lazy GC on access) or active (cron-like worker)?
- **Q-2.0.I:** parent_set semantics — strict tree, DAG, or flat tag?

---

## 6. Surface 4 — Operator trace format

### 6.1 Purpose
**Reasoning observability** (SCOS Principle 3) — every operator call must be replayable, scorable, attributable.

### 6.2 Trace record shape
```json
{
  "trace_id": "tr.XXXX",
  "ts": 1750000000,
  "session_id": "session.YYYY-MM-DD",
  "operator": "unify_pattern",
  "working_set_id": "ws.XXXX",
  "inputs": { "...": "..." },
  "outputs": { "...": "..." },
  "decisions": [
    { "step": 1, "kind": "bind", "var": "?X", "bound_to": 12345 },
    { "step": 2, "kind": "filter", "reason": "domain_purity:weak_cross_domain", "passed": false }
  ],
  "scores": { "confidence": 0.82, "evidence_weight": 3 },
  "duration_ms": 42,
  "outcome": "accepted|rejected|deferred",
  "provenance": { "retrieval_hash": "...", "substrate_epoch": 12 }
}
```

### 6.3 Storage
- Append-only JSONL per session, mirrored to `claudecode.db.operator_runs` for queryability
- Mirror schema:
```
operator_runs(
  trace_id TEXT PRIMARY KEY,
  ts INTEGER, session_id TEXT,
  operator TEXT, working_set_id TEXT,
  outcome TEXT, duration_ms INTEGER,
  score REAL, summary JSON
)
```

### 6.4 Required properties
- replayable: trace + working set snapshot reproduces outcome
- attributable: every decision step has a `reason` field (mirrors 1.4b trace `decision` discipline)
- score-comparable: A/B traces of same operator with different params can be diffed
- inspectable without code: humans can read the JSONL

### 6.5 Decision points
- **Q-2.0.J:** trace verbosity gate — always-on, env-flag-gated (like `RETRIEVAL_PURITY_TRACE_ENABLED`), or per-operator?
- **Q-2.0.K:** retention policy — trim after N days, or keep all and rely on archival?

---

## 7. Surface 5 — First benchmark suite

### 7.1 Purpose
**Direct symbolic operator tests.** Retrieval benchmarks don't measure symbolic growth. If we don't bench operators directly, retrieval improvements will be mistaken for symbolic improvements (and vice versa).

### 7.2 In-scope benchmarks for 2.0
| benchmark                       | what it measures                                                  | pass criterion                              |
|---------------------------------|-------------------------------------------------------------------|---------------------------------------------|
| `bench_referent_stability`      | same input query → same anchor ids over N runs                    | identity rate ≥ 0.99                        |
| `bench_unification_correctness` | hand-labeled pattern → expected bindings                          | precision ≥ 0.95, recall ≥ 0.90             |
| `bench_working_set_isolation`   | operators run in ws_A do not leak items into ws_B                 | leak count == 0                             |
| `bench_trace_replay`            | replay from trace reproduces final ws state                       | byte-equality of canonical state hash       |
| `bench_registry_dispatch`       | `enabled=0` operator is not dispatchable; `enabled=1` is          | 0 false-positives, 0 false-negatives        |

### 7.3 Out of scope for 2.0
- causal chain completion (Phase 3)
- contradiction detection (Phase 3, separate evidence cycle)
- analogy accuracy (Phase 3.4)
- planning correctness (Phase 4)

### 7.4 Harness pattern (mirror `run_purity_canary.py` discipline)
- subprocess isolation: each run a fresh process, no in-memory state carry
- A/B mode: registry-flag-gated, baseline = all operators disabled
- JSONL outputs + summary JSON
- explicit pass/fail criteria; binary `BENCH_PASS`

### 7.5 Decision points
- **Q-2.0.L:** benchmark labeled-set source — hand-built minimal (10–20 cases) or synthesized from 1.4b traces?
- **Q-2.0.M:** unification ground truth — Tim-labeled or LLM-proposed + HITL-reviewed?

---

## 8. Build order within Phase 2.0

```
2.0.1  Operator registry table + dispatcher (declarations only)  [DONE]
2.0.2  Working-memory scratch tables + lifecycle API             [DONE]
2.0.3  Unification core (variable binding, typed wildcards, k-hop chains)  -- reordered 2026-06-14 20:32 checkpoint
2.0.4  Operator trace format + storage                           -- moved from 2.0.3 to 2.0.4
2.0.5  Benchmark suite (5 benchmarks above)
2.0.6  Canary cycle — run benches under registry-off baseline vs registry-on
2.0.7  Graduation checkpoint (mirror 1.4b graduation discipline)
```

**Rule:** 2.0.N must pass its acceptance gate before 2.0.(N+1) starts. No parallel substrate-altering work during 2.0 build.

**Sequencing change rationale (2026-06-14 20:32):** trace had nothing to wrap until unification existed; unification now has scratch space to land in. Swap is net-zero on gates and net-positive on dependency order. See `project_phase2_0_2_checkpoint.md`.

---

## 9. Acceptance gates per step

| step    | gate                                                                                          |
|---------|-----------------------------------------------------------------------------------------------|
| 2.0.1   | registry table exists, `noop_passthrough` declared+enabled, dispatcher rejects unknown ops    |
| 2.0.2   | create/load/seal lifecycle works; isolation test passes; TTL expiry verified                  |
| 2.0.3   | trace records emitted for `noop_passthrough` runs; replay produces identical outcome          |
| 2.0.4   | hand-labeled minimal unification set passes precision ≥ 0.95                                  |
| 2.0.5   | all 5 benchmarks runnable; baseline (all-off) and on-state both produce JSONL summaries       |
| 2.0.6   | canary: bench results stable across two consecutive runs; no regression on 1.4b canary set    |
| 2.0.7   | HITL approval; memory braid; claudecode.db invariants written                                 |

---

## 10. Anti-patterns (carry from Phase 1 doctrine)

- **No default-on for new operators.** Every registry row ships `enabled=0`; HITL flips per operator after its own bench passes.
- **No substrate writes from working sets in 2.0.** Commit pipeline is 2.x territory.
- **No operator reads/writes outside its working set.** Substrate is read-only at the retrieval boundary.
- **No silent dispatch.** Every operator call emits a trace record, even on failure.
- **No retrieval policy changes during 2.0 build.** 1.4b is locked; if benches reveal retrieval-side issues, file a Phase 1 follow-up — do not touch retrieval inside 2.0.
- **No contradiction worker.** Stays gated. Substrate strength is necessary but not sufficient.

---

## 11. HITL decisions — RESOLVED 2026-06-14 04:22

All 13 confirmed with 3 guardrails. Policy layer locked. 2.0.1 is now startable.

| Q | resolution                                              | guardrail                                                          |
|---|---------------------------------------------------------|--------------------------------------------------------------------|
| A | registry in `resonance_v11.db`                          | —                                                                  |
| B | 5-class `truth_policy`: `never_writes\|proposes\|observes\|asserts\|retracts` | — |
| C | `enabled=0` enforced at dispatcher layer                | —                                                                  |
| D | top-k binding by score, default k=8                     | **hard ceiling** on candidate set — no operator may silently explode |
| E | unification reads `domain_purity` via retrieval slice only | —                                                              |
| F | bridge edges pre-tagged by retrieval into `working_set_edges` | —                                                            |
| G | working-memory in new `selyrion_workmem.db`             | —                                                                  |
| H | lazy GC on access + nightly sweep                       | **fail-closed on read of expired sets** — never serve stale data while waiting for sweep |
| I | `parent_set` = strict tree                              | —                                                                  |
| J | summary trace always-on; full-detail flag-gated         | —                                                                  |
| K | JSONL 30d rolling; `operator_runs` mirror keep-all      | **mirror stays compact summary-only** so "keep all" remains cheap  |
| L | hand-built minimal labeled set (10–20 cases) for 2.0.5  | —                                                                  |
| M | Tim-labeled minimal; LLM-proposed + HITL-reviewed expansion | —                                                              |

### Guardrail details

**D-guardrail — hard ceiling on top-k:**
```
default_k = 8
hard_ceiling_k = 64           # absolute upper bound, no operator override
unify(...) returns at most hard_ceiling_k bindings, even if k requested > 64
```
Rationale: protects against silent fan-out blowing up working-memory budgets, mirrors 1.4b "no soft filter, no nonlocal effects" doctrine.

**H-guardrail — expired-set fail-closed:**
```
on read of ws where expires_at < now:
  status := "expired"
  reads RAISE WorkingSetExpired  (no partial results returned)
  sweep cron is cleanup, not access-control
```
Rationale: TTL is a *correctness* boundary, not a *cleanup* boundary. Stale reads are worse than missing reads.

**K-guardrail — mirror compactness:**
```
operator_runs columns: trace_id, ts, session_id, operator,
                       working_set_id, outcome, duration_ms, score, summary
summary JSON: top-level only — operator name, outcome, score, key counts.
              NO decisions[] array, NO full inputs/outputs blobs.
full detail lives only in the JSONL (30d rolling).
```
Rationale: "keep all" must remain cheap or it stops being kept.

---

## 11a. Updated invariants (carry into 2.0.1+)

- registry doctrine: every operator declared in `resonance_v11.db.operator_registry`, `enabled=0` default, dispatcher is single gate
- working-mem doctrine: `selyrion_workmem.db` is the only place working sets live; substrate stays read-only
- TTL doctrine: expired = fail-closed on read; sweep is cleanup not gate
- trace doctrine: every operator call emits at least a summary record to `operator_runs`; full detail under env flag, 30d retention
- unification doctrine: top-k≤hard_ceiling=64, ranking-as-policy not filter-as-policy, no inline substrate re-queries

---

## 12. What this spec is NOT

- Not a code drop. Zero implementation in this file.
- Not a commit. No tables created, no modules written, no flags flipped.
- Not a contradiction-automation enabler.
- Not a substrate-write proposal.

It is the **scaffold map** that Phase 2.0 implementation will follow once the Q-2.0.* decisions are resolved.
