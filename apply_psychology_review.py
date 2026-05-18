#!/usr/bin/env python3
"""
apply_psychology_review.py — HITL review for Psychology Pass 1.

Rejects: direction errors, theory-as-entity derived_from, used_for misuse,
classic psychology misconceptions (negative reinforcement ≠ punishment).
"""

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

print("=== Psychology Pass 1 programmatic review ===\n")

print("Step 1: Direction errors")
reject("neurotransmitters",  "part_of",   "endocrine system")       # neurotransmitters → nervous system
reject("neuropsychology",    "contains",  "cognitive psychology")    # wrong direction
reject("symptoms",           "contains",  "anxiety")                 # anxiety is a symptom, not in symptoms
reject("symptoms",           "contains",  "depression")              # same
reject("autism",             "used_for",  "social communication")    # autism impairs it, not used_for it
reject("autism",             "used_for",  "self-regulation")         # same
reject("depression",         "used_for",  "emotional regulation")    # depression disrupts it
reject("anxiety",            "used_for",  "stress response")         # wrong direction
reject("schizophrenia",      "used_for",  "neuroimaging studies")    # tool used on it, not for it
reject("cognitive bias",     "used_for",  "problem-solving")         # biases distort, not enable
reject("habituation",        "part_of",   "conditioned response")    # habituation ≠ conditioned response

print("\nStep 2: Theory/process used as derived_from object")
reject("motivation",         "derived_from", "self-determination theory")  # theory not entity
reject("development",        "derived_from", "evolutionary theory")
reject("social influence",   "derived_from", "evolutionary theory")
reject("punishment",         "derived_from", "evolutionary theory")
reject("reward",             "derived_from", "natural selection")           # process

print("\nStep 3: Classic psychology misconceptions")
reject("punishment",         "contains",  "negative reinforcement")   # negative reinforcement ≠ punishment
reject("punishment",         "contains",  "learned helplessness")     # consequence not component
reject("reward",             "is_a",      "pleasure")                 # reward is not_a pleasure

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
print("\nNext: python3 llm_ingest_psychology.py --promote")
