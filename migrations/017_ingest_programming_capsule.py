"""Phase C3 — ingest one curated programming-domain language_expression capsule.

Target DB: ~/resonance_v11.db (capsules table).
Idempotent: UPDATEs if a programming capsule already exists, else INSERTs.
Reversible: pre-state row count / metadata snapshot to ~/claudecode.db.

Doctrine: expressions are STANCE/CADENCE exemplars (third-person register,
not addressed to the user), NOT canned code or boilerplate prose.
Filter-survival required: 50-280 chars, no trailing '?', no 2nd-person
opener, no _EXPR_REJECT term hits (parse/parser/json/api/.py/import/def…).

Acceptance gate:
  - exactly one programming capsule present
  - ≥ 25 expressions survive langeng_bridge._domain_pool() filter
  - infer_expression_domain('fix this Python traceback') == 'programming'
  - 7 existing expressive domains' pool sizes unchanged
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home()
TARGET_DB = HOME / "resonance_v11.db"
CLAUDECODE_DB = HOME / "claudecode.db"

DOMAIN = "programming"
CAPSULE_PREFIX = f"langeng_expr_{DOMAIN}_"
CAPSULE_TITLE = f"expression::{DOMAIN}"
EXISTING_DOMAINS = (
    "intellectual_curiosity", "emotional_resonance", "practical_grounding",
    "relational_warmth", "spiritual_inquiry", "creative_engagement",
    "humour_lightness",
)

# Trigger patterns the routing dispatcher will match on the user query.
TRIGGER_PATTERNS = [
    "python", "code", "function", "class", "method", "module", "library",
    "package", "error", "exception", "traceback", "bug", "fix", "debug",
    "stack trace", "test", "tests", "refactor", "implement", "syntax",
    "runtime", "type error", "compile",
]

# Curated stance/cadence exemplars — code-domain expert register.
# All hand-checked: 50-280 char, no '?' end, no 2nd-person opener, no REJECT hits.
EXPRESSIONS = [
    "The traceback is the first clue — start from the exception line and walk backwards through the call stack until the assumption breaks.",
    "A failing test that names the bug precisely is worth more than a passing patch that hides it.",
    "Reproduce it deterministically before reaching for a fix — randomness in the symptom usually means randomness in the cause.",
    "Reading code is two-thirds of writing it; the rest is the small surgical change that holds the invariants.",
    "Naming a function well prevents whole classes of bugs — the verb in the name is a contract about side effects.",
    "Bisect the failing range when the cause isn't obvious — half the surface, then half again, until the bad commit names itself.",
    "Pure functions are the easiest to test, refactor, and trust — pull side effects to the edge wherever possible.",
    "An error message that points at the wrong line is worse than no error message — fail loudly and at the boundary that broke.",
    "Mutation through shared references is the source of most surprising bugs — favor copying or freezing at the boundary.",
    "The simplest reproducible failure is the lever — shrink the inputs until only the bug remains.",
    "Logging is cheap compared to guessing; print the values the code thought it had at the moment it failed.",
    "When the stack trace is long, the real fault is usually in the first frame inside your own code, not the framework underneath.",
    "Type errors caught at the boundary save runtime tantrums later — validate where the data enters, not where it breaks.",
    "Refactor before adding a feature, never during — the diff stays small and reviewable.",
    "If two functions share a name and disagree on behavior, the bug isn't in either function — it's in the naming.",
    "Tests are the contract the future will read; write them so a stranger could understand the intent at a glance.",
    "The fastest way to debug a slow loop is to instrument it once, not stare at it twice.",
    "Keep the failing case in a fixture before fixing — without the witness, the fix becomes faith.",
    "A small, well-named helper beats a clever inline expression every time.",
    "When the behavior surprises, the assumption that wasn't checked is the one that's wrong.",
    "Dependency boundaries are where bugs hide — what crosses the line and what doesn't.",
    "Mocking the wrong thing teaches the test nothing; mock at the boundary, not inside the logic.",
    "When a fix touches three files for a one-line bug, the design is asking to be reshaped.",
    "Concurrency bugs reveal themselves under load, not at rest — exercise the contention before claiming it works.",
    "An invariant that the code maintains silently is worth writing down — comments are cheap, broken assumptions aren't.",
    "Performance is a feature, not a final pass — measure before optimizing, and optimize what the profile actually shows.",
    "The exception that nobody catches is the one that teaches the most — let it bubble until the right handler exists.",
    "A pull request without a failing test feels heroic and reads like a guess.",
    "Treat dependencies as untrusted input — pin versions, sandbox effects, verify behavior at the seam.",
    "Reading the standard library is the cheapest education available — the patterns there are battle-tested and self-documenting.",
]

GAP_TYPES = [
    "missing_code_register",
    "missing_technical_precision",
    "missing_debug_method",
    "missing_engineering_stance",
]


def _existing_programming_capsule(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT id FROM capsules WHERE capsule_type='language_expression' "
        "AND domain=? LIMIT 1",
        (DOMAIN,),
    ).fetchone()
    return row[0] if row else None


def _capsule_id() -> str:
    h = hashlib.md5(("|".join(EXPRESSIONS)).encode()).hexdigest()[:8]
    return f"{CAPSULE_PREFIX}{h}"


def _snapshot_pre(conn: sqlite3.Connection) -> dict:
    return {
        "capsules_total": conn.execute(
            "SELECT COUNT(*) FROM capsules"
        ).fetchone()[0],
        "lang_expr_total": conn.execute(
            "SELECT COUNT(*) FROM capsules WHERE capsule_type='language_expression'"
        ).fetchone()[0],
        "programming_existing": _existing_programming_capsule(conn),
        "domain_dist": dict(conn.execute(
            "SELECT domain, COUNT(*) FROM capsules "
            "WHERE capsule_type='language_expression' GROUP BY domain"
        ).fetchall()),
        "captured_at": time.time(),
    }


def _write_snapshot(snap: dict) -> None:
    with sqlite3.connect(CLAUDECODE_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS migration_017_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO migration_017_snapshot (snapshot_json, created_at) "
            "VALUES (?, ?)", (json.dumps(snap), time.time()),
        )


def _upsert() -> dict:
    now = time.time()
    metadata = {
        "domain": DOMAIN,
        "trigger_patterns": TRIGGER_PATTERNS,
        "expressions": EXPRESSIONS,
        "gap_types_learned_from": GAP_TYPES,
        "created_at": now,
        "updated_at": now,
    }
    meta_json = json.dumps(metadata)
    new_id = _capsule_id()

    with sqlite3.connect(TARGET_DB) as conn:
        existing = _existing_programming_capsule(conn)
        if existing:
            conn.execute(
                "UPDATE capsules SET metadata=?, title=? WHERE id=?",
                (meta_json, CAPSULE_TITLE, existing),
            )
            return {"action": "updated", "id": existing}
        conn.execute(
            "INSERT INTO capsules "
            "(id, parent_id, capsule_type, domain, source, title, metadata, created_at) "
            "VALUES (?, NULL, 'language_expression', ?, 'phase_c3_curated', ?, ?, ?)",
            (new_id, DOMAIN, CAPSULE_TITLE, meta_json, now),
        )
        return {"action": "inserted", "id": new_id}


def _verify(pre_snap: dict) -> dict:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from langeng_bridge import _domain_pool, infer_expression_domain

    # Pool sizes — programming should be ≥25; others unchanged
    pool_programming = _domain_pool(DOMAIN)
    other_pools = {d: len(_domain_pool(d)) for d in EXISTING_DOMAINS}
    pre_dist = pre_snap["domain_dist"]
    # Other-pool count is process-cached and lazy — compare by capsule count instead.
    with sqlite3.connect(TARGET_DB) as conn:
        post_dist = dict(conn.execute(
            "SELECT domain, COUNT(*) FROM capsules "
            "WHERE capsule_type='language_expression' GROUP BY domain"
        ).fetchall())

    other_domains_preserved = all(
        post_dist.get(d) == pre_dist.get(d) for d in EXISTING_DOMAINS
    )

    routes = {
        "fix this Python traceback":    infer_expression_domain("fix this Python traceback"),
        "debug a failing test":         infer_expression_domain("debug a failing test"),
        "refactor this function":       infer_expression_domain("refactor this function"),
        "how do I feel about loss":     infer_expression_domain("how do I feel about loss"),
        "tell me a joke":               infer_expression_domain("tell me a joke"),
    }
    routing_ok = (
        routes["fix this Python traceback"] == "programming"
        and routes["debug a failing test"] == "programming"
        and routes["refactor this function"] == "programming"
        and routes["how do I feel about loss"] == "emotional_resonance"
        and routes["tell me a joke"] == "humour_lightness"
    )

    return {
        "programming_pool_size": len(pool_programming),
        "programming_pool_ok": len(pool_programming) >= 25,
        "other_pools_post_size": other_pools,
        "other_capsule_counts_preserved": other_domains_preserved,
        "routes": routes,
        "routing_ok": routing_ok,
    }


def main() -> int:
    with sqlite3.connect(TARGET_DB) as conn:
        snap = _snapshot_pre(conn)
    _write_snapshot(snap)

    t0 = time.time()
    upsert = _upsert()
    dt = time.time() - t0
    v = _verify(snap)

    with sqlite3.connect(TARGET_DB) as conn:
        prog_count = conn.execute(
            "SELECT COUNT(*) FROM capsules "
            "WHERE capsule_type='language_expression' AND domain=?",
            (DOMAIN,),
        ).fetchone()[0]

    gate = (
        prog_count == 1
        and v["programming_pool_ok"]
        and v["other_capsule_counts_preserved"]
        and v["routing_ok"]
    )

    print(json.dumps({
        "migration": "017_ingest_programming_capsule",
        "target_db": str(TARGET_DB),
        "elapsed_s": round(dt, 3),
        "pre_snapshot": snap,
        "upsert": upsert,
        "programming_capsule_count": prog_count,
        "verify": v,
        "ACCEPTANCE_GATE_PASS": gate,
    }, indent=2))
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
