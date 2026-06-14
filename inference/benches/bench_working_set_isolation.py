"""
2.0.5 — bench_working_set_isolation.

Two independent working sets in same DB. Edges inserted into A must not
appear in unify() against B and vice versa. Leak count must == 0.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="b205wsi_"))
    os.environ["WORKMEM_DB_PATH"] = str(tmp / "workmem.db")
    os.environ["CLAUDECODE_DB_PATH"] = str(tmp / "cc.db")
    os.environ["OPERATOR_TRACE_DIR"] = str(tmp / "traces")
    Path(os.environ["OPERATOR_TRACE_DIR"]).mkdir(parents=True, exist_ok=True)

    mig = Path("/home/timbushnell/projectbrain_dev/migrations/009_workmem_init.sql").read_text()
    sqlite3.connect(os.environ["WORKMEM_DB_PATH"]).executescript(mig)

    from inference import working_memory as wm
    from inference import unify as U

    ws_a = wm.create("p205_iso_a", "A", "phase_2_0_5", ttl_seconds=600)
    ws_b = wm.create("p205_iso_b", "B", "phase_2_0_5", ttl_seconds=600)

    a_edges = [
        (100, "rel_a", 200, "asserted", 0.9, "{}"),
        (100, "rel_a", 201, "asserted", 0.8, "{}"),
        (101, "rel_b", 202, "asserted", 0.7, "{}"),
    ]
    b_edges = [
        (300, "rel_a", 400, "asserted", 0.9, "{}"),
        (300, "rel_a", 401, "asserted", 0.8, "{}"),
        (301, "rel_b", 402, "asserted", 0.7, "{}"),
    ]

    with sqlite3.connect(wm.WORKMEM_DB_PATH) as c:
        for ws, edges in ((ws_a, a_edges), (ws_b, b_edges)):
            for s, p, o, t, conf, prov in edges:
                c.execute(
                    "INSERT INTO working_set_edges "
                    "(working_set_id,subject_id,predicate,object_id,"
                    " local_truth,local_confidence,provenance) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (ws, s, p, o, t, conf, prov),
                )
        c.commit()

    a_objs = {200, 201, 202}
    b_objs = {400, 401, 402}

    out_a = U.unify([("?X", "*", "?Y")], ws_a, k=64)
    out_b = U.unify([("?X", "*", "?Y")], ws_b, k=64)

    a_seen = {b.bindings["?Y"] for b in out_a}
    b_seen = {b.bindings["?Y"] for b in out_b}

    a_leak = a_seen & b_objs
    b_leak = b_seen & a_objs
    leak_count = len(a_leak) + len(b_leak)

    a_complete = a_seen == a_objs
    b_complete = b_seen == b_objs

    passed = leak_count == 0 and a_complete and b_complete

    print(json.dumps({
        "bench": "working_set_isolation",
        "ts": int(time.time()),
        "a_seen": sorted(a_seen),
        "b_seen": sorted(b_seen),
        "a_leak_into_b": sorted(a_leak),
        "b_leak_into_a": sorted(b_leak),
        "leak_count": leak_count,
        "a_complete": a_complete,
        "b_complete": b_complete,
        "BENCH_PASS": passed,
    }, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
