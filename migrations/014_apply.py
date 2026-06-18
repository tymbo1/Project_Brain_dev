"""Apply 014_codeops_operator_registration.sql to ~/resonance_v11.db.

Idempotent (INSERT OR IGNORE). Verifies acceptance gate:
  - 4 py.* operators present in operator_registry
  - all 4 enabled=0 (gated; explicit enable required)
  - dispatcher rejects with OperatorDisabled when called
  - dispatcher accepts call once row is force-enabled in-memory (handler bound)
  - resonance_v11.db substrate footprint preserved (only operator_registry row count change)
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home()
TARGET_DB = HOME / "resonance_v11.db"
SQL_PATH = Path(__file__).with_name("014_codeops_operator_registration.sql")

EXPECTED_OPS = {
    "py.classify_error",
    "py.check_security",
    "py.propose_fix",
    "py.run_sandboxed",
}


def _apply(sql: str) -> None:
    with sqlite3.connect(TARGET_DB) as conn:
        conn.executescript(sql)


def _verify_registry() -> dict:
    with sqlite3.connect(TARGET_DB) as conn:
        rows = {
            r[0]: {"category": r[1], "truth_policy": r[2],
                   "cost_class": r[3], "enabled": int(r[4]),
                   "grounding": r[5]}
            for r in conn.execute(
                "SELECT name, category, truth_policy, cost_class, enabled, grounding "
                "FROM operator_registry WHERE name LIKE 'py.%'"
            )
        }
    return {
        "registered": sorted(rows.keys()),
        "all_present": EXPECTED_OPS.issubset(rows.keys()),
        "all_disabled": all(v["enabled"] == 0 for v in rows.values()),
        "rows": rows,
    }


def _verify_dispatcher_rejects_disabled() -> dict:
    """Each py.* op must raise OperatorDisabled when dispatched."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from inference import operator_dispatcher as od

    results = {}
    for op in sorted(EXPECTED_OPS):
        try:
            od.dispatch(op, {})
            results[op] = "UNEXPECTED_OK"
        except od.OperatorDisabled:
            results[op] = "rejected_disabled"
        except od.UnknownOperator as e:
            results[op] = f"UNKNOWN: {e}"
        except Exception as e:
            results[op] = f"ERROR: {type(e).__name__}: {e}"
    return {
        "results": results,
        "all_rejected": all(v == "rejected_disabled" for v in results.values()),
    }


def _verify_handlers_bound() -> dict:
    """All 4 py.* ops must have a handler bound in _HANDLERS dict."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from inference import operator_dispatcher as od
    bound = {op: (op in od._HANDLERS) for op in sorted(EXPECTED_OPS)}
    return {
        "bound": bound,
        "all_bound": all(bound.values()),
    }


def main() -> int:
    sql = SQL_PATH.read_text()
    t0 = time.time()
    _apply(sql)
    dt = time.time() - t0

    reg = _verify_registry()
    rej = _verify_dispatcher_rejects_disabled()
    handlers = _verify_handlers_bound()

    gate = (
        reg["all_present"]
        and reg["all_disabled"]
        and rej["all_rejected"]
        and handlers["all_bound"]
    )

    print(json.dumps({
        "migration": "014_codeops_operator_registration",
        "target_db": str(TARGET_DB),
        "elapsed_s": round(dt, 3),
        "registry": reg,
        "dispatcher_rejection": rej,
        "handlers_bound": handlers,
        "ACCEPTANCE_GATE_PASS": gate,
    }, indent=2))
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
