#!/usr/bin/env python3
"""apply_philosophy_review.py — HITL review for Philosophy Pass 1."""

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

print("=== Philosophy Pass 1 programmatic review ===\n")

print("Step 1: Direction errors")
reject("epistemology",       "contains",    "philosophy")          # branch not parent
reject("metaphysics",        "contains",    "philosophy")          # same
reject("logic",              "contains",    "philosophy")          # same
reject("ethics",             "contains",    "philosophy")          # same
reject("consciousness",      "is_a",        "philosophy of mind")  # consciousness is studied by, not a type of
reject("free will",          "is_a",        "determinism")         # free will is distinct from determinism
reject("determinism",        "contains",    "free will")           # free will is distinct, not contained
reject("morality",           "is_a",        "ethics")              # morality is studied by ethics, not a type
reject("truth",              "is_a",        "epistemology")        # truth is studied by, not a type of
reject("knowledge",          "is_a",        "epistemology")        # same

print("\nStep 2: Theory-as-entity errors")
reject("pragmatism",         "derived_from", "evolutionary theory")   # theory not entity
reject("naturalism",         "derived_from", "evolutionary theory")   # theory not entity
reject("existentialism",     "derived_from", "evolutionary theory")   # wrong entirely

print("\nStep 3: Conflation errors")
reject("relativism",         "is_a",        "skepticism")          # distinct positions
reject("idealism",           "is_a",        "dualism")             # idealism rejects dualism
reject("materialism",        "is_a",        "dualism")             # materialism opposes dualism
reject("phenomenology",      "is_a",        "existentialism")      # phenomenology precedes/underlies it
reject("analytic philosophy","contains",    "continental philosophy") # mutually exclusive traditions
reject("continental philosophy","contains", "analytic philosophy") # same

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
print("\nNext: python3 llm_ingest_philosophy.py --promote")
