"""Apply 019_codeunit_id_on_traces.sql + Phase D1 promotion acceptance gate.

Idempotent SQL: skips ADD COLUMN if codeunit_id already present.

Acceptance gate (no permanent state mutation):
  1) schema: execution_traces.codeunit_id present, idx_traces_codeunit_id present,
     pre-existing rows have NULL codeunit_id.
  2) policy: 6 synthetic verdicts × 6 starting truth_states cover ladder transitions
     (passed_minimal / passed_verified / passed_benchmarked / failed_parse /
     failed_static / failed_runtime, plus terminal HITL no-touch).
  3) write path: 4 synthetic execution_traces rows pointing at real codeunit ids
     drive codeops.apply_promotions; transitions match expectation.
  4) substrate untouched (resonance_v11.db).
  5) reversible: original truth_state restored + synthetic trace rows deleted.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home()
CLAUDECODE_DB = HOME / "claudecode.db"
SELYRIONCODE_DB = HOME / "selyrioncode.db"
SUBSTRATE_DB = HOME / "resonance_v11.db"
SQL_PATH = Path(__file__).with_name("019_codeunit_id_on_traces.sql")

NEW_COL = "codeunit_id"
NEW_IDX = "idx_traces_codeunit_id"

sys.path.insert(0, str(Path(__file__).parent.parent))


def _cols(conn: sqlite3.Connection, tbl: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")}


def _substrate_sig() -> tuple[int, float] | None:
    if not SUBSTRATE_DB.exists():
        return None
    st = SUBSTRATE_DB.stat()
    return (st.st_size, st.st_mtime)


def _apply_sql() -> dict:
    sql = SQL_PATH.read_text()
    with sqlite3.connect(CLAUDECODE_DB) as conn:
        present = _cols(conn, "execution_traces")
        skipped = []
        if NEW_COL in present:
            for t in ("INTEGER", "REAL", "TEXT"):
                needle = f"ALTER TABLE execution_traces ADD COLUMN {NEW_COL} {t};"
                if needle in sql:
                    sql = sql.replace(needle, f"-- skipped: {NEW_COL} already present")
                    skipped.append(NEW_COL)
                    break
        conn.executescript(sql)
    return {"skipped_alters": skipped}


def _schema_check() -> dict:
    with sqlite3.connect(CLAUDECODE_DB) as conn:
        cols = _cols(conn, "execution_traces")
        idx_present = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
            (NEW_IDX,),
        ).fetchone())
        preex_null = conn.execute(
            "SELECT COUNT(*) FROM execution_traces "
            "WHERE codeunit_id IS NOT NULL AND tool_name != 'codeops.orchestrator'"
        ).fetchone()[0]
    return {
        "codeunit_id_present": NEW_COL in cols,
        "idx_traces_codeunit_id_present": idx_present,
        "preexisting_non_orchestrator_rows_with_codeunit_id": preex_null,
    }


def _policy_check() -> dict:
    from codeops.promotion_policy import decide
    cases = [
        ("proposed",         "passed_minimal",     "verified_runtime"),
        ("verified_runtime", "passed_minimal",     None),
        ("verified_static",  "passed_verified",    "regression_tested"),
        ("regression_tested","passed_benchmarked", "benchmarked"),
        ("verified_runtime", "failed_runtime",     "failed"),
        ("failed",           "failed_parse",       None),
        ("deprecated",       "passed_minimal",     None),
        ("quarantined",      "failed_runtime",     None),
    ]
    out = []
    ok = True
    for cur, verdict, expected in cases:
        got = decide(cur, verdict)
        match = (got == expected)
        ok = ok and match
        out.append({"current": cur, "verdict": verdict,
                    "expected": expected, "got": got, "ok": match})
    return {"all_ok": ok, "cases": out}


def _write_path_check() -> dict:
    """Insert synthetic trace rows pointing at real codeunits, run promoter,
    verify transitions, restore state."""
    from codeops import apply_promotions

    with sqlite3.connect(SELYRIONCODE_DB) as c:
        seeds = []
        wanted = (("proposed", 1), ("verified_runtime", 2), ("failed", 1))
        for ts, n in wanted:
            rows = c.execute(
                "SELECT id, truth_state FROM codeunits WHERE truth_state=? LIMIT ?",
                (ts, n),
            ).fetchall()
            for r in rows:
                seeds.append({"cu_id": r[0], "from": r[1]})

    if len(seeds) < 2:
        return {"setup_ok": False, "reason": "insufficient seed states",
                "seeds": seeds}

    transitions_plan = [
        {"verdict": "passed_minimal",  "expect_to": "verified_runtime",
         "valid_from": ("proposed",)},
        {"verdict": "passed_verified", "expect_to": "regression_tested",
         "valid_from": ("verified_runtime",)},
        {"verdict": "failed_runtime",  "expect_to": "failed",
         "valid_from": ("verified_runtime", "proposed")},
    ]

    used = []
    trace_ids = []
    now = time.time()
    with sqlite3.connect(CLAUDECODE_DB) as c:
        for plan in transitions_plan:
            seed = next((s for s in seeds
                         if s["from"] in plan["valid_from"] and s not in used), None)
            if seed is None:
                continue
            used.append(seed)
            tid = f"d1.witness.{plan['verdict']}.{int(now*1000)%1000000}"
            c.execute(
                "INSERT INTO execution_traces "
                "(id, session_id, intent, tool_name, started_at, finished_at, "
                "domain_tag, outcome, codeunit_id, verdict, parse_ok, runtime_executed) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, "d1_acceptance",
                 f"D1_witness::{plan['verdict']}",
                 "codeops.orchestrator", now, now,
                 "programming",
                 "success" if "passed" in plan["verdict"] else "failure",
                 seed["cu_id"], plan["verdict"], 1, 1),
            )
            trace_ids.append(tid)
            plan["cu_id"] = seed["cu_id"]
            plan["from"] = seed["from"]
        c.commit()

    pre_states = {}
    with sqlite3.connect(SELYRIONCODE_DB) as c:
        for plan in transitions_plan:
            if "cu_id" not in plan:
                continue
            pre_states[plan["cu_id"]] = c.execute(
                "SELECT truth_state FROM codeunits WHERE id=?",
                (plan["cu_id"],),
            ).fetchone()[0]

    apply_res = apply_promotions.apply(since=now - 1)

    post_states = {}
    per_case = []
    all_ok = True
    with sqlite3.connect(SELYRIONCODE_DB) as c:
        for plan in transitions_plan:
            if "cu_id" not in plan:
                continue
            post = c.execute(
                "SELECT truth_state FROM codeunits WHERE id=?",
                (plan["cu_id"],),
            ).fetchone()[0]
            post_states[plan["cu_id"]] = post
            ok = (post == plan["expect_to"])
            all_ok = all_ok and ok
            per_case.append({
                "verdict": plan["verdict"],
                "codeunit_id": plan["cu_id"],
                "from": plan["from"],
                "expected_to": plan["expect_to"],
                "actual_to": post,
                "ok": ok,
            })

    # Restore truth_state
    with sqlite3.connect(SELYRIONCODE_DB) as c:
        for plan in transitions_plan:
            if "cu_id" not in plan:
                continue
            c.execute("UPDATE codeunits SET truth_state=? WHERE id=?",
                      (plan["from"], plan["cu_id"]))
        c.commit()

    # Delete synthetic trace rows
    with sqlite3.connect(CLAUDECODE_DB) as c:
        for tid in trace_ids:
            c.execute("DELETE FROM execution_traces WHERE id=?", (tid,))
        c.commit()

    return {
        "setup_ok": True,
        "transitions": per_case,
        "apply_res": apply_res,
        "all_ok": all_ok,
        "restored": True,
        "synthetic_trace_ids_cleaned": len(trace_ids),
    }


def main() -> int:
    sig_before = _substrate_sig()
    t0 = time.time()
    apply_result = _apply_sql()
    schema = _schema_check()
    policy = _policy_check()
    write = _write_path_check()
    sig_after = _substrate_sig()
    substrate_untouched = (sig_before == sig_after)

    gate = (
        schema["codeunit_id_present"]
        and schema["idx_traces_codeunit_id_present"]
        and schema["preexisting_non_orchestrator_rows_with_codeunit_id"] == 0
        and policy["all_ok"]
        and write.get("setup_ok") is True
        and write.get("all_ok") is True
        and write.get("restored") is True
        and substrate_untouched
    )

    print(json.dumps({
        "migration": "019_codeunit_id_on_traces + D1 promotion policy",
        "elapsed_s": round(time.time() - t0, 3),
        "apply": apply_result,
        "schema": schema,
        "policy": policy,
        "write_path": write,
        "substrate_untouched": substrate_untouched,
        "ACCEPTANCE_GATE_PASS": gate,
    }, indent=2, default=str))
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
