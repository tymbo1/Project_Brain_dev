"""
2.0.5 — bench_trace_replay.

Run noop_passthrough via dispatcher with OPERATOR_TRACE_FULL_ENABLED=1.
Read full JSONL record. Replay dispatcher with recorded inputs. Verify
canonical state hash byte-equality between original and replayed outputs
across N=20 distinct payloads.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

N = 20


def _canonical_hash(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="b205tr_"))
    trace_dir = tmp / "traces"
    trace_dir.mkdir()
    os.environ["OPERATOR_TRACE_DIR"] = str(trace_dir)
    os.environ["CLAUDECODE_DB_PATH"] = str(tmp / "cc.db")
    os.environ["OPERATOR_TRACE_FULL_ENABLED"] = "1"

    for mod in ("inference.operator_trace",
                "inference.operator_dispatcher",
                "inference.unify"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])

    from inference import operator_trace as OT
    from inference.operator_dispatcher import dispatch

    def _latest_tid(cc_path):
        with sqlite3.connect(cc_path) as c:
            row = c.execute(
                "SELECT trace_id FROM operator_runs "
                "WHERE operator='noop_passthrough' AND outcome='ok' "
                "ORDER BY ts DESC, rowid DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else None

    pairs = []
    for i in range(N):
        payload = {"i": i, "tag": f"p{i}", "nested": {"k": i * 2}}
        result = dispatch("noop_passthrough", payload)
        trace_id = _latest_tid(os.environ["CLAUDECODE_DB_PATH"])
        pairs.append((trace_id, payload, result.output))

    matches = 0
    mismatches = []
    for tid, inp, original_out in pairs:
        rec = OT.read_full(tid)
        if rec is None:
            mismatches.append({"trace_id": tid, "reason": "rec_none"})
            continue
        replayed = dispatch("noop_passthrough", rec["inputs"]).output
        h_orig = _canonical_hash(original_out)
        h_replay = _canonical_hash(replayed)
        h_rec = _canonical_hash(rec["outputs"])
        if h_orig == h_replay == h_rec:
            matches += 1
        else:
            mismatches.append({
                "trace_id": tid,
                "h_orig": h_orig[:8],
                "h_replay": h_replay[:8],
                "h_rec": h_rec[:8],
            })

    passed = matches == N
    print(json.dumps({
        "bench": "trace_replay",
        "ts": int(time.time()),
        "n": N,
        "matches": matches,
        "mismatches": mismatches,
        "BENCH_PASS": passed,
    }, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
