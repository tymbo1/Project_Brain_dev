"""Apply 015_python_version_library_profiles.sql to ~/selyrioncode.db.

Idempotent (CREATE TABLE IF NOT EXISTS). Verifies acceptance gate:
  - python_version_profiles table present + index
  - python_library_profiles table present + 3 indices
  - empty (no rows inserted by schema migration)
  - resonance_v11.db untouched (size + mtime unchanged)
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
SQL_PATH = Path(__file__).with_name("015_python_version_library_profiles.sql")

EXPECTED_TABLES = {"python_version_profiles", "python_library_profiles"}
EXPECTED_INDEXES = {
    "idx_py_ver_trust",
    "idx_py_lib_name", "idx_py_lib_freshness", "idx_py_lib_trust",
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
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('python_version_profiles','python_library_profiles')"
        )}
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_py_ver_%' OR name LIKE 'idx_py_lib_%'"
        )}
        ver_rows = conn.execute(
            "SELECT COUNT(*) FROM python_version_profiles"
        ).fetchone()[0]
        lib_rows = conn.execute(
            "SELECT COUNT(*) FROM python_library_profiles"
        ).fetchone()[0]
    return {
        "tables_present": EXPECTED_TABLES.issubset(tables),
        "indexes_present": EXPECTED_INDEXES.issubset(indexes),
        "missing_tables": sorted(EXPECTED_TABLES - tables),
        "missing_indexes": sorted(EXPECTED_INDEXES - indexes),
        "python_version_profiles_rows": ver_rows,
        "python_library_profiles_rows": lib_rows,
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
        and v["python_version_profiles_rows"] == 0
        and v["python_library_profiles_rows"] == 0
    )

    print(json.dumps({
        "migration": "015_python_version_library_profiles",
        "target_db": str(TARGET_DB),
        "elapsed_s": round(dt, 3),
        "verify": v,
        "substrate_untouched": substrate_untouched,
        "ACCEPTANCE_GATE_PASS": gate,
    }, indent=2))
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
