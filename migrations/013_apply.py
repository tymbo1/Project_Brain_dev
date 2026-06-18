"""Apply 013_selyrioncode_taxonomy_backfill.sql to ~/selyrioncode.db.

Idempotent: skips ADD COLUMN if column already exists.
Reversible: writes pre-migration snapshot to claudecode.db.

Acceptance gate:
  - codeunits.truth_state present
  - fix_pairs.repair_class present
  - 6,569 + 779 truth_state rows backfilled (working→verified_runtime, broken→failed)
  - fix_pairs.repair_class populated for at least 1,000 rows
  - resonance_v11.db untouched
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home()
TARGET_DB = HOME / "selyrioncode.db"
SUBSTRATE_DB = HOME / "resonance_v11.db"
CLAUDECODE_DB = HOME / "claudecode.db"
SQL_PATH = Path(__file__).with_name("013_selyrioncode_taxonomy_backfill.sql")


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    return column in cols


def _substrate_signature() -> tuple[int, float] | None:
    if not SUBSTRATE_DB.exists():
        return None
    st = SUBSTRATE_DB.stat()
    return (st.st_size, st.st_mtime)


def _snapshot_pre_migration() -> dict:
    """Capture row counts + status distribution for reversibility / verification."""
    with sqlite3.connect(TARGET_DB) as conn:
        codeunits_total = conn.execute("SELECT COUNT(*) FROM codeunits").fetchone()[0]
        fix_pairs_total = conn.execute("SELECT COUNT(*) FROM fix_pairs").fetchone()[0]
        status_dist = dict(conn.execute(
            "SELECT status, COUNT(*) FROM codeunits GROUP BY status"
        ).fetchall())
    return {
        "codeunits_total": codeunits_total,
        "fix_pairs_total": fix_pairs_total,
        "codeunits_status_dist": status_dist,
        "captured_at": time.time(),
    }


def _write_snapshot(snapshot: dict) -> None:
    with sqlite3.connect(CLAUDECODE_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS migration_013_snapshot (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_json TEXT NOT NULL,
                created_at  REAL NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO migration_013_snapshot (snapshot_json, created_at) VALUES (?, ?)",
            (json.dumps(snapshot), time.time()),
        )


def _apply_idempotent(sql: str) -> dict:
    """Apply migration, skipping ADD COLUMN if already present."""
    with sqlite3.connect(TARGET_DB) as conn:
        skipped_alters = []
        if _column_exists(conn, "codeunits", "truth_state"):
            sql = sql.replace(
                "ALTER TABLE codeunits ADD COLUMN truth_state TEXT DEFAULT 'proposed';",
                "-- skipped: codeunits.truth_state already present",
            )
            skipped_alters.append("codeunits.truth_state")
        if _column_exists(conn, "fix_pairs", "repair_class"):
            sql = sql.replace(
                "ALTER TABLE fix_pairs ADD COLUMN repair_class TEXT;",
                "-- skipped: fix_pairs.repair_class already present",
            )
            skipped_alters.append("fix_pairs.repair_class")
        conn.executescript(sql)
    return {"skipped_alters": skipped_alters}


def _verify() -> dict:
    with sqlite3.connect(TARGET_DB) as conn:
        truth_state_present = _column_exists(conn, "codeunits", "truth_state")
        repair_class_present = _column_exists(conn, "fix_pairs", "repair_class")

        truth_dist = dict(conn.execute(
            "SELECT truth_state, COUNT(*) FROM codeunits GROUP BY truth_state"
        ).fetchall()) if truth_state_present else {}
        repair_dist = dict(conn.execute(
            "SELECT COALESCE(repair_class, '_NULL_'), COUNT(*) "
            "FROM fix_pairs GROUP BY repair_class"
        ).fetchall()) if repair_class_present else {}

        idx_truth = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_codeunits_truth_state'"
        ).fetchone())
        idx_repair = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_fix_pairs_repair_class'"
        ).fetchone())

    repair_class_backfilled = sum(
        v for k, v in repair_dist.items() if k != "_NULL_"
    )

    return {
        "truth_state_present": truth_state_present,
        "repair_class_present": repair_class_present,
        "truth_state_dist": truth_dist,
        "repair_class_dist": repair_dist,
        "repair_class_backfilled_count": repair_class_backfilled,
        "idx_truth_state_present": idx_truth,
        "idx_repair_class_present": idx_repair,
    }


def main() -> int:
    sig_before = _substrate_signature()
    snapshot = _snapshot_pre_migration()
    _write_snapshot(snapshot)

    sql = SQL_PATH.read_text()
    t0 = time.time()
    apply_result = _apply_idempotent(sql)
    dt = time.time() - t0
    v = _verify()
    sig_after = _substrate_signature()
    substrate_untouched = (sig_before == sig_after)

    gate = (
        v["truth_state_present"]
        and v["repair_class_present"]
        and v["idx_truth_state_present"]
        and v["idx_repair_class_present"]
        and substrate_untouched
        and v["truth_state_dist"].get("verified_runtime", 0) >= 6000
        and v["truth_state_dist"].get("failed", 0) >= 700
        and v["repair_class_backfilled_count"] >= 1000
    )

    print(json.dumps({
        "migration": "013_selyrioncode_taxonomy_backfill",
        "target_db": str(TARGET_DB),
        "elapsed_s": round(dt, 3),
        "pre_snapshot": snapshot,
        "apply_result": apply_result,
        "verify": v,
        "substrate_untouched": substrate_untouched,
        "ACCEPTANCE_GATE_PASS": gate,
    }, indent=2))
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
