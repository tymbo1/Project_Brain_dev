"""
2.0.5 — bench_referent_stability.

Run unify() N times on identical (ws, pattern). Identity rate ≥ 0.99.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

N_RUNS = 50
TARGET = 0.99


def _canonical_binding(b) -> str:
    payload = {
        "bindings": {k: v for k, v in sorted(b.bindings.items())},
        "edge_ids": sorted([e["id"] for e in b.matched_edges]),
        "score": round(b.score, 12),
        "hit_bridge": b.hit_bridge,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def _canonical_result(out) -> str:
    return hashlib.sha256(
        ("|".join(_canonical_binding(b) for b in out)).encode()
    ).hexdigest()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="b205rs_"))
    os.environ["WORKMEM_DB_PATH"] = str(tmp / "workmem.db")
    os.environ["CLAUDECODE_DB_PATH"] = str(tmp / "cc.db")
    os.environ["OPERATOR_TRACE_DIR"] = str(tmp / "traces")
    Path(os.environ["OPERATOR_TRACE_DIR"]).mkdir(parents=True, exist_ok=True)

    mig = Path("/home/timbushnell/projectbrain_dev/migrations/009_workmem_init.sql").read_text()
    conn = sqlite3.connect(os.environ["WORKMEM_DB_PATH"])
    conn.executescript(mig)
    conn.close()

    from inference import working_memory as wm
    from inference import unify as U

    ws_id = wm.create("p205_rs", "stable_q", "phase_2_0_5", ttl_seconds=600)
    edges = [
        (1, "rel_a", 10, "asserted", 0.9, "{}"),
        (1, "rel_a", 11, "asserted", 0.8, "{}"),
        (1, "rel_a", 12, "asserted", 0.7, "{}"),
        (2, "rel_a", 20, "asserted", 0.6, "{}"),
        (2, "rel_b", 21, "asserted", 0.5, "{}"),
        (3, "rel_a", 30, "asserted", 0.4, '{"is_bridge":true,"domain_purity":"compatible_bridge"}'),
    ]
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

    pattern = [("?X", "rel_a", "?Y")]
    hashes = []
    for _ in range(N_RUNS):
        out = U.unify(pattern, ws_id, k=8)
        hashes.append(_canonical_result(out))

    most = max(set(hashes), key=hashes.count)
    identity = hashes.count(most) / N_RUNS
    passed = identity >= TARGET

    print(json.dumps({
        "bench": "referent_stability",
        "ts": int(time.time()),
        "n_runs": N_RUNS,
        "identity_rate": identity,
        "target": TARGET,
        "unique_hashes": len(set(hashes)),
        "BENCH_PASS": passed,
    }, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
