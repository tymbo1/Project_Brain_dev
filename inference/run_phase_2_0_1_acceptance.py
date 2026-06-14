"""
Phase 2.0.1 acceptance gate harness.

Pass criteria (locked):
1. operator_registry table exists in resonance_v11.db.
2. noop_passthrough is declared, enabled=1, and dispatches successfully.
3. Dispatcher rejects unknown operators (UnknownOperator).
4. Dispatcher rejects enabled=0 operators (OperatorDisabled).

Run:
    python3 inference/run_phase_2_0_1_acceptance.py
Exit code 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time

from inference.operator_dispatcher import (
    DB_PATH,
    OperatorDisabled,
    OperatorResult,
    UnknownOperator,
    dispatch,
    list_registered,
)


def _check(label: str, cond: bool, detail: str = "") -> dict:
    return {"check": label, "pass": bool(cond), "detail": detail}


def main() -> int:
    results: list[dict] = []

    # 1. table exists
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='operator_registry'"
            ).fetchone()
        results.append(_check("table_exists", row is not None, str(row)))
    except Exception as exc:
        results.append(_check("table_exists", False, repr(exc)))

    # 2. noop_passthrough dispatch
    try:
        r: OperatorResult = dispatch("noop_passthrough", {"x": 1})
        ok = (
            r.outcome == "ok"
            and isinstance(r.output, dict)
            and r.output.get("echo") == {"x": 1}
        )
        results.append(
            _check("noop_dispatches", ok,
                   f"outcome={r.outcome} output={r.output}")
        )
    except Exception as exc:
        results.append(_check("noop_dispatches", False, repr(exc)))

    # 3. unknown rejected
    try:
        dispatch("definitely_not_registered_xyz", {})
        results.append(_check("unknown_rejected", False,
                              "dispatch did not raise"))
    except UnknownOperator as exc:
        results.append(_check("unknown_rejected", True, str(exc)))
    except Exception as exc:
        results.append(_check("unknown_rejected", False,
                              f"wrong exc: {exc!r}"))

    # 4. enabled=0 rejected — use a placeholder that ships disabled
    try:
        dispatch("unify_pattern", {})
        results.append(_check("disabled_rejected", False,
                              "dispatch did not raise"))
    except OperatorDisabled as exc:
        results.append(_check("disabled_rejected", True, str(exc)))
    except Exception as exc:
        results.append(_check("disabled_rejected", False,
                              f"wrong exc: {exc!r}"))

    # 5. inventory sanity (informational, not a gate)
    inv = list_registered()
    enabled = [r for r in inv if r["enabled"] == 1]

    all_pass = all(r["pass"] for r in results)
    summary = {
        "ts": int(time.time()),
        "db": DB_PATH,
        "checks": results,
        "registry_total": len(inv),
        "registry_enabled": [r["name"] for r in enabled],
        "PHASE_2_0_1_PASS": all_pass,
    }
    print(json.dumps(summary, indent=2))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
