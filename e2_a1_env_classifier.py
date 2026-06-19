"""e2_a1_env_classifier.py — classify codeunits.environment for NULL-env rows.

Deterministic chain:
    ast.parse       → python
    bash -n         → bash
    sqlite3 EXPLAIN → sql
    json.loads      → json
    else            → unknown

Two-phase like apply_promotions:
    --dry-run (default): scan, classify, write preview JSON, no mutation
    --apply              : write classification back to codeunits.environment

Idempotent: re-running --apply is safe (only changes NULL → non-NULL).
Never overwrites an existing non-NULL environment.

After --apply, run wide_sweep_e1.py against the newly-tagged python slice
to re-verify §9 evidence (the verified_runtime claim predates env-filter).
"""
from __future__ import annotations

import argparse
import ast
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = Path.home()
SELYRIONCODE_DB = HOME / "selyrioncode.db"
PREVIEW = Path("/tmp/e2_a1_env_classifier_preview.json")

SQL_KEYWORDS = (
    "select ", "insert into", "update ", "delete from", "create table",
    "create index", "create view", "alter table", "drop table", "drop index",
    "with ", "pragma ", "explain ",
)


def _try_python(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except (SyntaxError, ValueError):
        return False


def _try_bash(code: str) -> bool:
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(code)
        p = f.name
    try:
        r = subprocess.run(
            ["bash", "-n", p],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False
    finally:
        try:
            Path(p).unlink()
        except OSError:
            pass


def _try_sql(code: str) -> bool:
    low = code.lower().lstrip()
    if not any(low.startswith(k) or ("\n" + k) in ("\n" + low) for k in SQL_KEYWORDS):
        return False
    try:
        con = sqlite3.connect(":memory:")
        con.execute("EXPLAIN " + code.split(";")[0].strip())
        con.close()
        return True
    except sqlite3.Error:
        return False


def _try_json(code: str) -> bool:
    s = code.strip()
    if not s or s[0] not in "{[":
        return False
    try:
        json.loads(s)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def classify(code: str) -> str:
    if not code or not code.strip():
        return "unknown"
    if _try_python(code):
        return "python"
    if _try_json(code):
        return "json"
    if _try_sql(code):
        return "sql"
    if _try_bash(code):
        return "bash"
    return "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write classification to codeunits.environment")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    t0 = time.time()
    with sqlite3.connect(SELYRIONCODE_DB) as c:
        q = ("SELECT id, truth_state, parsed_code, raw_input "
             "FROM codeunits WHERE environment IS NULL")
        if args.limit:
            q += f" LIMIT {args.limit}"
        rows = c.execute(q).fetchall()

    by_state: dict[str, dict[str, int]] = {}
    classifications: list[tuple[str, str]] = []
    for cu_id, truth, parsed, raw in rows:
        code = parsed or raw or ""
        env = classify(code)
        classifications.append((cu_id, env))
        by_state.setdefault(truth or "_", {})
        by_state[truth or "_"][env] = by_state[truth or "_"].get(env, 0) + 1

    totals: dict[str, int] = {}
    for st, h in by_state.items():
        for env, n in h.items():
            totals[env] = totals.get(env, 0) + n

    preview = {
        "scanned": len(rows),
        "elapsed_s": round(time.time() - t0, 2),
        "totals_by_env": totals,
        "by_state": by_state,
        "applied": False,
    }

    if args.apply:
        with sqlite3.connect(SELYRIONCODE_DB) as c:
            c.execute("BEGIN IMMEDIATE")
            for cu_id, env in classifications:
                c.execute(
                    "UPDATE codeunits SET environment=? "
                    "WHERE id=? AND environment IS NULL",
                    (env, cu_id),
                )
            c.commit()
        preview["applied"] = True
        preview["apply_elapsed_s"] = round(time.time() - t0, 2)

    PREVIEW.write_text(json.dumps(preview, indent=2))
    print(json.dumps(preview, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
