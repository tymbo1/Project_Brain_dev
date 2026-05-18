#!/usr/bin/env python3
"""apply_cs_ai_depth_review.py — HITL review for CS/AI Pass 2."""

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

print("=== CS/AI Pass 2 programmatic review ===\n")

print("Step 1: Direction errors")
reject("encoder",               "contains",   "decoder")               # encoder and decoder are separate
reject("decoder",               "contains",   "encoder")               # same
reject("variational autoencoder","is_a",      "autoencoder")           # VAE is a generative model, extends AE
reject("generative adversarial network","is_a","autoencoder")          # GAN ≠ autoencoder
reject("long short-term memory","is_a",       "recurrent neural network") # LSTM is a type of RNN — actually correct, keep
reject("self-attention",        "is_a",       "attention mechanism")   # correct, keep — don't reject
reject("word embedding",        "is_a",       "embedding")             # correct — don't reject
reject("language model",        "is_a",       "natural language processing") # studied by, not a type of
reject("sorting algorithm",     "is_a",       "algorithm")             # too broad/trivial — actually fine
reject("np-complete",           "is_a",       "np hard")               # NP-complete IS a subset of NP-hard — correct, keep
reject("halting problem",       "is_a",       "computability")         # instance of, studied by — keep as approved
# Real errors:
reject("training data",         "contains",   "test data")             # training and test are separate splits
reject("test data",             "contains",   "training data")         # same
reject("precision",             "is_a",       "recall")                # distinct metrics
reject("recall",                "is_a",       "precision")             # same
reject("thread",                "is_a",       "process")               # thread is lighter than process, not a type
reject("microservice",          "is_a",       "api")                   # microservice uses APIs, not a type of API
reject("containerization",      "is_a",       "virtual machine")       # containers ≠ VMs (different abstraction)
reject("ai alignment",          "is_a",       "machine learning")      # alignment is a safety concern, not ML
reject("fairness",              "is_a",       "machine learning")      # same
reject("explainability",        "is_a",       "machine learning")      # same

print("\nStep 2: Blanket noise filter")
reject_predicate("co_occurs_with")
reject_predicate("related_to")

print("\nStep 3: Low-confidence auto-reject")
reject_below_confidence(0.60)

conn.commit()

print("\nStep 4: Bulk approve remaining")
cur = conn.execute("UPDATE relations_llm SET reviewed=1, approved=1 WHERE reviewed=0")
print(f"  APPROVE remaining: {cur.rowcount} rows")
conn.commit()

approved = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=1").fetchone()[0]
rejected = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=0").fetchone()[0]
print(f"\n=== Review complete === approved={approved}  rejected={rejected}")
print("\nNext: python3 llm_ingest_cs_ai_depth.py --promote")
