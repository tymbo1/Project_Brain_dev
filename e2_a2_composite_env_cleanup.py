"""e2_a2_composite_env_cleanup.py — collapse composite env tags to single language.

Target rows: codeunits.environment containing '|' or '/' separators
(e.g. 'python|bash', 'python|bash|sql|other', 'python/bash').

These are ingestor "could not decide" tags. Re-classify each row using
the deterministic chain from e2_a1_env_classifier.classify, then
overwrite the composite tag with the single-language verdict.

Two-phase like e2_a1: --dry-run (default) / --apply.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home()
SELYRIONCODE_DB = HOME / "selyrioncode.db"
PREVIEW = Path("/tmp/e2_a2_composite_env_preview.json")

sys.path.insert(0, str(Path(__file__).parent))
from e2_a1_env_classifier import classify


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    with sqlite3.connect(SELYRIONCODE_DB) as c:
        rows = c.execute(
            "SELECT id, truth_state, environment, parsed_code, raw_input "
            "FROM codeunits "
            "WHERE environment LIKE '%|%' OR environment LIKE '%/%'"
        ).fetchall()

    transitions: list[tuple[str, str, str, str]] = []
    by_env: dict[str, dict[str, int]] = {}
    for cu_id, truth, old_env, parsed, raw in rows:
        code = parsed or raw or ""
        new_env = classify(code)
        transitions.append((cu_id, truth, old_env, new_env))
        key = f"{old_env} -> {new_env}"
        by_env.setdefault(truth or "_", {})
        by_env[truth or "_"][key] = by_env[truth or "_"].get(key, 0) + 1

    totals: dict[str, int] = {}
    for st, h in by_env.items():
        for k, n in h.items():
            totals[k] = totals.get(k, 0) + n

    preview = {
        "scanned": len(rows),
        "elapsed_s": round(time.time() - t0, 2),
        "transitions": totals,
        "by_state": by_env,
        "applied": False,
    }

    if args.apply:
        with sqlite3.connect(SELYRIONCODE_DB) as c:
            c.execute("BEGIN IMMEDIATE")
            for cu_id, _truth, _old, new_env in transitions:
                c.execute(
                    "UPDATE codeunits SET environment=? WHERE id=?",
                    (new_env, cu_id),
                )
            c.commit()
        preview["applied"] = True
        preview["apply_elapsed_s"] = round(time.time() - t0, 2)

    PREVIEW.write_text(json.dumps(preview, indent=2))
    print(json.dumps(preview, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
