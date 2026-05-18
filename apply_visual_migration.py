#!/usr/bin/env python3
"""
apply_visual_migration.py

Applies visual_schema_migration.sql plus the ALTER TABLE column additions
that SQLite can't do conditionally in plain SQL.

Safe to re-run — skips columns that already exist.
"""
import sqlite3
from pathlib import Path

DB_PATH  = Path.home() / "resonance_v11.db"
SQL_FILE = Path(__file__).parent / "visual_schema_migration.sql"


def existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_column_if_missing(conn, table, col, definition):
    if col not in existing_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
        print(f"  + {table}.{col}")
    else:
        print(f"  ~ {table}.{col} (already exists)")


def main():
    print(f"Applying visual schema migration to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=30)

    # ── relations columns ─────────────────────────────────────────────────────
    print("\n[relations]")
    add_column_if_missing(conn, "relations", "source_dataset",        "TEXT")
    add_column_if_missing(conn, "relations", "raw_predicate",         "TEXT")
    add_column_if_missing(conn, "relations", "normalized_predicate",  "TEXT")
    add_column_if_missing(conn, "relations", "predicate_type",        "TEXT")
    add_column_if_missing(conn, "relations", "frame_id",              "INTEGER")
    add_column_if_missing(conn, "relations", "vis_timestamp",         "REAL")
    add_column_if_missing(conn, "relations", "usage_count",           "INTEGER DEFAULT 1")

    # ── anchors columns ───────────────────────────────────────────────────────
    print("\n[anchors]")
    add_column_if_missing(conn, "anchors", "modality",    "TEXT DEFAULT 'text'")
    add_column_if_missing(conn, "anchors", "instance_id", "TEXT")

    conn.commit()

    # ── SQL file (tables, predicates, indexes, backfill) ─────────────────────
    print("\n[SQL migration]")
    sql = SQL_FILE.read_text()
    # Strip verification SELECTs (after COMMIT) — executescript can't return rows
    sql_body = sql.split("-- ── Verify")[0].strip()
    conn.executescript(sql_body)

    # Run verification queries separately
    print("\n[Verification]")
    for row in conn.execute("SELECT layer, COUNT(*) FROM predicates GROUP BY layer ORDER BY layer"):
        print(f"  predicates [{row[0]}]: {row[1]}")
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('hypotheses','events','sequences') ORDER BY name"):
        print(f"  table: {row[0]}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
