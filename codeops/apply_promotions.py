"""codeops/apply_promotions.py — Phase D1 one-shot promoter.

Scans ~/claudecode.db.execution_traces for verification-bundle rows with a
codeunit_id, picks the LATEST verdict per codeunit, applies promotion_policy,
and updates ~/selyrioncode.db.codeunits.truth_state.

Idempotent: re-running with no new evidence is a no-op.
Read-only on substrate (resonance_v11.db is never opened).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .promotion_policy import decide

HOME = Path.home()
CLAUDECODE_DB = HOME / "claudecode.db"
SELYRIONCODE_DB = HOME / "selyrioncode.db"


def _latest_verdicts(since: float | None = None) -> dict[str, str]:
    """Return {codeunit_id: latest_verdict} from execution_traces."""
    with sqlite3.connect(CLAUDECODE_DB) as c:
        rows = c.execute(
            "SELECT codeunit_id, verdict FROM execution_traces "
            "WHERE codeunit_id IS NOT NULL AND verdict IS NOT NULL "
            + ("AND started_at >= ? " if since is not None else "")
            + "ORDER BY started_at ASC",
            (since,) if since is not None else (),
        ).fetchall()
    out: dict[str, str] = {}
    for cu_id, verdict in rows:
        out[cu_id] = verdict  # last-write-wins because ORDER BY ASC
    return out


def _current_states(ids: list[str]) -> dict[str, str | None]:
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    with sqlite3.connect(SELYRIONCODE_DB) as c:
        rows = c.execute(
            f"SELECT id, truth_state FROM codeunits WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def apply(since: float | None = None, dry_run: bool = False) -> dict:
    t0 = time.time()
    latest = _latest_verdicts(since)
    if not latest:
        return {"scanned": 0, "transitions": [], "applied": 0,
                "dry_run": dry_run, "elapsed_s": round(time.time() - t0, 3)}

    states = _current_states(list(latest))
    transitions = []
    for cu_id, verdict in latest.items():
        cur = states.get(cu_id)
        if cur is None:
            continue  # codeunit not in selyrioncode.db
        new = decide(cur, verdict)
        if new is None:
            continue
        transitions.append({
            "codeunit_id": cu_id,
            "from": cur,
            "to": new,
            "verdict": verdict,
        })

    applied = 0
    if transitions and not dry_run:
        with sqlite3.connect(SELYRIONCODE_DB) as c:
            for t in transitions:
                c.execute(
                    "UPDATE codeunits SET truth_state=? WHERE id=?",
                    (t["to"], t["codeunit_id"]),
                )
                applied += 1
            c.commit()

    return {
        "scanned": len(latest),
        "transitions": transitions,
        "applied": applied,
        "dry_run": dry_run,
        "elapsed_s": round(time.time() - t0, 3),
    }


if __name__ == "__main__":
    import json, sys
    dry = "--dry-run" in sys.argv
    print(json.dumps(apply(dry_run=dry), indent=2))
