#!/usr/bin/env python3
"""
Merge corrupted anchor artifacts into their clean canonical targets.

Reads malformed_anchors_merge.tsv (from audit_malformed_anchors.py).
For each (malformed → clean) pair where target_exists=True and malformed != clean:
  1. Redirect all relations_aggregated rows to clean anchor id
  2. Accumulate seen_count/evidence_count on conflicts
  3. Update anchors.relation_count on clean anchor
  4. Delete the malformed anchor

DEFAULT: dry-run only. Pass --execute to write to DB.
Checkpoint: migrate_merge_checkpoint.txt — resumable.
"""

import sqlite3
import os
import csv
import argparse

DB_PATH    = os.path.expanduser("~/resonance_v11.db")
MERGE_TSV  = os.path.join(os.path.dirname(__file__), "malformed_anchors_merge.tsv")
CHECKPOINT = os.path.join(os.path.dirname(__file__), "migrate_merge_checkpoint.txt")
CHUNK_SIZE = 500

KEEP_PATTERNS = {
    't cell', 'b cell', 'g protein', 'x chromosome', 'y chromosome',
    'q fever', 'q factor', 'r package', 'e learning', 't tauri star',
    'g alpha subunit', 'g protein-coupled receptor',
    'g protein-coupled receptor kinase', 'g protein-coupled bile acid receptor',
}


def load_checkpoint():
    if os.path.exists(CHECKPOINT):
        with open(CHECKPOINT) as f:
            val = f.read().strip()
            return int(val) if val else 0
    return 0


def save_checkpoint(n):
    with open(CHECKPOINT, 'w') as f:
        f.write(str(n))


def load_merge_candidates():
    candidates = []
    with open(MERGE_TSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            if row['target_exists'] != 'True':
                continue
            malformed = row['malformed']
            target    = row['clean_target']
            if target in KEEP_PATTERNS or malformed == target:
                continue
            candidates.append((malformed, target, int(row['relation_count'])))
    return candidates


UPSERT_SUBJ = """
    INSERT INTO relations_aggregated
        (subject_id, predicate, object_id, domain_tags, edge_type,
         seen_count, evidence_count, confidence, edge_weight, polarity)
    SELECT ?,
           COALESCE(predicate,   ''),
           COALESCE(object_id,   ''),
           COALESCE(domain_tags, ''),
           COALESCE(edge_type,   ''),
           COALESCE(seen_count,   1),
           COALESCE(evidence_count, 1),
           confidence, edge_weight, polarity
    FROM relations_aggregated
    WHERE subject_id = ?
      AND object_id  IS NOT NULL
      AND predicate  IS NOT NULL
    ON CONFLICT(subject_id, predicate, object_id, domain_tags, edge_type)
    DO UPDATE SET
        seen_count     = seen_count     + excluded.seen_count,
        evidence_count = evidence_count + excluded.evidence_count,
        confidence     = MAX(confidence,  excluded.confidence),
        edge_weight    = MAX(COALESCE(edge_weight,0), COALESCE(excluded.edge_weight,0))
"""

UPSERT_OBJ = """
    INSERT INTO relations_aggregated
        (subject_id, predicate, object_id, domain_tags, edge_type,
         seen_count, evidence_count, confidence, edge_weight, polarity)
    SELECT COALESCE(subject_id,  ''),
           COALESCE(predicate,   ''),
           ?,
           COALESCE(domain_tags, ''),
           COALESCE(edge_type,   ''),
           COALESCE(seen_count,   1),
           COALESCE(evidence_count, 1),
           confidence, edge_weight, polarity
    FROM relations_aggregated
    WHERE object_id  = ?
      AND subject_id IS NOT NULL
      AND predicate  IS NOT NULL
    ON CONFLICT(subject_id, predicate, object_id, domain_tags, edge_type)
    DO UPDATE SET
        seen_count     = seen_count     + excluded.seen_count,
        evidence_count = evidence_count + excluded.evidence_count,
        confidence     = MAX(confidence,  excluded.confidence),
        edge_weight    = MAX(COALESCE(edge_weight,0), COALESCE(excluded.edge_weight,0))
"""


def run(execute: bool):
    candidates = load_merge_candidates()
    start = load_checkpoint()
    remaining = candidates[start:]

    print(f"Merge candidates: {len(candidates)}  |  Done: {start}  |  Remaining: {len(remaining)}")
    if not execute:
        print("DRY RUN — showing first 30. Pass --execute to apply.")
        for mal, tgt, rc in remaining[:30]:
            print(f"  MERGE  '{mal}' ({rc} relations) → '{tgt}'")
        return

    con = sqlite3.connect(DB_PATH, timeout=120)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-128000")

    merged = 0
    skipped = 0
    errors  = 0

    for i, (malformed, target, rc) in enumerate(remaining):
        mal_row = con.execute(
            "SELECT id FROM anchors WHERE canonical = ?", (malformed,)).fetchone()
        tgt_row = con.execute(
            "SELECT id, relation_count FROM anchors WHERE canonical = ?", (target,)).fetchone()

        if not mal_row or not tgt_row:
            skipped += 1
            continue

        mal_id = mal_row[0]
        tgt_id = tgt_row[0]

        if not mal_id or not tgt_id or mal_id == tgt_id:
            skipped += 1
            continue

        try:
            con.execute(UPSERT_SUBJ, (tgt_id, mal_id))
            con.execute("DELETE FROM relations_aggregated WHERE subject_id = ?", (mal_id,))
            con.execute(UPSERT_OBJ,  (tgt_id, mal_id))
            con.execute("DELETE FROM relations_aggregated WHERE object_id  = ?", (mal_id,))
            con.execute("UPDATE anchors SET relation_count = relation_count + ? WHERE id = ?",
                        (rc, tgt_id))
            con.execute("DELETE FROM anchors WHERE id = ?", (mal_id,))
            merged += 1
        except Exception as e:
            errors += 1
            con.rollback()
            print(f"  SKIP  '{malformed}' → '{target}': {e}")
            continue

        if (i + 1) % CHUNK_SIZE == 0:
            con.commit()
            save_checkpoint(start + i + 1)
            pct = (start + i + 1) / len(candidates) * 100
            print(f"  [{start+i+1:>5}/{len(candidates)}]  merged={merged}  skipped={skipped}  errors={errors}  ({pct:.1f}%)")

    con.commit()
    save_checkpoint(len(candidates))
    con.close()

    print(f"\nDone. Merged: {merged}  Skipped: {skipped}  Errors: {errors}")
    if os.path.exists(CHECKPOINT):
        os.remove(CHECKPOINT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    run(execute=args.execute)
