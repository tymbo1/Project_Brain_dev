"""
Phase 2.0.4 acceptance gate — operator trace format + storage.

Pass criteria (Q-2.0.J + Q-2.0.K + K guardrail):
1. Mirror schema exists in claudecode.db.operator_runs.
2. Summary record is emitted for every operator call (dispatcher + unify),
   including rejected calls (unknown / disabled).
3. Mirror summary is COMPACT — no full input/output blobs, no decisions[] array.
4. Full JSONL is ABSENT when OPERATOR_TRACE_FULL_ENABLED unset/0.
5. Full JSONL is PRESENT when OPERATOR_TRACE_FULL_ENABLED=1.
6. Replay from a full record reproduces the same outcome (canonical hash).
7. sweep_jsonl removes files older than retention_days.
8. Substrate untouched. Trace modules do not name resonance_v11.
9. 2.0.1 + 2.0.3 acceptance gates still pass post-wiring.

Run:
    python3 inference/run_phase_2_0_4_acceptance.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RESONANCE_DB = "/home/timbushnell/resonance_v11.db"


def _check(label, ok, detail=""):
    return {"check": label, "pass": bool(ok), "detail": detail}


def _substrate_fingerprint():
    st = os.stat(RESONANCE_DB)
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _canonical_hash(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


def main() -> int:
    results = []
    pre = _substrate_fingerprint()

    # isolate trace + claudecode for this run so prior history doesn't pollute
    tmp_root = Path(tempfile.mkdtemp(prefix="p20_4_"))
    trace_dir = tmp_root / "traces"
    trace_dir.mkdir()
    cc_path = tmp_root / "claudecode.db"

    os.environ["OPERATOR_TRACE_DIR"] = str(trace_dir)
    os.environ["CLAUDECODE_DB_PATH"] = str(cc_path)
    os.environ.pop("OPERATOR_TRACE_FULL_ENABLED", None)

    # import after env set so modules pick up overrides
    if "inference.operator_trace" in sys.modules:
        importlib.reload(sys.modules["inference.operator_trace"])
    if "inference.operator_dispatcher" in sys.modules:
        importlib.reload(sys.modules["inference.operator_dispatcher"])
    if "inference.unify" in sys.modules:
        importlib.reload(sys.modules["inference.unify"])

    from inference import operator_trace as OT
    from inference.operator_dispatcher import (
        OperatorDisabled,
        UnknownOperator,
        dispatch,
    )
    from inference import unify as U
    from inference import working_memory as wm

    # 1. schema exists after first emit
    try:
        OT.emit("noop_passthrough", inputs={"probe": 1},
                outputs={"echo": {"probe": 1}}, outcome="ok",
                duration_ms=0.1)
        with sqlite3.connect(str(cc_path)) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='operator_runs'"
            ).fetchone()
        results.append(_check("mirror_schema_exists",
                              row is not None, str(row)))
    except Exception as exc:
        results.append(_check("mirror_schema_exists", False, repr(exc)))

    # 2a. dispatcher emits on success
    try:
        n_before = _count_runs(cc_path)
        r = dispatch("noop_passthrough", {"x": 7})
        n_after = _count_runs(cc_path)
        results.append(_check("dispatch_success_emits",
                              n_after == n_before + 1 and r.outcome == "ok",
                              f"delta={n_after - n_before}"))
    except Exception as exc:
        results.append(_check("dispatch_success_emits", False, repr(exc)))

    # 2b. dispatcher emits on unknown
    try:
        n_before = _count_runs(cc_path)
        try:
            dispatch("nonexistent_op", {})
            raised = False
        except UnknownOperator:
            raised = True
        n_after = _count_runs(cc_path)
        last = _latest_run(cc_path)
        ok = (raised and n_after == n_before + 1
              and last["outcome"] == "rejected_unknown")
        results.append(_check("dispatch_unknown_emits", ok,
                              f"raised={raised} outcome={last['outcome']}"))
    except Exception as exc:
        results.append(_check("dispatch_unknown_emits", False, repr(exc)))

    # 2c. dispatcher emits on disabled
    try:
        n_before = _count_runs(cc_path)
        try:
            dispatch("unify_pattern", {})
            raised = False
        except OperatorDisabled:
            raised = True
        n_after = _count_runs(cc_path)
        last = _latest_run(cc_path)
        ok = (raised and n_after == n_before + 1
              and last["outcome"] == "rejected_disabled")
        results.append(_check("dispatch_disabled_emits", ok,
                              f"raised={raised} outcome={last['outcome']}"))
    except Exception as exc:
        results.append(_check("dispatch_disabled_emits", False, repr(exc)))

    # 2d. unify emits its own trace
    try:
        ws_id = wm.create("p204_unify", "q", "phase_2_0_4", ttl_seconds=600)
        with sqlite3.connect(wm.WORKMEM_DB_PATH) as conn:
            conn.execute(
                "INSERT INTO working_set_edges "
                "(working_set_id,subject_id,predicate,object_id,"
                " local_truth,local_confidence,provenance) VALUES "
                "(?,?,?,?,?,?,?)",
                (ws_id, 1, "rel", 2, "asserted", 0.9, "{}"),
            )
            conn.commit()
        n_before = _count_runs(cc_path)
        out = U.unify([(1, "rel", "?Y")], ws_id)
        n_after = _count_runs(cc_path)
        last = _latest_run(cc_path)
        ok = (n_after == n_before + 1 and last["operator"] == "unify"
              and last["working_set_id"] == ws_id
              and len(out) == 1)
        results.append(_check("unify_emits", ok,
                              f"delta={n_after - n_before} op={last['operator']}"))
    except Exception as exc:
        results.append(_check("unify_emits", False, repr(exc)))

    # 3. summary is compact — no decisions[], no full blobs in mirror
    try:
        last = _latest_run(cc_path)
        summary = json.loads(last["summary"])
        keys = set(summary.keys())
        # must NOT contain heavy payload keys
        forbidden = {"decisions", "inputs_full", "outputs_full",
                     "decisions_full"}
        # may contain input_keys, output_keys, decision_count but not arrays
        no_heavy = not (forbidden & keys)
        no_full_blob = isinstance(summary.get("input_keys"), list) \
            and isinstance(summary.get("output_keys"), list) \
            and isinstance(summary.get("decision_count"), int)
        results.append(_check("mirror_summary_compact",
                              no_heavy and no_full_blob,
                              f"keys={sorted(keys)}"))
    except Exception as exc:
        results.append(_check("mirror_summary_compact", False, repr(exc)))

    # 4. full JSONL absent when flag off
    try:
        files = list(trace_dir.glob("operator_trace_*.jsonl"))
        results.append(_check("jsonl_absent_when_flag_off",
                              len(files) == 0,
                              f"files={[f.name for f in files]}"))
    except Exception as exc:
        results.append(_check("jsonl_absent_when_flag_off", False, repr(exc)))

    # 5. full JSONL present when flag on
    try:
        os.environ["OPERATOR_TRACE_FULL_ENABLED"] = "1"
        full_tid = OT.emit("noop_passthrough",
                           inputs={"y": 42}, outputs={"echo": {"y": 42}},
                           outcome="ok", duration_ms=0.2,
                           decisions=[{"step": 1, "kind": "echo"}])
        files = list(trace_dir.glob("operator_trace_*.jsonl"))
        present = any(full_tid in f.read_text() for f in files)
        results.append(_check("jsonl_present_when_flag_on",
                              present and len(files) >= 1,
                              f"files={[f.name for f in files]} "
                              f"full_tid={full_tid}"))
    except Exception as exc:
        results.append(_check("jsonl_present_when_flag_on", False, repr(exc)))

    # 6. replay from full record reproduces outcome (canonical hash)
    try:
        rec = OT.read_full(full_tid)
        # replay: run the same operator with recorded inputs, compare outputs
        replay_out = dispatch("noop_passthrough", rec["inputs"]).output
        original_hash = _canonical_hash(rec["outputs"])
        replay_hash = _canonical_hash(replay_out)
        results.append(_check("replay_canonical_match",
                              original_hash == replay_hash,
                              f"orig={original_hash[:8]} "
                              f"replay={replay_hash[:8]}"))
    except Exception as exc:
        results.append(_check("replay_canonical_match", False, repr(exc)))

    # 7. sweep removes old files
    try:
        old_day = (dt.date.today() - dt.timedelta(days=40)).isoformat()
        old_file = trace_dir / f"operator_trace_{old_day}.jsonl"
        old_file.write_text('{"trace_id":"tr.old"}\n')
        sweep = OT.sweep_jsonl(retention_days=30)
        still = old_file.exists()
        ok = not still and old_file.name in sweep["removed"]
        results.append(_check("sweep_removes_old", ok,
                              f"sweep={sweep} still={still}"))
    except Exception as exc:
        results.append(_check("sweep_removes_old", False, repr(exc)))

    # 8a. substrate untouched
    post = _substrate_fingerprint()
    results.append(_check("substrate_untouched", pre == post,
                          f"pre={pre} post={post}"))

    # 8b. operator_trace.py does not name substrate
    src = open(OT.__file__, "rb").read()
    results.append(_check("trace_module_does_not_name_substrate",
                          b"resonance_v11" not in src,
                          OT.__file__))

    # 9a. 2.0.1 gate still passes (subprocess, fresh env)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "inference.run_phase_2_0_1_acceptance"],
            capture_output=True, text=True, timeout=60,
        )
        results.append(_check("regression_2_0_1", r.returncode == 0,
                              f"rc={r.returncode}"))
    except Exception as exc:
        results.append(_check("regression_2_0_1", False, repr(exc)))

    # 9b. 2.0.3 gate still passes
    try:
        r = subprocess.run(
            [sys.executable, "-m", "inference.run_phase_2_0_3_acceptance"],
            capture_output=True, text=True, timeout=60,
        )
        results.append(_check("regression_2_0_3", r.returncode == 0,
                              f"rc={r.returncode}"))
    except Exception as exc:
        results.append(_check("regression_2_0_3", False, repr(exc)))

    # cleanup
    try:
        shutil.rmtree(tmp_root)
    except Exception:
        pass

    all_pass = all(r["pass"] for r in results)
    print(json.dumps({
        "ts": int(time.time()),
        "checks": results,
        "PHASE_2_0_4_PASS": all_pass,
    }, indent=2))
    return 0 if all_pass else 1


def _count_runs(cc_path) -> int:
    with sqlite3.connect(str(cc_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM operator_runs").fetchone()[0]


def _latest_run(cc_path) -> dict:
    with sqlite3.connect(str(cc_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM operator_runs ORDER BY ts DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return dict(row)


if __name__ == "__main__":
    sys.exit(main())
