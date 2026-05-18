#!/usr/bin/env python3
"""apply_philosophy_pass3_review.py — HITL review for Philosophy Pass 3."""

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "resonance_v11.db"
conn = sqlite3.connect(DB_PATH)

def reject(subj, pred, obj):
    cur = conn.execute("""
        UPDATE relations_llm SET reviewed=1, approved=0
        WHERE subject_id IN (SELECT id FROM anchors WHERE canonical=?)
          AND predicate=? AND object_id IN (SELECT id FROM anchors WHERE canonical=?)
          AND reviewed=0
    """, (subj.lower(), pred, obj.lower()))
    if cur.rowcount:
        print(f"  REJECT {subj} --{pred}--> {obj} ({cur.rowcount} row)")

def reject_predicate(pred):
    cur = conn.execute(
        "UPDATE relations_llm SET reviewed=1, approved=0 WHERE predicate=? AND reviewed=0", (pred,)
    )
    print(f"  REJECT all predicate='{pred}': {cur.rowcount} rows")

def reject_below_confidence(threshold):
    cur = conn.execute(
        "UPDATE relations_llm SET reviewed=1, approved=0 WHERE confidence < ? AND reviewed=0",
        (threshold,)
    )
    print(f"  REJECT confidence < {threshold}: {cur.rowcount} rows")

print("=== Philosophy Pass 3 programmatic review ===\n")

print("Step 1: Category/domain errors")
reject("anomalous monism",       "contains",    "phenomenology")        # analytic ≠ phenomenology
reject("anomalous monism",       "contains",    "nominalism")           # anomalous monism ≠ nominalism
reject("being in the world",     "is_a",        "phenomenology")        # concept within, not a type of
reject("abstract objects",       "part_of",     "philosophy of language") # studied by, not part of

print("\nStep 2: Direction and derivation errors")
reject("anarchism",              "derived_from", "classical liberalism") # anarchism critiques liberalism
reject("common good",            "derived_from", "utilitarianism")       # common good predates utilitarianism
reject("abstract objects",       "derived_from", "mental representations") # Platonic objects are independent
reject("civic virtue",           "derived_from", "aristotelian ethics")  # theory not entity

print("\nStep 3: Wrong predicate")
reject("anguish",                "used_for",    "self-preservation")    # anguish doesn't serve self-preservation

print("\nStep 4: Blanket noise filter")
reject_predicate("co_occurs_with")
reject_predicate("related_to")

print("\nStep 5: Low-confidence auto-reject")
reject_below_confidence(0.60)

conn.commit()

print("\nStep 6: Bulk approve remaining")
cur = conn.execute("UPDATE relations_llm SET reviewed=1, approved=1 WHERE reviewed=0")
print(f"  APPROVE remaining: {cur.rowcount} rows")
conn.commit()

approved = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=1").fetchone()[0]
rejected = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=0").fetchone()[0]
print(f"\n=== Review complete === approved={approved}  rejected={rejected}")
print("\nNext: python3 llm_ingest_philosophy_pass3.py --promote")
