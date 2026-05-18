#!/usr/bin/env python3
"""
Extract document/fragment anchors from Layer 1 (anchors) → Layer 2 (fragments).

Reads malformed_anchors_delete.tsv (from audit_malformed_anchors.py).
For each fragment anchor:
  1. INSERT into fragments (text, source, state, confidence)
  2. INSERT fragment_links for any relations it has in relations_aggregated
  3. DELETE relations_aggregated rows referencing the malformed anchor
  4. DELETE the anchor

Does NOT destroy data — moves it to the correct layer.

DEFAULT: dry-run only. Pass --execute to write to DB.
Checkpoint: migrate_extract_checkpoint.txt — resumable.
"""

import sqlite3
import os
import csv
import argparse
import hashlib
import time

DB_PATH    = os.path.expanduser("~/resonance_v11.db")
DELETE_TSV = os.path.join(os.path.dirname(__file__), "malformed_anchors_delete.tsv")
CHECKPOINT = os.path.join(os.path.dirname(__file__), "migrate_extract_checkpoint.txt")
CHUNK_SIZE = 200

# Minimum relation_count to bother linking (very isolated fragments aren't worth linking)
MIN_RC_FOR_LINKS = 5


def load_checkpoint():
    if os.path.exists(CHECKPOINT):
        with open(CHECKPOINT) as f:
            val = f.read().strip()
            return int(val) if val else 0
    return 0


def save_checkpoint(n):
    with open(CHECKPOINT, 'w') as f:
        f.write(str(n))


def make_fragment_id(text: str) -> str:
    return "frag_" + hashlib.md5(text.encode()).hexdigest()[:16]


def load_candidates():
    candidates = []
    with open(DELETE_TSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            candidates.append((row['canonical'], int(row['relation_count']), row['reason']))
    return candidates


def run(execute: bool):
    candidates = load_candidates()
    start = load_checkpoint()
    remaining = candidates[start:]

    print(f"Fragment candidates: {len(candidates)}  |  Already done: {start}  |  Remaining: {len(remaining)}")
    if not execute:
        print("DRY RUN — showing first 30. Pass --execute to apply.")
        for canon, rc, reason in remaining[:30]:
            print(f"  EXTRACT  [{rc:>5}]  '{canon}'  ({reason})")
        return

    con = sqlite3.connect(DB_PATH, timeout=120)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")

    extracted = 0
    linked    = 0
    skipped   = 0

    for i, (canon, rc, reason) in enumerate(remaining):
        anc_row = con.execute(
            "SELECT id, maturity FROM anchors WHERE canonical = ?", (canon,)).fetchone()

        if not anc_row:
            skipped += 1
            continue

        anc_id  = anc_row[0]
        maturity = anc_row[1] or 0.0
        frag_id  = make_fragment_id(canon)
        confidence = min(maturity / 1e9, 1.0)

        # Insert into fragments
        con.execute("""
            INSERT OR IGNORE INTO fragments (id, text, source, state, confidence)
            VALUES (?, ?, 'anchor_migration', 'extracted', ?)
        """, (frag_id, canon, confidence))

        # Preserve ALL relations as fragment_links BEFORE removing from field.
        # Option B: fragments stay connected to the anchor field via fragment_links.
        # This keeps resonance pathways intact for LLM recall expansion.
        outbound = con.execute("""
            SELECT object_id, predicate FROM relations_aggregated
            WHERE subject_id = ?
        """, (anc_id,)).fetchall()
        for nbr_id, pred in outbound:
            con.execute("""
                INSERT OR IGNORE INTO fragment_links (fragment_id, anchor_id, relation)
                VALUES (?, ?, ?)
            """, (frag_id, nbr_id, pred))
            linked += 1

        inbound = con.execute("""
            SELECT subject_id, predicate FROM relations_aggregated
            WHERE object_id = ?
        """, (anc_id,)).fetchall()
        for nbr_id, pred in inbound:
            con.execute("""
                INSERT OR IGNORE INTO fragment_links (fragment_id, anchor_id, relation)
                VALUES (?, ?, ?)
            """, (frag_id, nbr_id, pred))
            linked += 1

        # Now remove from relations_aggregated (fragment exits the core field,
        # but its connections are preserved in fragment_links)
        con.execute("DELETE FROM relations_aggregated WHERE subject_id = ?", (anc_id,))
        con.execute("DELETE FROM relations_aggregated WHERE object_id = ?", (anc_id,))

        # Delete anchor
        con.execute("DELETE FROM anchors WHERE id = ?", (anc_id,))

        extracted += 1

        if (i + 1) % CHUNK_SIZE == 0:
            con.commit()
            save_checkpoint(start + i + 1)
            pct = (start + i + 1) / len(candidates) * 100
            print(f"  [{start+i+1:>6}/{len(candidates)}]  extracted={extracted}  links={linked}  skipped={skipped}  ({pct:.1f}%)")

    con.commit()
    save_checkpoint(len(candidates))
    con.close()

    print(f"\nDone. Extracted: {extracted}  Links created: {linked}  Skipped: {skipped}")
    if os.path.exists(CHECKPOINT):
        os.remove(CHECKPOINT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true",
                        help="Actually write to DB (default: dry-run)")
    args = parser.parse_args()
    run(execute=args.execute)
