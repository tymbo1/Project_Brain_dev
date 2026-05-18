#!/usr/bin/env python3
"""
apply_ling_pass3_review.py — Programmatic HITL review for Pass 3 + Pass 3b.

Applies GPT-reviewed decisions without interactive loop.
Pattern: reject known bad → reject off-topic drift → reject low confidence → bulk approve rest.
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

print("=== Pass 3 + 3b programmatic review ===\n")

print("Step 1: Hard rejects — direction / ontology errors")
reject("bilabial",              "is_a",      "manner of articulation")
reject("phrase",                "contains",  "sentence")
reject("root",                  "contains",  "stem")
reject("stem",                  "part_of",   "root")
reject("articulatory features", "part_of",   "velar")
reject("predicate logic",       "contains",  "propositional logic")
reject("semivowel",             "is_a",      "vowel")
reject("noun",                  "contains",  "article")
reject("noun",                  "requires",  "nominalization")
reject("verb",                  "contains",  "phrase")
reject("verb",                  "contains",  "phrase structure")
reject("grammar",               "enables",   "lexeme")

print("\nStep 2: Off-topic drift rejects (LLM wandered from seed concept)")
reject("sentence",        "part_of",    "text")
reject("text",            "contains",   "sentence")
reject("verb",            "enables",    "tense")
reject("tense",           "derived_from","aspect")
reject("action",          "related_to", "event")
reject("event",           "contains",   "state")
reject("state",           "distinct_from","process")
reject("noun",            "related_to", "category")
reject("linguistic unit", "is_a",       "language")
reject("word form",       "is_a",       "form")
reject("adjective",       "contains",   "descriptive information")

print("\nStep 3: Low-confidence auto-reject")
reject_below_confidence(0.36)

conn.commit()

print("\nStep 4: Bulk approve remaining")
bulk_approve_remaining()
conn.commit()

# Summary
total    = conn.execute("SELECT COUNT(*) FROM relations_llm").fetchone()[0]
approved = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=1").fetchone()[0]
rejected = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=0").fetchone()[0]
pending  = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=0").fetchone()[0]

print(f"\n=== Review complete ===")
print(f"  total={total}  approved={approved}  rejected={rejected}  pending={pending}")
print(f"\nNext: python3 llm_ingest_ling_pass3.py --promote")
