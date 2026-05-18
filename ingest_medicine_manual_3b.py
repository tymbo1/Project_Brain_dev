#!/usr/bin/env python3
"""
ingest_medicine_manual_3b.py — Hand-authored seed relations for 7 concepts
that produced 0–3 valid relations in Medicine Pass 3 (objects not in DB as anchors).

Writes directly to relations_aggregated (Tier 1) + stamps sem_domain='medicine'.
Run with --dry-run (default) or --commit.

Concepts: aorta(0), transcription(1), interleukin(1), diastole(2),
          interferon(2), atp(3), angiogenesis(3)
"""

import sqlite3, argparse
from pathlib import Path

DB_PATH = Path.home() / "resonance_v11.db"

parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true", default=True)
parser.add_argument("--commit",  action="store_true")
args = parser.parse_args()
if args.commit:
    args.dry_run = False

MANUAL_RELATIONS = [
    # aorta — 0 relations from Pass 3 (all objects missed anchors)
    ("aorta",          "is_a",          "artery"),
    ("aorta",          "part_of",       "heart"),
    ("aorta",          "part_of",       "cardiovascular system"),
    ("aorta",          "contains",      "blood"),
    ("aorta",          "requires",      "blood pressure"),
    ("aorta",          "enables",       "circulation"),
    ("aorta",          "distinct_from", "vein"),

    # transcription — had only: distinct_from translation
    ("transcription",  "is_a",          "biological process"),
    ("transcription",  "part_of",       "gene expression"),
    ("transcription",  "requires",      "dna"),
    ("transcription",  "requires",      "nucleus"),
    ("transcription",  "enables",       "translation"),
    ("transcription",  "part_of",       "cell"),

    # interleukin — had only: distinct_from chemokine
    ("interleukin",    "is_a",          "cytokine"),
    ("interleukin",    "part_of",       "immune system"),
    ("interleukin",    "requires",      "t cell"),
    ("interleukin",    "requires",      "b cell"),
    ("interleukin",    "enables",       "immune response"),
    ("interleukin",    "distinct_from", "interferon"),

    # diastole — had: distinct_from systole, part_of cardiac cycle
    ("diastole",       "is_a",          "cardiac phase"),
    ("diastole",       "part_of",       "heartbeat"),
    ("diastole",       "requires",      "blood flow"),
    ("diastole",       "enables",       "heart"),
    ("diastole",       "requires",      "relaxation"),

    # interferon — had: is_a cytokine, contains glycoprotein
    ("interferon",     "part_of",       "immune system"),
    ("interferon",     "enables",       "antiviral"),
    ("interferon",     "requires",      "virus"),
    ("interferon",     "distinct_from", "interleukin"),
    ("interferon",     "enables",       "immune response"),

    # atp — had: is_a nucleotide, distinct_from gtp, distinct_from nadh
    ("atp",            "part_of",       "cell"),
    ("atp",            "requires",      "mitochondria"),
    ("atp",            "enables",       "metabolism"),
    ("atp",            "derived_from",  "glucose"),
    ("atp",            "used_for",      "energy production"),

    # angiogenesis — had: distinct_from apoptosis, contains pericytes
    ("angiogenesis",   "is_a",          "biological process"),
    ("angiogenesis",   "part_of",       "development"),
    ("angiogenesis",   "requires",      "oxygen"),
    ("angiogenesis",   "enables",       "tumor growth"),
    ("angiogenesis",   "enables",       "wound healing"),
    ("angiogenesis",   "distinct_from", "apoptosis"),
]

def resolve(name: str, conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT id FROM anchors WHERE canonical = ? LIMIT 1", (name.lower(),)
    ).fetchone()
    return row[0] if row else None

def _stamp_sem_domain(conn, anchor_ids: set, domain: str):
    if not anchor_ids:
        return
    conn.execute(
        f"DELETE FROM ssre_top_semantic WHERE anchor_id IN ({','.join('?'*len(anchor_ids))})",
        list(anchor_ids)
    )
    conn.executemany(
        "INSERT INTO ssre_top_semantic (anchor_id, sem_domain) VALUES (?,?)",
        [(aid, domain) for aid in anchor_ids]
    )

def main():
    conn = sqlite3.connect(DB_PATH)
    mode = "DRY RUN" if args.dry_run else "COMMIT"
    print(f"\nMedicine manual seed (Pass 3b) — {mode}")
    print(f"Relations defined: {len(MANUAL_RELATIONS)}")
    print("=" * 60)

    inserted = skipped_exists = skipped_no_anchor = 0
    touched = set()

    for subj, pred, obj in MANUAL_RELATIONS:
        subj_id = resolve(subj, conn)
        obj_id  = resolve(obj, conn)
        if not subj_id or not obj_id:
            print(f"  SKIP (no anchor)  {subj} --{pred}--> {obj}")
            skipped_no_anchor += 1
            continue
        exists = conn.execute(
            "SELECT 1 FROM relations_aggregated WHERE subject_id=? AND predicate=? AND object_id=? LIMIT 1",
            (subj_id, pred, obj_id)
        ).fetchone()
        if exists:
            print(f"  EXISTS            {subj} --{pred}--> {obj}")
            skipped_exists += 1
            continue
        print(f"  {'DRY' if args.dry_run else 'INSERT'}   {subj} --{pred}--> {obj}")
        if not args.dry_run:
            conn.execute("""
                INSERT INTO relations_aggregated
                (subject_id, predicate, object_id, domain_tags, edge_type,
                 confidence, seen_count, evidence_count)
                VALUES (?,?,?,?,?,?,2,1)
            """, (subj_id, pred, obj_id, "medicine,manual", "semantic", 0.95))
            touched.add(subj_id)
            touched.add(obj_id)
        inserted += 1

    if not args.dry_run and touched:
        _stamp_sem_domain(conn, touched, "medicine")
        conn.commit()
        print(f"\nStamped sem_domain='medicine' on {len(touched)} anchors.")

    print(f"\n{'='*60}")
    print(f"Inserted:            {inserted}")
    print(f"Already existed:     {skipped_exists}")
    print(f"No anchor (skipped): {skipped_no_anchor}")
    if args.dry_run:
        print("\nDRY RUN — nothing written. Re-run with --commit to insert.")
    else:
        print(f"\nDone. {inserted} manual relations now in Tier 1.")
        print("Next: run llm_ingest_medicine_pass3b.py --commit to extend via LLM")

if __name__ == "__main__":
    main()
