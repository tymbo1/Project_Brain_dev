#!/usr/bin/env python3
"""apply_medicine_pass3b_review.py — HITL review for Medicine Pass 3b (recovery)."""

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

print("=== Medicine Pass 3b programmatic review ===\n")

print("Step 1: Direction / category errors")
reject("transcription", "contains",  "rna polymerase")       # rna polymerase enables transcription
reject("transcription", "enables",   "protein synthesis")     # translation does this, not transcription
reject("interleukin",   "contains",  "amino acid sequence")   # category error
reject("diastole",      "part_of",   "cardiovascular system") # state, not structural component
reject("interferon",    "contains",  "peptide sequence")      # category error
reject("interferon",    "part_of",   "lymphocyte")            # wrong direction
reject("angiogenesis",  "derived_from", "stem cell differentiation")  # process

print("\nStep 2: Blanket noise filter")
reject_predicate("co_occurs_with")
reject_predicate("related_to")

conn.commit()

print("\nStep 3: Bulk approve remaining")
cur = conn.execute("UPDATE relations_llm SET reviewed=1, approved=1 WHERE reviewed=0")
print(f"  APPROVE remaining: {cur.rowcount} rows")
conn.commit()

approved = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=1").fetchone()[0]
rejected = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=0").fetchone()[0]
print(f"\n=== Review complete === approved={approved}  rejected={rejected}")
print("\nNext: python3 llm_ingest_medicine_pass3b.py --promote")
