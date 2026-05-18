#!/usr/bin/env python3
"""apply_psychology_depth_review.py — HITL review for Psychology Pass 2."""

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

print("=== Psychology Pass 2 programmatic review ===\n")

print("Step 1: Direction errors")
reject("operant conditioning",  "contains",  "classical conditioning")   # distinct paradigms
reject("working memory",        "part_of",   "cognitive load")            # wrong direction
reject("executive functions",   "requires",  "cognitive load")            # wrong direction
reject("sadness",               "used_for",  "emotional regulation")      # sadness disrupts it
reject("sadness",               "enables",   "self-pity")                 # consequence not enabling
reject("extraversion",          "used_for",  "assertiveness")             # wrong predicate
reject("extraversion",          "used_for",  "public speaking")           # wrong predicate
reject("extinction",            "contains",  "habituation")               # different phenomena

print("\nStep 2: Theory/process as derived_from object")
reject("intrinsic motivation",  "requires",  "self-determination theory") # theory not entity
reject("problem solving",       "derived_from", "social learning theory") # theory not entity
reject("metacognition",         "derived_from", "neural networks")        # too vague

print("\nStep 3: Introversion pathologisation")
reject("introversion",          "contains",  "social anxiety")            # introversion ≠ pathology
reject("introversion",          "contains",  "avoidance behavior")        # same

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
print("\nNext: python3 llm_ingest_psychology_depth.py --promote")
