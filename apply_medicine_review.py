#!/usr/bin/env python3
"""
apply_medicine_review.py — Programmatic HITL review for Medicine Pass 1 (and future passes).

Applies GPT-reviewed decisions without interactive loop.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "resonance_v11.db"
conn = sqlite3.connect(DB_PATH)

def reject(subj: str, pred: str, obj: str):
    cur = conn.execute("""
        UPDATE relations_llm SET reviewed=1, approved=0
        WHERE subject_id IN (SELECT id FROM anchors WHERE canonical=?)
          AND predicate=?
          AND object_id IN (SELECT id FROM anchors WHERE canonical=?)
          AND reviewed=0
    """, (subj.lower(), pred, obj.lower()))
    if cur.rowcount:
        print(f"  REJECT {subj} --{pred}--> {obj} ({cur.rowcount} row)")

def reject_below_confidence(threshold: float):
    cur = conn.execute(
        "UPDATE relations_llm SET reviewed=1, approved=0 WHERE confidence < ? AND reviewed=0",
        (threshold,)
    )
    print(f"  REJECT confidence < {threshold}: {cur.rowcount} rows")

def bulk_approve_remaining():
    cur = conn.execute(
        "UPDATE relations_llm SET reviewed=1, approved=1 WHERE reviewed=0"
    )
    print(f"  APPROVE remaining: {cur.rowcount} rows")

print("=== Medicine Pass 1 programmatic review ===\n")

print("Step 1: Hard rejects — direction / ontology errors")
reject("medicine",          "distinct_from", "surgery")       # surgery IS part of medicine
reject("diagnosis",         "contains",      "disease")       # reversed
reject("molecular medicine","part_of",       "biochemistry")  # reversed
reject("immunology",        "part_of",       "inflammation response")  # reversed
reject("disease",           "part_of",       "patient outcome")        # wrong concept
reject("medicine",          "part_of",       "healthcare")    # reversed (healthcare contains medicine)

print("\nStep 2: Field-contains-entity violations (GPT correction)")
reject("biochemistry",      "contains",      "enzymes")       # field cannot 'contain' an entity
reject("enzymes",           "part_of",       "biochemistry")  # entity cannot be 'part of' a field

print("\nStep 3: Redundant co_occurs_with where stronger relation already exists")
reject("pharmacology",      "co_occurs_with","biochemistry")  # redundant: pharmacology requires biochemistry
reject("biochemistry",      "co_occurs_with","pharmacology")  # same pair, reverse

print("\nStep 4: Low-confidence auto-reject")
reject_below_confidence(0.36)

conn.commit()

print("\nStep 5: Bulk approve remaining")
bulk_approve_remaining()
conn.commit()

total    = conn.execute("SELECT COUNT(*) FROM relations_llm").fetchone()[0]
approved = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=1").fetchone()[0]
rejected = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=0").fetchone()[0]
pending  = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=0").fetchone()[0]

print(f"\n=== Review complete ===")
print(f"  total={total}  approved={approved}  rejected={rejected}  pending={pending}")
print(f"\nNext: python3 llm_ingest_medicine.py --promote")
