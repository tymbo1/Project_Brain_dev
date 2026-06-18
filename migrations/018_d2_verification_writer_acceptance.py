"""Phase D2 — verification-bundle writer acceptance gate.

Drives codeops.orchestrator.run() over 5 canonical witness cases and asserts the
new execution_traces rows carry the right bundle values + §9 verdict.

Witness cases:
  W1 good       — `print(1+1)`              → parse_ok=1, runtime_executed=1, exit=0, verdict=passed_minimal
  W2 syntax     — `def x(:`                 → parse_ok=0, verdict=failed_parse
  W3 nameerror  — `print(undefined_x)`      → parse=1, runtime=1, exit≠0, exc=NameError, verdict=failed_runtime
  W4 sandbox    — `eval('1+1')`             → blocked, parse=1, runtime_executed=0, risks≠NULL, verdict=failed_static
  W5 tests      — `def test_x(): assert 1`  → parse=1, tests_present=1, runtime=1, exit=0, verdict=passed_minimal

Acceptance:
  - 5 new execution_traces rows captured
  - Each row's verdict matches expectation
  - 562 pre-existing NULL rows untouched
  - resonance_v11.db untouched
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home()
TARGET_DB = HOME / "claudecode.db"
SUBSTRATE_DB = HOME / "resonance_v11.db"

sys.path.insert(0, str(Path(__file__).parent.parent))


WITNESSES = [
    ("W1_good",      "print(1+1)",                          "passed_minimal"),
    ("W2_syntax",    "def x(:\n",                           "failed_parse"),
    ("W3_nameerror", "print(undefined_x)",                  "failed_runtime"),
    ("W4_sandbox",   "x = eval('1+1')\nprint(x)",           "failed_static"),
    ("W5_tests",     "def test_x():\n    assert 1 == 1\n",  "passed_minimal"),
]


def _substrate_sig() -> tuple[int, float] | None:
    if not SUBSTRATE_DB.exists():
        return None
    st = SUBSTRATE_DB.stat()
    return (st.st_size, st.st_mtime)


def _pre_state() -> dict:
    with sqlite3.connect(TARGET_DB) as c:
        total = c.execute("SELECT COUNT(*) FROM execution_traces").fetchone()[0]
        nulls = c.execute(
            "SELECT COUNT(*) FROM execution_traces "
            "WHERE parse_ok IS NULL AND runtime_executed IS NULL AND verdict IS NULL"
        ).fetchone()[0]
    return {"rows": total, "all_bundle_null_rows": nulls}


def _post_state(since: float) -> list[dict]:
    with sqlite3.connect(TARGET_DB) as c:
        rows = c.execute(
            "SELECT id, parse_ok, lint_ok, typecheck_ok, import_resolution_ok, "
            "runtime_executed, runtime_exit_code, runtime_exception_type, "
            "tests_present, risks_detected_json, verdict, intent "
            "FROM execution_traces WHERE started_at >= ? "
            "AND tool_name = 'codeops.orchestrator' "
            "ORDER BY started_at ASC",
            (since,),
        ).fetchall()
    cols = ("id", "parse_ok", "lint_ok", "typecheck_ok", "import_resolution_ok",
            "runtime_executed", "runtime_exit_code", "runtime_exception_type",
            "tests_present", "risks_detected_json", "verdict", "intent")
    return [dict(zip(cols, r)) for r in rows]


def main() -> int:
    from codeops import orchestrator

    sig_before = _substrate_sig()
    pre = _pre_state()
    t0 = time.time()

    invocations = []
    for label, code, expected_verdict in WITNESSES:
        r = orchestrator.run(code, lang="python", max_attempts=1,
                             original_problem=f"D2_witness::{label}")
        invocations.append({
            "label": label,
            "expected_verdict": expected_verdict,
            "status": r.get("status"),
        })

    elapsed = time.time() - t0
    rows = _post_state(t0)
    post = _pre_state()
    sig_after = _substrate_sig()

    verdict_by_label = {}
    for row in rows:
        intent = row.get("intent") or ""
        for label, _, _ in WITNESSES:
            if f"D2_witness::{label}" in intent:
                verdict_by_label[label] = row
                break

    per_case = []
    all_match = True
    for label, _, expected in WITNESSES:
        got = verdict_by_label.get(label)
        v = got["verdict"] if got else None
        ok = (v == expected)
        all_match = all_match and ok
        per_case.append({
            "label": label,
            "expected_verdict": expected,
            "actual_verdict": v,
            "verdict_ok": ok,
            "row": got,
        })

    rows_written = len(verdict_by_label)
    substrate_untouched = (sig_before == sig_after)
    pre_existing_preserved = (post["rows"] - pre["rows"] == rows_written)

    gate = (
        rows_written == len(WITNESSES)
        and all_match
        and substrate_untouched
        and pre_existing_preserved
    )

    print(json.dumps({
        "migration": "018_d2_verification_writer_acceptance",
        "target_db": str(TARGET_DB),
        "elapsed_s": round(elapsed, 3),
        "pre_state": pre,
        "post_state": post,
        "rows_written": rows_written,
        "invocations": invocations,
        "per_case": per_case,
        "substrate_untouched": substrate_untouched,
        "pre_existing_preserved": pre_existing_preserved,
        "ACCEPTANCE_GATE_PASS": gate,
    }, indent=2, default=str))
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
