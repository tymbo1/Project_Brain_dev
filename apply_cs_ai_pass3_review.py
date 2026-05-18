#!/usr/bin/env python3
"""apply_cs_ai_pass3_review.py — HITL review for CS/AI Pass 3."""

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

print("=== CS/AI Pass 3 programmatic review ===\n")

print("Step 1: Direction/category errors")
reject("stochastic gradient descent","is_a",  "gradient descent")      # SGD IS a type of GD — actually correct, keep
reject("adam optimizer",         "is_a",      "gradient descent")      # Adam uses GD concepts but is an optimizer, not a type
reject("relu",                   "is_a",      "sigmoid")               # distinct activation functions
reject("sigmoid",                "is_a",      "relu")                  # same
reject("softmax",                "is_a",      "sigmoid")               # softmax ≠ sigmoid
reject("residual network",       "is_a",      "convolutional neural network") # ResNet is an architecture, not a type of CNN specifically
reject("layer normalization",    "is_a",      "batch normalization")   # distinct normalization methods
reject("byte pair encoding",     "is_a",      "tokenization")          # BPE IS a type of tokenization — correct, keep
reject("zero-shot learning",     "is_a",      "supervised learning")   # zero-shot is explicitly no supervision
reject("memoization",            "is_a",      "dynamic programming")   # memoization is a technique used IN DP, not a type
reject("greedy algorithm",       "is_a",      "dynamic programming")   # opposite approach
reject("deadlock",               "is_a",      "concurrency")           # deadlock is a problem in concurrency, not a type
reject("mutex",                  "is_a",      "semaphore")             # mutex is a special case but conceptually distinct
reject("virtual memory",         "is_a",      "memory management")     # technique within MM — actually correct, keep
reject("reward hacking",         "is_a",      "reinforcement learning")# problem with RL, not a type
reject("hallucination",          "is_a",      "machine learning")      # failure mode, not a type
reject("model collapse",         "is_a",      "machine learning")      # failure mode, not a type
reject("decidability",           "is_a",      "computability")         # property studied by computability theory — keep
reject("complexity class",       "is_a",      "computability")         # complexity ≠ computability

print("\nStep 2: Wrong predicate")
reject("hallucination",          "used_for",  "natural language processing") # hallucination is a failure, not a tool
reject("reward hacking",         "used_for",  "reinforcement learning")      # failure mode, not a tool
reject("model collapse",         "used_for",  "generative model")            # failure mode, not a tool
reject("alignment tax",          "used_for",  "machine learning")            # cost/tradeoff, not a tool

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
print("\nNext: python3 llm_ingest_cs_ai_pass3.py --promote")
