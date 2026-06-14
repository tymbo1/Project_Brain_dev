"""
Phase 2.0.5 acceptance — symbolic operator benchmark suite.

Subprocess-isolates each bench. Aggregates BENCH_SUITE_PASS. Verifies
substrate fingerprint identity. Re-runs 2.0.1 + 2.0.3 + 2.0.4 regressions.

Run:
    python3 -m inference.run_phase_2_0_5_acceptance
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

RESONANCE_DB = "/home/timbushnell/resonance_v11.db"

BENCHES = [
    "inference.benches.bench_referent_stability",
    "inference.benches.bench_unification_correctness",
    "inference.benches.bench_working_set_isolation",
    "inference.benches.bench_trace_replay",
    "inference.benches.bench_registry_dispatch",
]

REGRESSIONS = [
    "inference.run_phase_2_0_1_acceptance",
    "inference.run_phase_2_0_3_acceptance",
    "inference.run_phase_2_0_4_acceptance",
]


def _fingerprint():
    st = os.stat(RESONANCE_DB)
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _run(mod) -> dict:
    p = subprocess.run(
        [sys.executable, "-m", mod],
        capture_output=True, text=True, timeout=180,
    )
    summary = None
    try:
        for line in reversed(p.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                pass
        # Try parsing the full stdout as JSON (single JSON object printed)
        summary = json.loads(p.stdout)
    except Exception:
        summary = {"raw_stdout_tail": p.stdout[-500:],
                   "stderr_tail": p.stderr[-500:]}
    return {"module": mod, "rc": p.returncode, "summary": summary}


def main() -> int:
    pre = _fingerprint()
    results = {"benches": [], "regressions": []}

    for mod in BENCHES:
        r = _run(mod)
        results["benches"].append(r)

    for mod in REGRESSIONS:
        r = _run(mod)
        results["regressions"].append(r)

    post = _fingerprint()
    substrate_untouched = pre == post

    bench_pass = all(r["rc"] == 0 for r in results["benches"])
    regression_pass = all(r["rc"] == 0 for r in results["regressions"])
    suite_pass = bench_pass and regression_pass and substrate_untouched

    out = {
        "ts": int(time.time()),
        "substrate_fingerprint_pre": pre,
        "substrate_fingerprint_post": post,
        "substrate_untouched": substrate_untouched,
        "benches": [
            {"module": r["module"], "rc": r["rc"],
             "bench_pass": (r["summary"] or {}).get("BENCH_PASS")
             if isinstance(r["summary"], dict) else None}
            for r in results["benches"]
        ],
        "regressions": [
            {"module": r["module"], "rc": r["rc"]}
            for r in results["regressions"]
        ],
        "BENCH_SUITE_PASS": suite_pass,
    }
    # full detail
    out["detail"] = results
    print(json.dumps(out, indent=2))
    return 0 if suite_pass else 1


if __name__ == "__main__":
    sys.exit(main())
