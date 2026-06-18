"""Apply 016_execution_traces_verification_bundle.sql to ~/claudecode.db.

Idempotent: skips ADD COLUMN for columns already present (SQLite ALTER limitation).
Verifies acceptance gate:
  - 14 new columns present on execution_traces
  - idx_traces_verdict present
  - pre-existing row count preserved
  - new columns all NULL on pre-existing rows
  - resonance_v11.db untouched (size + mtime unchanged)
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home()
TARGET_DB = HOME / "claudecode.db"
SUBSTRATE_DB = HOME / "resonance_v11.db"
SQL_PATH = Path(__file__).with_name("016_execution_traces_verification_bundle.sql")

NEW_COLUMNS = [
    "parse_ok", "lint_ok", "typecheck_ok", "import_resolution_ok",
    "runtime_executed", "runtime_exit_code", "runtime_exception_type",
    "tests_present", "tests_run", "tests_passed", "tests_failed",
    "memory_mb", "risks_detected_json", "verdict",
]
NEW_INDEX = "idx_traces_verdict"


def _column_set(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _substrate_signature() -> tuple[int, float] | None:
    if not SUBSTRATE_DB.exists():
        return None
    st = SUBSTRATE_DB.stat()
    return (st.st_size, st.st_mtime)


def _apply_idempotent(sql: str) -> dict:
    with sqlite3.connect(TARGET_DB) as conn:
        present = _column_set(conn, "execution_traces")
        skipped = []
        for col in NEW_COLUMNS:
            if col in present:
                # neutralize the ALTER for this column
                # match either of the supported column types
                for t in ("INTEGER", "REAL", "TEXT"):
                    needle = f"ALTER TABLE execution_traces ADD COLUMN {col:<23}{t};"
                    if needle in sql:
                        sql = sql.replace(
                            needle,
                            f"-- skipped: execution_traces.{col} already present",
                        )
                        skipped.append(col)
                        break
        conn.executescript(sql)
    return {"skipped_alters": skipped}


def _verify(pre_row_count: int) -> dict:
    with sqlite3.connect(TARGET_DB) as conn:
        cols = _column_set(conn, "execution_traces")
        idx = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
            (NEW_INDEX,),
        ).fetchone())
        row_count = conn.execute(
            "SELECT COUNT(*) FROM execution_traces"
        ).fetchone()[0]
        # All new cols should be NULL on every pre-existing row.
        null_check_clauses = " AND ".join(f"{c} IS NULL" for c in NEW_COLUMNS)
        all_null_count = conn.execute(
            f"SELECT COUNT(*) FROM execution_traces WHERE {null_check_clauses}"
        ).fetchone()[0]
    missing = sorted(c for c in NEW_COLUMNS if c not in cols)
    return {
        "columns_present": not missing,
        "missing_columns": missing,
        "idx_traces_verdict_present": idx,
        "row_count": row_count,
        "row_count_preserved": (row_count == pre_row_count),
        "preexisting_rows_all_null": all_null_count == row_count,
    }


def main() -> int:
    sig_before = _substrate_signature()
    with sqlite3.connect(TARGET_DB) as conn:
        pre_row_count = conn.execute(
            "SELECT COUNT(*) FROM execution_traces"
        ).fetchone()[0]

    sql = SQL_PATH.read_text()
    t0 = time.time()
    apply_result = _apply_idempotent(sql)
    dt = time.time() - t0
    v = _verify(pre_row_count)
    sig_after = _substrate_signature()
    substrate_untouched = (sig_before == sig_after)

    gate = (
        v["columns_present"]
        and v["idx_traces_verdict_present"]
        and v["row_count_preserved"]
        and v["preexisting_rows_all_null"]
        and substrate_untouched
    )

    print(json.dumps({
        "migration": "016_execution_traces_verification_bundle",
        "target_db": str(TARGET_DB),
        "elapsed_s": round(dt, 3),
        "pre_row_count": pre_row_count,
        "apply_result": apply_result,
        "verify": v,
        "substrate_untouched": substrate_untouched,
        "ACCEPTANCE_GATE_PASS": gate,
    }, indent=2))
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
