"""
Phase 2.0.6 — canary cycle.

Spec §9 gate:
  - bench results stable across two consecutive runs of the 2.0.5 suite
  - no regression on 1.4b canary set

Procedure:
  1. Snapshot substrate fingerprint.
  2. Run 2.0.5 suite twice (subprocess). Require BENCH_SUITE_PASS both times
     AND numeric stability across the per-bench summary metrics.
  3. Read (or run if --refresh-canary) /tmp/purity_canary_summary.json.
     Require CANARY_PASS=true.
  4. Substrate fingerprint unchanged.

Run:
    python3 -m inference.run_phase_2_0_6_acceptance
    python3 -m inference.run_phase_2_0_6_acceptance --refresh-canary
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

RESONANCE_DB = "/home/timbushnell/resonance_v11.db"
CANARY_SUMMARY = "/tmp/purity_canary_summary.json"
CANARY_DRIVER = "/home/timbushnell/projectbrain_dev/inference/run_purity_canary.py"
BENCH_DRIVER = "inference.run_phase_2_0_5_acceptance"

STABILITY_KEYS = {
    "bench_referent_stability": ["identity_rate", "unique_hashes"],
    "bench_unification_correctness": ["precision", "recall", "tp", "fp", "fn", "cases"],
    "bench_working_set_isolation": ["leak_count", "a_complete", "b_complete"],
    "bench_trace_replay": ["matches", "n"],
    "bench_registry_dispatch": ["false_positives", "false_negatives",
                                "unknown_correct", "enabled_count",
                                "disabled_count"],
}


def _fingerprint():
    st = os.stat(RESONANCE_DB)
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _run_suite() -> dict:
    p = subprocess.run(
        [sys.executable, "-m", BENCH_DRIVER],
        capture_output=True, text=True, timeout=600,
    )
    try:
        summary = json.loads(p.stdout)
    except Exception:
        summary = {"raw_stdout_tail": p.stdout[-500:],
                   "stderr_tail": p.stderr[-500:]}
    return {"rc": p.returncode, "summary": summary}


def _extract_metrics(suite_summary) -> dict:
    """Pull stability-relevant metrics from a 2.0.5 suite summary."""
    out = {}
    benches_detail = suite_summary.get("detail", {}).get("benches", [])
    for entry in benches_detail:
        mod = entry["module"].split(".")[-1]
        s = entry.get("summary") or {}
        keys = STABILITY_KEYS.get(mod, [])
        out[mod] = {k: s.get(k) for k in keys}
        out[mod]["_BENCH_PASS"] = s.get("BENCH_PASS")
    return out


def _stability_diff(m1, m2) -> list[dict]:
    diffs = []
    for mod, vals in m1.items():
        other = m2.get(mod, {})
        for k, v in vals.items():
            ov = other.get(k)
            if v != ov:
                diffs.append({"bench": mod, "key": k, "run1": v, "run2": ov})
    return diffs


def _load_or_refresh_canary(refresh: bool) -> dict:
    if refresh or not Path(CANARY_SUMMARY).exists():
        p = subprocess.run(
            [sys.executable, CANARY_DRIVER],
            capture_output=True, text=True, timeout=900,
        )
        if p.returncode != 0 and not Path(CANARY_SUMMARY).exists():
            return {"error": "canary_run_failed",
                    "rc": p.returncode,
                    "stderr_tail": p.stderr[-500:]}
    try:
        return json.loads(Path(CANARY_SUMMARY).read_text())
    except Exception as exc:
        return {"error": "canary_summary_unreadable", "detail": repr(exc)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-canary", action="store_true",
                    help="Re-run 1.4b canary instead of reading cached summary")
    args = ap.parse_args()

    pre = _fingerprint()

    run1 = _run_suite()
    run2 = _run_suite()

    suite_pass_1 = run1["rc"] == 0 and run1["summary"].get("BENCH_SUITE_PASS") is True
    suite_pass_2 = run2["rc"] == 0 and run2["summary"].get("BENCH_SUITE_PASS") is True

    metrics_1 = _extract_metrics(run1["summary"]) if suite_pass_1 else {}
    metrics_2 = _extract_metrics(run2["summary"]) if suite_pass_2 else {}
    stability_diffs = _stability_diff(metrics_1, metrics_2) if suite_pass_1 and suite_pass_2 else None

    canary = _load_or_refresh_canary(args.refresh_canary)
    canary_pass = bool(canary.get("pass", {}).get("CANARY_PASS"))

    post = _fingerprint()
    substrate_untouched = pre == post

    bench_stable = (suite_pass_1 and suite_pass_2
                    and isinstance(stability_diffs, list)
                    and len(stability_diffs) == 0)
    phase_pass = bench_stable and canary_pass and substrate_untouched

    out = {
        "ts": int(time.time()),
        "substrate_fingerprint_pre": pre,
        "substrate_fingerprint_post": post,
        "substrate_untouched": substrate_untouched,
        "suite_run_1_pass": suite_pass_1,
        "suite_run_2_pass": suite_pass_2,
        "bench_stability_diffs": stability_diffs,
        "bench_stable": bench_stable,
        "canary_source": "refreshed" if args.refresh_canary else "cached",
        "canary_summary_ts": canary.get("ts"),
        "canary_pass_breakdown": canary.get("pass"),
        "canary_pass": canary_pass,
        "metrics_run_1": metrics_1,
        "metrics_run_2": metrics_2,
        "PHASE_2_0_6_PASS": phase_pass,
    }
    print(json.dumps(out, indent=2))
    return 0 if phase_pass else 1


if __name__ == "__main__":
    sys.exit(main())
