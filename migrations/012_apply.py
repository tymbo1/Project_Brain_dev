"""Apply 012_selyrion_python_backbone.sql to ~/selyrion_python.db.

Idempotent. Verifies acceptance gate:
  - 3 schema tables + meta table present
  - indices present
  - resonance_v11.db untouched (size + mtime unchanged)
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home()
TARGET_DB = HOME / "selyrion_python.db"
SUBSTRATE_DB = HOME / "resonance_v11.db"
SQL_PATH = Path(__file__).with_name("012_selyrion_python_backbone.sql")

EXPECTED_TABLES = {
    "python_anchors",
    "python_code_units",
    "python_failure_cases",
    "python_meta",
}
EXPECTED_INDEXES = {
    "idx_py_anchors_canonical", "idx_py_anchors_subtype", "idx_py_anchors_stability",
    "idx_py_units_unit_type", "idx_py_units_truth_state", "idx_py_units_updated_at",
    "idx_py_fail_failure_type", "idx_py_fail_status", "idx_py_fail_target_unit",
}


def _substrate_signature() -> tuple[int, float] | None:
    if not SUBSTRATE_DB.exists():
        return None
    st = SUBSTRATE_DB.stat()
    return (st.st_size, st.st_mtime)


def _apply(sql: str) -> None:
    with sqlite3.connect(TARGET_DB) as conn:
        conn.executescript(sql)


def _verify() -> dict:
    with sqlite3.connect(TARGET_DB) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_py_%'"
        )}
        meta = dict(conn.execute(
            "SELECT key, value FROM python_meta"
        ).fetchall())
    return {
        "tables_present": EXPECTED_TABLES.issubset(tables),
        "indexes_present": EXPECTED_INDEXES.issubset(indexes),
        "missing_tables": sorted(EXPECTED_TABLES - tables),
        "missing_indexes": sorted(EXPECTED_INDEXES - indexes),
        "schema_version": meta.get("schema_version"),
        "scope_step": meta.get("scope_step"),
    }


def main() -> int:
    sig_before = _substrate_signature()
    sql = SQL_PATH.read_text()
    t0 = time.time()
    _apply(sql)
    dt = time.time() - t0
    v = _verify()
    sig_after = _substrate_signature()
    substrate_untouched = (sig_before == sig_after)

    gate = (
        v["tables_present"]
        and v["indexes_present"]
        and substrate_untouched
        and v["schema_version"] == "012"
    )
    print({
        "migration": "012_selyrion_python_backbone",
        "target_db": str(TARGET_DB),
        "elapsed_s": round(dt, 3),
        "verify": v,
        "substrate_untouched": substrate_untouched,
        "ACCEPTANCE_GATE_PASS": gate,
    })
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
