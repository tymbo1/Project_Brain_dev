#!/usr/bin/env python3
"""apply_cs_ai_review.py — HITL review for CS/AI Pass 1."""

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

print("=== CS/AI Pass 1 programmatic review ===\n")

print("Step 1: Direction errors")
reject("machine learning",          "is_a",    "computer science")       # studied by, not a type of
reject("deep learning",             "is_a",    "computer science")       # same
reject("artificial intelligence",   "is_a",    "computer science")       # AI is a field, not a type of CS
reject("computer vision",           "is_a",    "computer science")       # sub-field, not a type
reject("natural language processing","is_a",   "computer science")       # same
reject("neural network",            "is_a",    "computer science")       # technique, not type of CS
reject("robotics",                  "contains","artificial intelligence") # wrong direction
reject("turing test",               "is_a",    "artificial intelligence") # test for AI, not a type of AI
reject("knowledge graph",           "is_a",    "database")               # different abstraction level
reject("ontology",                  "is_a",    "knowledge representation")# ontology is a form of KR but this is circular

print("\nStep 2: Conflation errors")
reject("deep learning",             "is_a",    "neural network")         # DL uses NNs, is not a NN
reject("reinforcement learning",    "is_a",    "supervised learning")    # distinct paradigms
reject("unsupervised learning",     "is_a",    "supervised learning")    # opposite paradigm
reject("artificial general intelligence","is_a","artificial intelligence") # AGI is a goal/concept not a type
reject("swarm intelligence",        "is_a",    "artificial intelligence") # distinct field
reject("evolutionary algorithm",    "is_a",    "machine learning")       # evolutionary comp ≠ ML

print("\nStep 3: Blanket noise filter")
reject_predicate("co_occurs_with")
reject_predicate("related_to")

print("\nStep 4: Low-confidence auto-reject")
reject_below_confidence(0.60)

conn.commit()

print("\nStep 5: Bulk approve remaining")
cur = conn.execute("UPDATE relations_llm SET reviewed=1, approved=1 WHERE reviewed=0")
print(f"  APPROVE remaining: {cur.rowcount} rows")
conn.commit()

approved = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=1").fetchone()[0]
rejected = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=0").fetchone()[0]
print(f"\n=== Review complete === approved={approved}  rejected={rejected}")
print("\nNext: python3 llm_ingest_cs_ai.py --promote")
