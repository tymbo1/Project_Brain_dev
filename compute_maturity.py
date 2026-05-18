#!/usr/bin/env python3
"""
compute_maturity.py — Populate maturity scores for resonance_v11.db

Computes per-anchor maturity from the relations table:
    maturity = relation_count * (1 + sources*0.5) * (1 + predicates*0.3) * (1 + neighbors*0.1)

Where:
    sources    = distinct capsule_ids for outbound relations
    predicates = distinct predicates for outbound relations
    neighbors  = distinct object_ids for outbound relations

State thresholds (from resonance_ingest_v10.py):
    < 2   → draft
    < 10  → emerging
    < 50  → stable
    >= 50 → established

visible = 1 if maturity >= 2.0

Usage:
    python3 compute_maturity.py [--db ~/resonance_v11.db] [--batch 50000] [--dry-run]
"""

import sqlite3
import argparse
import time
from pathlib import Path

VISIBILITY_THRESHOLD = 2.0
BATCH_SIZE = 50_000

def compute_state(m: float) -> str:
    if m < 2:   return "draft"
    if m < 10:  return "emerging"
    if m < 50:  return "stable"
    return "established"


def run(db_path: Path, batch_size: int, dry_run: bool):
    print(f"DB: {db_path}")
    print(f"Batch size: {batch_size:,}")
    print(f"Dry run: {dry_run}")
    print()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA cache_size=-524288;")  # 512 MB cache
    conn.execute("PRAGMA temp_store=MEMORY;")

    # Step 1: Build aggregate stats per anchor from relations table
    print("Step 1: Computing per-anchor stats from relations table...")
    print("        (This aggregates 57M+ rows — may take several minutes)")
    t0 = time.time()

    # Single-pass aggregation over relations
    agg_sql = """
        SELECT
            subject_id,
            COUNT(*)                    AS rel_count,
            COUNT(DISTINCT capsule_id)  AS src_count,
            COUNT(DISTINCT predicate)   AS pred_count,
            COUNT(DISTINCT object_id)   AS nbr_count
        FROM relations
        GROUP BY subject_id
    """

    cursor = conn.execute(agg_sql)
    elapsed = time.time() - t0
    print(f"        Query returned in {elapsed:.1f}s, fetching in batches...\n")

    # Step 2: Process batches and build update list
    total = 0
    batch = []
    updates = []

    t1 = time.time()

    for row in cursor:
        subject_id, rel_count, src_count, pred_count, nbr_count = row
        maturity = rel_count * (1 + src_count * 0.5) * (1 + pred_count * 0.3) * (1 + nbr_count * 0.1)
        state = compute_state(maturity)
        visible = 1 if maturity >= VISIBILITY_THRESHOLD else 0
        updates.append((maturity, state, visible, subject_id))
        total += 1

        if len(updates) >= batch_size:
            if not dry_run:
                conn.executemany(
                    "UPDATE anchors SET maturity=?, state=?, visible=? WHERE id=?",
                    updates
                )
                conn.commit()
            updates.clear()
            elapsed = time.time() - t1
            rate = total / elapsed
            print(f"  Processed {total:>10,} anchors  ({rate:,.0f}/s)")

    # Final batch
    if updates:
        if not dry_run:
            conn.executemany(
                "UPDATE anchors SET maturity=?, state=?, visible=? WHERE id=?",
                updates
            )
            conn.commit()
        updates.clear()

    elapsed = time.time() - t1
    print(f"\nStep 2 done: {total:,} anchors updated in {elapsed:.1f}s")

    # Step 3: Stats
    print("\nStep 3: Post-update stats...")
    row = conn.execute("SELECT MAX(maturity), AVG(maturity), COUNT(*) FROM anchors WHERE maturity > 0").fetchone()
    print(f"  max maturity : {row[0]:,.2f}")
    print(f"  avg maturity : {row[1]:,.4f}")
    print(f"  anchors > 0  : {row[2]:,}")

    state_dist = conn.execute("SELECT state, COUNT(*) FROM anchors GROUP BY state ORDER BY COUNT(*) DESC").fetchall()
    print("\n  State distribution:")
    for s, c in state_dist:
        print(f"    {s or '(null)':20s}: {c:>10,}")

    vis_count = conn.execute("SELECT COUNT(*) FROM anchors WHERE visible=1").fetchone()[0]
    print(f"\n  Visible anchors: {vis_count:,}")

    print("\n  Top 10 by maturity:")
    top = conn.execute("SELECT canonical, maturity, state, relation_count FROM anchors ORDER BY maturity DESC LIMIT 10").fetchall()
    for canon, mat, st, rc in top:
        st_str = st or "(null)"
        print(f"    {canon:30s}  maturity={mat:>12,.2f}  state={st_str:12s}  relations={rc:,}")

    conn.close()
    if dry_run:
        print("\n[DRY RUN — no changes written to DB]")
    else:
        print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compute maturity scores for resonance_v11.db")
    parser.add_argument("--db", default=str(Path.home() / "resonance_v11.db"),
                        help="Path to DB (default: ~/resonance_v11.db)")
    parser.add_argument("--batch", type=int, default=BATCH_SIZE,
                        help=f"Batch size for updates (default: {BATCH_SIZE:,})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute stats without writing to DB")
    args = parser.parse_args()

    run(Path(args.db).expanduser(), args.batch, args.dry_run)
