"""Phase E1 parallel-worker stress harness.

Seeds N verify_codeunit tasks (mix of proposed + verified_runtime), launches K
workers as separate processes, polls queue until drained, summarizes:
  - throughput (tasks/sec)
  - claim collisions (should be 0)
  - drift surface (verified_runtime rows that came back failed_runtime)
  - dry-run apply_promotions: what truth_state mutations WOULD happen

Doctrine: deterministic class writes evidence freely; truth_state stays untouched
during the run. promoter runs separately, by user decision.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
CLAUDECODE_DB = HOME / "claudecode.db"
SELYRIONCODE_DB = HOME / "selyrioncode.db"
SUBSTRATE_DB = HOME / "resonance_v11.db"

sys.path.insert(0, str(Path(__file__).parent))


def _substrate_sig():
    if not SUBSTRATE_DB.exists():
        return None
    st = SUBSTRATE_DB.stat()
    return (st.st_size, st.st_mtime)


def _queue_stats(task_type: str) -> dict:
    with sqlite3.connect(CLAUDECODE_DB) as c:
        rows = c.execute(
            "SELECT status, COUNT(*) FROM daemon_work_queue "
            "WHERE task_type=? GROUP BY status",
            (task_type,),
        ).fetchall()
    return {s: n for s, n in rows}


def seed(n_proposed: int, n_verified: int) -> dict:
    from codeops.daemon import scheduler

    with sqlite3.connect(SELYRIONCODE_DB) as c:
        proposed = c.execute(
            "SELECT id FROM codeunits WHERE truth_state='proposed' "
            "AND environment='python' "
            "AND LENGTH(parsed_code) BETWEEN 20 AND 800 LIMIT ?",
            (n_proposed,),
        ).fetchall()
        verified = c.execute(
            "SELECT id FROM codeunits WHERE truth_state='verified_runtime' "
            "AND environment='python' "
            "AND LENGTH(parsed_code) BETWEEN 20 AND 800 LIMIT ?",
            (n_verified,),
        ).fetchall()
    ids = [r[0] for r in proposed] + [r[0] for r in verified]

    new = dup = 0
    for cu_id in ids:
        r = scheduler.enqueue("verify_codeunit", {"codeunit_id": cu_id},
                              lane="cpu")
        if r["inserted"]:
            new += 1
        else:
            dup += 1
    return {"proposed_seeded": len(proposed),
            "verified_seeded": len(verified),
            "new_tasks": new, "already_queued": dup,
            "seeded_codeunit_ids": ids}


def launch_workers(k: int, iterations: int, log_dir: Path) -> list:
    log_dir.mkdir(parents=True, exist_ok=True)
    procs = []
    for i in range(k):
        wid = f"stress.w{i+1}"
        logp = log_dir / f"{wid}.log"
        f = open(logp, "w")
        p = subprocess.Popen(
            [sys.executable, "-m", "codeops.daemon.run_worker",
             "--worker-id", wid,
             "--iterations", str(iterations),
             "--idle-sleep", "0.3"],
            stdout=f, stderr=subprocess.STDOUT,
            cwd=str(Path(__file__).parent),
        )
        procs.append({"id": wid, "pid": p.pid, "proc": p, "log": logp,
                      "log_fh": f})
    return procs


def wait_for_drain(task_type: str, *, timeout_s: int = 600,
                   poll_s: float = 1.5) -> dict:
    t0 = time.time()
    samples = []
    while True:
        st = _queue_stats(task_type)
        samples.append({"t": round(time.time() - t0, 2), **st})
        pending = st.get("pending", 0)
        claimed = st.get("claimed", 0)
        if pending == 0 and claimed == 0:
            return {"drained": True, "elapsed_s": round(time.time() - t0, 2),
                    "samples": samples}
        if time.time() - t0 > timeout_s:
            return {"drained": False, "elapsed_s": round(time.time() - t0, 2),
                    "samples": samples}
        time.sleep(poll_s)


def collect_results(seeded_ids: list, since: float) -> dict:
    with sqlite3.connect(CLAUDECODE_DB) as c:
        placeholders = ",".join("?" * len(seeded_ids))
        rows = c.execute(
            f"SELECT codeunit_id, verdict FROM execution_traces "
            f"WHERE started_at >= ? AND codeunit_id IN ({placeholders})",
            (since, *seeded_ids),
        ).fetchall()
        q = c.execute(
            f"SELECT status, COUNT(*) FROM daemon_work_queue "
            f"WHERE task_type='verify_codeunit' GROUP BY status"
        ).fetchall()
    verdicts = {}
    for cu_id, verdict in rows:
        verdicts.setdefault(cu_id, []).append(verdict)

    with sqlite3.connect(SELYRIONCODE_DB) as c:
        pre = {r[0]: r[1] for r in c.execute(
            f"SELECT id, truth_state FROM codeunits WHERE id IN ({placeholders})",
            seeded_ids,
        ).fetchall()}

    drift = []
    for cu_id, vs in verdicts.items():
        latest = vs[-1]
        pre_state = pre.get(cu_id)
        if pre_state == "verified_runtime" and latest in (
                "failed_runtime", "failed_static", "failed_parse"):
            drift.append({"cu_id": cu_id, "from": pre_state,
                          "latest_verdict": latest})

    return {
        "trace_rows_per_codeunit": {k: len(v) for k, v in verdicts.items()},
        "distinct_codeunits_with_evidence": len(verdicts),
        "queue_post": dict(q),
        "drift_count": len(drift),
        "drift_examples": drift[:5],
    }


def dry_run_promotions(since: float) -> dict:
    from codeops.apply_promotions import apply
    return apply(since=since, dry_run=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--iterations-per-worker", type=int, default=25)
    ap.add_argument("--seed-proposed", type=int, default=20)
    ap.add_argument("--seed-verified", type=int, default=40)
    ap.add_argument("--log-dir", default="/tmp/stress_e1_logs")
    ap.add_argument("--drain-timeout", type=int, default=600)
    args = ap.parse_args()

    sig_before = _substrate_sig()
    t_start = time.time()

    pre_q = _queue_stats("verify_codeunit")
    with sqlite3.connect(CLAUDECODE_DB) as c:
        pre_trace_count = c.execute(
            "SELECT COUNT(*) FROM execution_traces"
        ).fetchone()[0]

    seed_res = seed(args.seed_proposed, args.seed_verified)
    seeded_ids = seed_res.pop("seeded_codeunit_ids")
    pending_added = seed_res["new_tasks"]

    t_launch = time.time()
    procs = launch_workers(args.workers, args.iterations_per_worker,
                           Path(args.log_dir))
    drain = wait_for_drain("verify_codeunit", timeout_s=args.drain_timeout)
    t_drained = time.time()

    # Reap
    for p in procs:
        p["proc"].wait(timeout=60)
        p["log_fh"].close()
        p["rc"] = p["proc"].returncode

    results = collect_results(seeded_ids, since=t_launch)
    dry = dry_run_promotions(since=t_launch)

    with sqlite3.connect(CLAUDECODE_DB) as c:
        post_trace_count = c.execute(
            "SELECT COUNT(*) FROM execution_traces"
        ).fetchone()[0]

    new_traces = post_trace_count - pre_trace_count
    work_elapsed = round(t_drained - t_launch, 2)
    throughput = round(new_traces / work_elapsed, 2) if work_elapsed > 0 else None

    sig_after = _substrate_sig()
    substrate_untouched = (sig_before == sig_after)

    out = {
        "stress_e1_parallel": {
            "workers": args.workers,
            "iterations_per_worker": args.iterations_per_worker,
            "seed": seed_res,
            "pre_queue": pre_q,
            "pre_trace_count": pre_trace_count,
            "drain": {"drained": drain["drained"],
                       "wall_elapsed_s": work_elapsed,
                       "queue_samples": drain["samples"]},
            "workers_status": [
                {"id": p["id"], "pid": p["pid"], "rc": p["rc"],
                 "log": str(p["log"])} for p in procs
            ],
            "results": results,
            "new_trace_rows": new_traces,
            "throughput_traces_per_sec": throughput,
            "dry_run_promotions": dry,
            "substrate_untouched": substrate_untouched,
            "total_wall_s": round(time.time() - t_start, 2),
        }
    }
    print(json.dumps(out, indent=2, default=str))
    return 0 if substrate_untouched and drain["drained"] else 1


if __name__ == "__main__":
    sys.exit(main())
