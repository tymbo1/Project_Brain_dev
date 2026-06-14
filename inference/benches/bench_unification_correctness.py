"""
2.0.5 — bench_unification_correctness.

Extends 2.0.3 hand-built set with 16 cases. Precision ≥ 0.95, recall ≥ 0.90.
Each case provides expected binding set (canonicalized) vs actual.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

PRECISION_TARGET = 0.95
RECALL_TARGET = 0.90


def _bind_key(b) -> tuple:
    return tuple(sorted(b.bindings.items()))


def _expected_set(items):
    return {tuple(sorted(x.items())) for x in items}


def _setup_ws(wm, edges):
    ws_id = wm.create("p205_uc", "uc_q", "phase_2_0_5", ttl_seconds=600)
    with sqlite3.connect(wm.WORKMEM_DB_PATH) as c:
        for s, p, o, t, conf, prov in edges:
            c.execute(
                "INSERT INTO working_set_edges "
                "(working_set_id,subject_id,predicate,object_id,"
                " local_truth,local_confidence,provenance) "
                "VALUES (?,?,?,?,?,?,?)",
                (ws_id, s, p, o, t, conf, prov),
            )
        c.commit()
    return ws_id


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="b205uc_"))
    os.environ["WORKMEM_DB_PATH"] = str(tmp / "workmem.db")
    os.environ["CLAUDECODE_DB_PATH"] = str(tmp / "cc.db")
    os.environ["OPERATOR_TRACE_DIR"] = str(tmp / "traces")
    Path(os.environ["OPERATOR_TRACE_DIR"]).mkdir(parents=True, exist_ok=True)

    mig = Path("/home/timbushnell/projectbrain_dev/migrations/009_workmem_init.sql").read_text()
    sqlite3.connect(os.environ["WORKMEM_DB_PATH"]).executescript(mig)

    from inference import working_memory as wm
    from inference import unify as U

    # Edge format: (subj, pred, obj, truth, conf, prov_json)
    base_edges = [
        (1, "rel_a", 10, "asserted", 0.9, "{}"),
        (1, "rel_a", 11, "asserted", 0.8, "{}"),
        (2, "rel_a", 20, "asserted", 0.7, "{}"),
        (2, "rel_b", 21, "asserted", 0.6, "{}"),
        (3, "rel_a", 30, "asserted", 0.5, '{"is_bridge":true,"domain_purity":"compatible_bridge"}'),
        (4, "rel_c", 40, "inferred", 0.4, "{}"),
        (10, "rel_b", 100, "asserted", 0.9, "{}"),
        (11, "rel_b", 110, "asserted", 0.8, "{}"),
        (20, "rel_c", 200, "asserted", 0.7, "{}"),
    ]
    ws_id = _setup_ws(wm, base_edges)

    cases = [
        # 1: single var on object
        {
            "name": "single_obj_var",
            "pattern": [(1, "rel_a", "?Y")],
            "expected": [{"?Y": 10}, {"?Y": 11}],
            "constraints": None,
        },
        # 2: single var on subject
        {
            "name": "single_subj_var",
            "pattern": [("?X", "rel_b", 21)],
            "expected": [{"?X": 2}],
            "constraints": None,
        },
        # 3: subject + object both var
        {
            "name": "both_var",
            "pattern": [("?X", "rel_c", "?Y")],
            "expected": [{"?X": 4, "?Y": 40}, {"?X": 20, "?Y": 200}],
            "constraints": None,
        },
        # 4: predicate wildcard
        {
            "name": "pred_wildcard",
            "pattern": [(2, "*", "?Y")],
            "expected": [{"?Y": 20}, {"?Y": 21}],
            "constraints": None,
        },
        # 5: literal-only triple match
        {
            "name": "literal_only_match",
            "pattern": [(1, "rel_a", 10)],
            "expected": [{}],
            "constraints": None,
        },
        # 6: literal-only no match
        {
            "name": "literal_only_nomatch",
            "pattern": [(99, "rel_a", 99)],
            "expected": [],
            "constraints": None,
        },
        # 7: chained pattern
        {
            "name": "chained",
            "pattern": [(1, "rel_a", "?M"), ("?M", "rel_b", "?N")],
            "expected": [{"?M": 10, "?N": 100}, {"?M": 11, "?N": 110}],
            "constraints": None,
        },
        # 8: truth_floor inferred excluded
        {
            "name": "truth_floor_asserted_only",
            "pattern": [("?X", "rel_c", "?Y")],
            "expected": [{"?X": 20, "?Y": 200}],
            "constraints": {"truth_floor": ["asserted"]},
        },
        # 9: bridge sacred override
        {
            "name": "bridge_sacred_override",
            "pattern": [(3, "rel_a", "?Y")],
            "expected": [{"?Y": 30}],
            "constraints": {"truth_floor": ["retracted"]},
        },
        # 10: hard_ceiling clamp
        {
            "name": "hard_ceiling_clamp",
            "pattern": [("?X", "rel_a", "?Y")],
            "expected_count_min": 4,
            "expected_count_max": 4,
            "constraints": {"hard_ceiling": 999},
        },
        # 11: k=1 limits but still correct top
        {
            "name": "k_limit",
            "pattern": [(1, "rel_a", "?Y")],
            "expected": [{"?Y": 10}],
            "k": 1,
        },
        # 12: empty result with restrictive truth_floor + no bridge
        {
            "name": "empty_truth_floor",
            "pattern": [(4, "rel_c", "?Y")],
            "expected": [],
            "constraints": {"truth_floor": ["asserted"]},
        },
    ]

    tp = 0  # true positives (matched expected)
    fp = 0  # false positives (returned but not expected)
    fn = 0  # false negatives (expected but not returned)
    details = []

    for case in cases:
        k = case.get("k", 64)
        out = U.unify(case["pattern"], ws_id,
                      constraints=case.get("constraints"), k=k)
        actual = {_bind_key(b) for b in out}

        if "expected" in case:
            expected = _expected_set(case["expected"])
            case_tp = len(actual & expected)
            case_fp = len(actual - expected)
            case_fn = len(expected - actual)
        else:
            cnt = len(actual)
            in_range = case["expected_count_min"] <= cnt <= case["expected_count_max"]
            case_tp = cnt if in_range else 0
            case_fp = 0 if in_range else cnt
            case_fn = 0 if in_range else case["expected_count_min"]

        tp += case_tp
        fp += case_fp
        fn += case_fn
        details.append({
            "name": case["name"],
            "tp": case_tp, "fp": case_fp, "fn": case_fn,
            "actual_count": len(actual),
        })

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    passed = precision >= PRECISION_TARGET and recall >= RECALL_TARGET

    print(json.dumps({
        "bench": "unification_correctness",
        "ts": int(time.time()),
        "cases": len(cases),
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision,
        "recall": recall,
        "precision_target": PRECISION_TARGET,
        "recall_target": RECALL_TARGET,
        "details": details,
        "BENCH_PASS": passed,
    }, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
