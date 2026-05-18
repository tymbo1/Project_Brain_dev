#!/usr/bin/env python3
"""
ingest_ling_manual_3b.py — Hand-authored seed relations for 16 concepts that
failed LLM generation in Pass 3b (abstract/formal terms lacking CMS grounding).

Writes directly to relations_aggregated (Tier 1) + stamps sem_domain.
Run with --dry-run (default) or --commit.
"""

import sqlite3, uuid, time, argparse
from pathlib import Path

DB_PATH = Path.home() / "resonance_v11.db"

parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true", default=True)
parser.add_argument("--commit",  action="store_true")
args = parser.parse_args()
if args.commit:
    args.dry_run = False

# subject → predicate → object (all must resolve as anchors)
MANUAL_RELATIONS = [
    # affricate
    ("affricate",        "is_a",         "consonant"),
    ("affricate",        "contains",     "stop"),
    ("affricate",        "contains",     "fricative"),
    ("affricate",        "distinct_from","stop"),
    ("affricate",        "distinct_from","fricative"),
    ("affricate",        "part_of",      "phoneme"),

    # voiced
    ("voiced",           "part_of",      "phoneme"),
    ("voiced",           "distinct_from","voiceless"),
    ("voiced",           "co_occurs_with","consonant"),
    ("voiced",           "co_occurs_with","vowel"),
    ("voiced",           "related_to",   "phonology"),

    # alveolar
    ("alveolar",         "is_a",         "place of articulation"),
    ("alveolar",         "part_of",      "consonant"),
    ("alveolar",         "distinct_from","bilabial"),
    ("alveolar",         "distinct_from","velar"),
    ("alveolar",         "co_occurs_with","phoneme"),

    # rime
    ("rime",             "part_of",      "syllable"),
    ("rime",             "contains",     "nucleus"),
    ("rime",             "contains",     "coda"),
    ("rime",             "distinct_from","onset"),
    ("rime",             "co_occurs_with","onset"),

    # mora
    ("mora",             "part_of",      "syllable"),
    ("mora",             "part_of",      "foot"),
    ("mora",             "related_to",   "phonology"),
    ("mora",             "distinct_from","syllable"),
    ("mora",             "used_for",     "syllable"),

    # glottal
    ("glottal",          "is_a",         "place of articulation"),
    ("glottal",          "part_of",      "consonant"),
    ("glottal",          "requires",     "glottis"),
    ("glottal",          "distinct_from","alveolar"),
    ("glottal",          "distinct_from","bilabial"),

    # complement
    ("complement",       "part_of",      "sentence"),
    ("complement",       "requires",     "verb"),
    ("complement",       "distinct_from","adjunct"),
    ("complement",       "co_occurs_with","subject"),
    ("complement",       "co_occurs_with","object"),
    ("complement",       "related_to",   "syntax"),

    # head
    ("head",             "part_of",      "phrase"),
    ("head",             "distinct_from","modifier"),
    ("head",             "co_occurs_with","modifier"),
    ("head",             "related_to",   "syntax"),
    ("head",             "enables",      "phrase"),

    # adverb
    ("adverb",           "is_a",         "word class"),
    ("adverb",           "part_of",      "phrase"),
    ("adverb",           "co_occurs_with","verb"),
    ("adverb",           "co_occurs_with","adjective"),
    ("adverb",           "distinct_from","adjective"),
    ("adverb",           "related_to",   "syntax"),

    # determiner
    ("determiner",       "is_a",         "word class"),
    ("determiner",       "part_of",      "noun phrase"),
    ("determiner",       "contains",     "article"),
    ("determiner",       "requires",     "noun"),
    ("determiner",       "distinct_from","pronoun"),
    ("determiner",       "co_occurs_with","noun"),

    # synonym
    ("synonym",          "is_a",         "semantic relation"),
    ("synonym",          "part_of",      "lexical semantics"),
    ("synonym",          "distinct_from","antonym"),
    ("synonym",          "co_occurs_with","polysemy"),
    ("synonym",          "related_to",   "word sense"),
    ("synonym",          "related_to",   "semantic field"),

    # antonym
    ("antonym",          "is_a",         "semantic relation"),
    ("antonym",          "part_of",      "lexical semantics"),
    ("antonym",          "distinct_from","synonym"),
    ("antonym",          "co_occurs_with","polysemy"),
    ("antonym",          "related_to",   "semantic field"),

    # prototype
    ("prototype",        "part_of",      "semantics"),
    ("prototype",        "related_to",   "category"),
    ("prototype",        "distinct_from","definition"),
    ("prototype",        "enables",      "categorization"),
    ("prototype",        "related_to",   "meaning"),

    # denotation
    ("denotation",       "is_a",         "semantic property"),
    ("denotation",       "part_of",      "semantics"),
    ("denotation",       "distinct_from","connotation"),
    ("denotation",       "related_to",   "reference"),
    ("denotation",       "related_to",   "meaning"),

    # compositionality
    ("compositionality", "part_of",      "semantics"),
    ("compositionality", "requires",     "morpheme"),
    ("compositionality", "requires",     "syntax"),
    ("compositionality", "enables",      "meaning"),
    ("compositionality", "related_to",   "predicate logic"),

    # scope
    ("scope",            "part_of",      "semantics"),
    ("scope",            "co_occurs_with","quantifier"),
    ("scope",            "related_to",   "predicate logic"),
    ("scope",            "related_to",   "syntax"),
    ("scope",            "distinct_from","reference"),
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
    print(f"\nLinguistics manual seed (Pass 3b) — {mode}")
    print(f"Relations defined: {len(MANUAL_RELATIONS)}")
    print("=" * 60)

    inserted = skipped_exists = skipped_no_anchor = 0
    touched = set()

    for subj, pred, obj in MANUAL_RELATIONS:
        subj_id = resolve(subj, conn)
        obj_id  = resolve(obj, conn)
        if not subj_id or not obj_id:
            print(f"  SKIP (no anchor) {subj} --{pred}--> {obj}")
            skipped_no_anchor += 1
            continue
        exists = conn.execute(
            "SELECT 1 FROM relations_aggregated WHERE subject_id=? AND predicate=? AND object_id=? LIMIT 1",
            (subj_id, pred, obj_id)
        ).fetchone()
        if exists:
            print(f"  EXISTS {subj} --{pred}--> {obj}")
            skipped_exists += 1
            continue
        print(f"  {'DRY' if args.dry_run else 'INSERT'} {subj} --{pred}--> {obj}")
        if not args.dry_run:
            conn.execute("""
                INSERT INTO relations_aggregated
                (subject_id, predicate, object_id, domain_tags, edge_type,
                 confidence, seen_count, evidence_count)
                VALUES (?,?,?,?,?,?,2,1)
            """, (subj_id, pred, obj_id, "linguistics,manual", "semantic", 0.95))
            touched.add(subj_id)
            touched.add(obj_id)
        inserted += 1

    if not args.dry_run and touched:
        _stamp_sem_domain(conn, touched, "linguistics")
        conn.commit()
        print(f"\nStamped sem_domain='linguistics' on {len(touched)} anchors.")

    print(f"\n{'='*60}")
    print(f"Inserted:          {inserted}")
    print(f"Already existed:   {skipped_exists}")
    print(f"No anchor (skipped): {skipped_no_anchor}")
    if args.dry_run:
        print("\nDRY RUN — nothing written. Re-run with --commit to insert.")
    else:
        print(f"\nDone. {inserted} manual relations now in Tier 1.")
        print("Next: re-run llm_ingest_ling_pass3b.py --commit to extend via LLM")

if __name__ == "__main__":
    main()
