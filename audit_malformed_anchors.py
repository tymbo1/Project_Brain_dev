#!/usr/bin/env python3
"""
Audit malformed anchors — READ ONLY, no DB writes.

True malformed patterns (not apostrophes in proper nouns):
  - Leading "_ " prefix  → "_ life", "_ fear"
  - Leading "- " prefix  → "- spirit", "- water"
  - Trailing lone "'"    → "god'", "bed'", "gtp'"
  - Leading/trailing whitespace

Output:
  malformed_anchors_report.txt   — human-readable summary
  malformed_anchors_merge.tsv    — MERGE candidates
  malformed_anchors_delete.tsv   — DELETE candidates
"""

import sqlite3
import os
import re

DB_PATH  = os.path.expanduser("~/resonance_v11.db")
OUT_DIR  = os.path.dirname(__file__)
REPORT   = os.path.join(OUT_DIR, "malformed_anchors_report.txt")
MERGE_F  = os.path.join(OUT_DIR, "malformed_anchors_merge.tsv")
DELETE_F = os.path.join(OUT_DIR, "malformed_anchors_delete.tsv")

HIGH_VALUE_THRESHOLD = 50
MAX_CONCEPT_WORDS    = 5

FRAGMENT_STARTERS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
    'for', 'of', 'with', 'by', 'from', 'among', 'only', 'your',
    'its', 'their', 'this', 'that', 'these', 'those'
}


def clean_artifact(s: str) -> str:
    """Strip the leading/trailing artifact, return what's underneath."""
    s = s.strip()
    s = re.sub(r'^[_\-]\s+', '', s)   # "_ life" → "life", "- spirit" → "spirit"
    s = re.sub(r"'+$", '', s)          # "god'" → "god", "gtp''" → "gtp"
    return s.strip().lower()


def is_fragment(s: str) -> bool:
    if not s:
        return True
    words = s.split()
    if len(words) > MAX_CONCEPT_WORDS:
        return True
    if re.search(r'[;:!?@#$%^&*+=\[\]{}<>\\|/]', s):
        return True
    if words[0] in FRAGMENT_STARTERS:
        return True
    return False


def classify(canon: str, rc: int) -> tuple:
    """Returns (class, clean_target, reason). class: MERGE | DELETE | REVIEW"""
    cleaned = clean_artifact(canon)

    if not cleaned:
        return ('DELETE', '', 'empty after stripping artifact')

    if is_fragment(cleaned):
        if rc >= HIGH_VALUE_THRESHOLD:
            return ('REVIEW', cleaned, f'fragment with high rc={rc}')
        return ('DELETE', cleaned, 'sentence fragment')

    # Real concept underneath
    if rc >= HIGH_VALUE_THRESHOLD:
        return ('REVIEW', cleaned, f'high-value merge candidate rc={rc}')
    return ('MERGE', cleaned, 'leading/trailing artifact stripped')


def run():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    rows = con.execute("""
        SELECT id, canonical, relation_count, maturity
        FROM anchors
        WHERE canonical LIKE '_ %'
           OR canonical LIKE '- %'
           OR canonical != TRIM(canonical)
           OR (canonical LIKE "%'"
               AND canonical NOT LIKE "%'s"
               AND canonical NOT LIKE "%'t"
               AND canonical NOT LIKE "%'s %"
               AND canonical NOT LIKE "%'ll%"
               AND canonical NOT LIKE "%'re%"
               AND canonical NOT LIKE "%'ve%")
        ORDER BY relation_count DESC
    """).fetchall()

    clean_canonicals = set(
        r[0] for r in con.execute("SELECT canonical FROM anchors").fetchall()
    )

    merge  = []
    delete = []
    review = []

    for row in rows:
        canon   = row['canonical']
        rc      = row['relation_count'] or 0
        cls, target, reason = classify(canon, rc)
        target_exists = target in clean_canonicals if target else False

        if cls == 'MERGE':
            merge.append((canon, target, rc, target_exists))
        elif cls == 'DELETE':
            delete.append((canon, rc, reason))
        else:
            review.append((canon, rc, f"{reason} → '{target}' (exists={target_exists})"))

    con.close()

    # ── Report ────────────────────────────────────────────────────────────────
    with open(REPORT, 'w') as f:
        f.write("MALFORMED ANCHOR AUDIT REPORT\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Total detected:    {len(rows)}\n")
        f.write(f"  MERGE:           {len(merge)}\n")
        f.write(f"  DELETE:          {len(delete)}\n")
        f.write(f"  REVIEW:          {len(review)}\n\n")

        f.write("─" * 60 + "\n")
        f.write("REVIEW REQUIRED\n")
        f.write("─" * 60 + "\n")
        for canon, rc, reason in sorted(review, key=lambda x: -x[1]):
            f.write(f"  [{rc:>6}]  '{canon}'  →  {reason}\n")

        f.write("\n" + "─" * 60 + "\n")
        f.write("TOP MERGE CANDIDATES\n")
        f.write("─" * 60 + "\n")
        for canon, target, rc, exists in sorted(merge, key=lambda x: -x[2])[:100]:
            f.write(f"  [{rc:>6}]  '{canon}'  →  '{target}'  [{'EXISTS' if exists else 'NEW'}]\n")
        if len(merge) > 100:
            f.write(f"  ... {len(merge)-100} more in TSV\n")

        f.write("\n" + "─" * 60 + "\n")
        f.write("TOP DELETE CANDIDATES\n")
        f.write("─" * 60 + "\n")
        for canon, rc, reason in sorted(delete, key=lambda x: -x[1])[:50]:
            f.write(f"  [{rc:>6}]  '{canon}'  ({reason})\n")
        if len(delete) > 50:
            f.write(f"  ... {len(delete)-50} more in TSV\n")

    # ── TSV files ─────────────────────────────────────────────────────────────
    with open(MERGE_F, 'w') as f:
        f.write("malformed\tclean_target\trelation_count\ttarget_exists\n")
        for canon, target, rc, exists in sorted(merge, key=lambda x: -x[2]):
            f.write(f"{canon}\t{target}\t{rc}\t{exists}\n")

    with open(DELETE_F, 'w') as f:
        f.write("canonical\trelation_count\treason\n")
        for canon, rc, reason in sorted(delete, key=lambda x: -x[1]):
            f.write(f"{canon}\t{rc}\t{reason}\n")

    print(f"Done.")
    print(f"  Report: {REPORT}")
    print(f"  Merge:  {MERGE_F}  ({len(merge)} candidates)")
    print(f"  Delete: {DELETE_F}  ({len(delete)} candidates)")
    print(f"  Review: {len(review)} items in report")


if __name__ == "__main__":
    run()
