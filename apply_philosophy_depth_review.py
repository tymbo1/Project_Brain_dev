#!/usr/bin/env python3
"""apply_philosophy_depth_review.py — HITL review for Philosophy Pass 2."""

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

print("=== Philosophy Pass 2 programmatic review ===\n")

print("Step 1: Direction errors")
reject("qualia",                "is_a",       "consciousness")          # qualia is a component, not a type
reject("intentionality",        "is_a",       "consciousness")          # intentionality is a feature of mind
reject("functionalism",         "is_a",       "physicalism")            # functionalism is compatible but distinct
reject("eliminativism",         "is_a",       "physicalism")            # eliminativism is a radical position
reject("hard problem of consciousness", "is_a", "philosophy of mind")   # studied by, not a type of
reject("phenomenal consciousness","is_a",     "philosophy of mind")     # same
reject("propositional logic",   "contains",   "predicate logic")        # predicate logic extends propositional
reject("formal logic",          "is_a",       "propositional logic")    # formal logic is broader
reject("coherentism",           "is_a",       "foundationalism")        # they are rival theories
reject("foundationalism",       "is_a",       "coherentism")            # same
reject("libertarianism",        "is_a",       "liberalism")             # distinct positions in political phil
reject("communitarianism",      "is_a",       "liberalism")             # communitarianism critiques liberalism
reject("scientific realism",    "is_a",       "philosophy of science")  # studied by, not a type of

print("\nStep 2: Theory-as-entity errors")
reject("qualia",                "derived_from", "evolutionary theory")
reject("intentionality",        "derived_from", "evolutionary theory")
reject("functionalism",         "derived_from", "evolutionary theory")
reject("moral agency",          "derived_from", "evolutionary theory")
reject("social contract",       "derived_from", "evolutionary theory")

print("\nStep 3: Category errors in phil of mind")
reject("physicalism",           "contains",   "dualism")               # physicalism rejects dualism
reject("eliminativism",         "contains",   "dualism")               # same
reject("property dualism",      "is_a",       "materialism")           # property dualism is not materialism
reject("epiphenomenalism",      "is_a",       "functionalism")         # distinct positions

print("\nStep 4: Logic direction errors")
reject("validity",              "is_a",       "soundness")             # soundness requires validity, not reverse
reject("soundness",             "is_a",       "validity")              # soundness implies validity, not a type

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
print("\nNext: python3 llm_ingest_philosophy_depth.py --promote")
