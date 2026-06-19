"""wide_sweep_e1.py — drain all python verified_runtime codeunits.

Seeds verify_codeunit tasks for every selyrioncode.codeunits row where
truth_state='verified_runtime' AND environment='python', launches K
parallel workers, polls until drain, writes dry-run promotion preview.

Apply step is SEPARATE (codeops.apply_promotions --apply). This script
never mutates selyrioncode.codeunits.truth_state.

Run:
    nohup python3 wide_sweep_e1.py --workers 12 > /tmp/wide_sweep_e1.log 2>&1 &
    cat /tmp/wide_sweep_e1_progress.json  # live progress
"""
from __future__ import annotations

import argparse
import json
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

PROGRESS = Path("/tmp/wide_sweep_e1_progress.json")


def _substrate_sig():
    if not SUBSTRATE_DB.exists():
        return None
    st = SUBSTRATE_DB.stat()
    return (st.st_size, st.st_mtime)


def _queue_stats(task_type):
    with sqlite3.connect(CLAUDECODE_DB) as c:
        rows = c.execute(
            "SELECT status, COUNT(*) FROM daemon_work_queue "
            "WHERE task_type=? GROUP BY status",
            (task_type,),
        ).fetchall()
    return {s: n for s, n in rows}


def _write(d):
    PROGRESS.write_text(json.dumps(d, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--iterations-per-worker", type=int, default=500)
    ap.add_argument("--log-dir", default="/tmp/wide_sweep_e1_logs")
    ap.add_argument("--poll-s", type=float, default=15.0)
    args = ap.parse_args()

    sig_before = _substrate_sig()
    t_start = time.time()

    from codeops.daemon import verifier_worker
    from codeops import apply_promotions

    _write({"phase": "seeding", "started_at_iso": time.strftime("%Y-%m-%d %H:%M:%S")})

    seed = verifier_worker.seed_tasks(
        truth_states=("verified_runtime",), limit=None)
    t_launch = time.time()

    _write({
        "phase": "launching_workers",
        "seed": seed,
        "elapsed_s": round(t_launch - t_start, 2),
    })

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    procs = []
    for i in range(args.workers):
        wid = f"sweep.w{i+1:02d}"
        logp = log_dir / f"{wid}.log"
        f = open(logp, "w")
        p = subprocess.Popen(
            [sys.executable, "-m", "codeops.daemon.run_worker",
             "--worker-id", wid,
             "--iterations", str(args.iterations_per_worker),
             "--idle-sleep", "0.5"],
            stdout=f, stderr=subprocess.STDOUT,
            cwd=str(Path(__file__).parent),
        )
        procs.append({"id": wid, "pid": p.pid, "proc": p, "log_fh": f,
                      "log": str(logp)})

    last_done = 0
    samples = []
    while True:
        st = _queue_stats("verify_codeunit")
        pending = st.get("pending", 0)
        claimed = st.get("claimed", 0)
        done = st.get("done", 0)
        elapsed = time.time() - t_launch
        delta_done = done - last_done
        rate = delta_done / args.poll_s if last_done else 0.0
        last_done = done

        sample = {
            "t": round(elapsed, 1),
            "pending": pending,
            "claimed": claimed,
            "done": done,
            "rate_traces_per_s": round(rate, 3),
        }
        samples.append(sample)

        _write({
            "phase": "draining",
            "seed": seed,
            "workers": args.workers,
            "elapsed_s": round(elapsed, 1),
            "current": sample,
            "samples_tail": samples[-10:],
        })

        if pending == 0 and claimed == 0:
            break
        time.sleep(args.poll_s)

    t_drained = time.time()

    for p in procs:
        try:
            p["proc"].wait(timeout=120)
        except subprocess.TimeoutExpired:
            p["proc"].kill()
        p["log_fh"].close()
        p["rc"] = p["proc"].returncode

    dry = apply_promotions.apply(since=t_launch, dry_run=True)

    transitions = dry.get("transitions", []) if isinstance(dry, dict) else []
    promotion_hist = {}
    for t in transitions:
        key = f"{t['from']} -> {t['to']} ({t['verdict']})"
        promotion_hist[key] = promotion_hist.get(key, 0) + 1

    sig_after = _substrate_sig()
    substrate_untouched = (sig_before == sig_after)

    out = {
        "phase": "DONE",
        "started_at_iso": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(t_start)),
        "completed_at_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "workers": args.workers,
        "iterations_per_worker": args.iterations_per_worker,
        "seed": seed,
        "drain_wall_s": round(t_drained - t_launch, 2),
        "throughput_traces_per_s": round(
            seed.get("enqueued", 0) / max(1, t_drained - t_launch), 3),
        "workers_rc": [p["rc"] for p in procs],
        "samples_count": len(samples),
        "samples_tail": samples[-10:],
        "dry_run_promotions": {
            "scanned": dry.get("scanned"),
            "transitions_total": len(transitions),
            "histogram": promotion_hist,
        },
        "substrate_untouched": substrate_untouched,
        "total_wall_s": round(time.time() - t_start, 2),
    }
    _write(out)
    print(json.dumps(out, indent=2, default=str))
    return 0 if substrate_untouched else 1


if __name__ == "__main__":
    sys.exit(main())
