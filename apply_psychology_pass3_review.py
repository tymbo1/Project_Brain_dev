#!/usr/bin/env python3
"""apply_psychology_pass3_review.py — HITL review for Psychology Pass 3."""

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

print("=== Psychology Pass 3 programmatic review ===\n")

print("Step 1: Biology category errors")
reject("cortisol",                  "is_a",      "neurotransmitter")       # cortisol is a hormone
reject("autonomic nervous system",  "part_of",   "central nervous system") # ANS is peripheral NS
reject("cortisol",                  "requires",  "glucagon")               # cortisol requires ACTH, not glucagon
reject("unconscious",               "requires",  "neurotransmitters")      # psychological construct ≠ biological requirement
reject("unconscious",               "requires",  "brain regions")          # same category error

print("\nStep 2: Direction errors")
reject("neurogenesis",              "part_of",   "adult neurogenesis")     # adult neurogenesis is a type of neurogenesis
reject("retrieval",                 "part_of",   "recall")                 # recall uses retrieval, not vice versa
reject("emotional memory",          "part_of",   "hippocampal formation")  # hippocampus contains EM circuits, not reverse
reject("reappraisal",               "contains",  "cognitive reappraisal")  # circular — cognitive reappraisal IS reappraisal
reject("alexithymia",               "part_of",   "personality disorders")  # alexithymia is a trait dimension

print("\nStep 3: Theory-as-entity and composition errors")
reject("bystander effect",          "contains",  "social identity theory") # theory ≠ component
reject("free association",          "part_of",   "cognitive psychology")   # free association is psychoanalytic
reject("catharsis",                 "derived_from", "evolutionary theory") # theory not entity
reject("cognitive restructuring",   "derived_from", "social learning theory") # theory not entity; also wrong domain
reject("insecure attachment",       "derived_from", "attachment theory")   # theory not entity
reject("transference",              "derived_from", "attachment theory")   # theory not entity

print("\nStep 4: Wrong predicate")
reject("social comparison",         "used_for",  "emotional intelligence") # SC doesn't produce EI

print("\nStep 5: Blanket noise filter")
reject_predicate("co_occurs_with")
reject_predicate("related_to")

print("\nStep 6: Low-confidence auto-reject")
reject_below_confidence(0.60)

conn.commit()

print("\nStep 7: Bulk approve remaining")
cur = conn.execute("UPDATE relations_llm SET reviewed=1, approved=1 WHERE reviewed=0")
print(f"  APPROVE remaining: {cur.rowcount} rows")
conn.commit()

approved = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=1").fetchone()[0]
rejected = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=0").fetchone()[0]
print(f"\n=== Review complete === approved={approved}  rejected={rejected}")
print("\nNext: python3 llm_ingest_psychology_pass3.py --promote")
