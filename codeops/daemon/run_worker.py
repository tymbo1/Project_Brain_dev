"""codeops/daemon/run_worker.py — CLI for a single verifier-worker process.

Usage:
    python3 -m codeops.daemon.run_worker --worker-id w1
    python3 -m codeops.daemon.run_worker --worker-id w1 --iterations 100
    python3 -m codeops.daemon.run_worker --seed-from proposed --limit 50

One process = one worker. Run N processes in parallel for N-way parallelism.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import verifier_worker, scheduler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker-id", required=False, default=None,
                    help="unique id per worker process (default: pid)")
    ap.add_argument("--iterations", type=int, default=None,
                    help="loop iterations; default = forever")
    ap.add_argument("--idle-sleep", type=float, default=5.0)
    ap.add_argument("--lease", type=int, default=scheduler.DEFAULT_LEASE_S)
    ap.add_argument("--seed-from", default=None,
                    help="comma-sep truth_states to enqueue before running "
                         "(e.g. 'proposed,plausible')")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap on seeded candidates")
    ap.add_argument("--stats-only", action="store_true",
                    help="print queue stats and exit")
    args = ap.parse_args()

    if args.stats_only:
        print(json.dumps(scheduler.queue_stats(verifier_worker.TASK_TYPE),
                         indent=2))
        return 0

    if args.seed_from:
        states = tuple(s.strip() for s in args.seed_from.split(",") if s.strip())
        seeded = verifier_worker.seed_tasks(limit=args.limit, truth_states=states)
        print(json.dumps({"seeded": seeded}, indent=2))

    import os
    worker_id = args.worker_id or f"verifier.pid{os.getpid()}"
    result = verifier_worker.run_loop(
        worker_id=worker_id,
        iterations=args.iterations,
        idle_sleep_s=args.idle_sleep,
        lease_s=args.lease,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
