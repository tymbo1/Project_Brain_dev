"""
2.0.5 — bench_registry_dispatch.

Iterate over all operators in operator_registry. For each:
- if enabled=1: dispatch must succeed (no rejection) for safe ops.
- if enabled=0: dispatch must raise OperatorDisabled.

Also exercise unknown operators (raise UnknownOperator).
Pass criteria: 0 false positives, 0 false negatives on the enable gate.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

UNKNOWN_OPS = ["nonexistent_op_a", "nonexistent_op_b", "xyz_unknown"]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="b205rd_"))
    os.environ["CLAUDECODE_DB_PATH"] = str(tmp / "cc.db")
    os.environ["OPERATOR_TRACE_DIR"] = str(tmp / "traces")
    Path(os.environ["OPERATOR_TRACE_DIR"]).mkdir(parents=True, exist_ok=True)

    from inference.operator_dispatcher import (
        OperatorDisabled, UnknownOperator, DB_PATH, dispatch, list_registered,
    )

    registry = list_registered(enabled_only=False)

    enabled_ops = [r for r in registry if r["enabled"] == 1]
    disabled_ops = [r for r in registry if r["enabled"] == 0]

    fp = 0  # enabled but rejected (false positive on gate)
    fn = 0  # disabled but dispatched (false negative on gate)
    details = []

    # disabled must raise
    for r in disabled_ops:
        try:
            dispatch(r["name"], {})
            fn += 1
            details.append({"op": r["name"], "expected": "rejected_disabled",
                            "got": "ok", "fault": "FN"})
        except OperatorDisabled:
            details.append({"op": r["name"], "expected": "rejected_disabled",
                            "got": "rejected_disabled", "fault": None})
        except Exception as exc:
            details.append({"op": r["name"], "expected": "rejected_disabled",
                            "got": repr(exc), "fault": "FN"})
            fn += 1

    # enabled must NOT raise OperatorDisabled (handler-bound only)
    for r in enabled_ops:
        try:
            dispatch(r["name"], {})
            details.append({"op": r["name"], "expected": "ok",
                            "got": "ok", "fault": None})
        except OperatorDisabled:
            fp += 1
            details.append({"op": r["name"], "expected": "ok",
                            "got": "rejected_disabled", "fault": "FP"})
        except UnknownOperator as exc:
            details.append({"op": r["name"], "expected": "ok",
                            "got": f"unknown_no_handler:{exc}", "fault": "FP"})
            fp += 1
        except Exception as exc:
            details.append({"op": r["name"], "expected": "ok",
                            "got": repr(exc), "fault": "FP"})
            fp += 1

    unknown_correct = 0
    for name in UNKNOWN_OPS:
        try:
            dispatch(name, {})
            details.append({"op": name, "expected": "unknown",
                            "got": "ok", "fault": "unknown_miss"})
        except UnknownOperator:
            unknown_correct += 1
            details.append({"op": name, "expected": "unknown",
                            "got": "rejected_unknown", "fault": None})
        except Exception as exc:
            details.append({"op": name, "expected": "unknown",
                            "got": repr(exc), "fault": "unknown_other"})

    passed = (fp == 0 and fn == 0
              and unknown_correct == len(UNKNOWN_OPS)
              and len(enabled_ops) >= 1)

    print(json.dumps({
        "bench": "registry_dispatch",
        "ts": int(time.time()),
        "registry_size": len(registry),
        "enabled_count": len(enabled_ops),
        "disabled_count": len(disabled_ops),
        "false_positives": fp,
        "false_negatives": fn,
        "unknown_correct": unknown_correct,
        "unknown_tested": len(UNKNOWN_OPS),
        "details": details,
        "BENCH_PASS": passed,
    }, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
