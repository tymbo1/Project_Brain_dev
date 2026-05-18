#!/usr/bin/env python3
"""
apply_medicine_depth_review.py — HITL review for Medicine Pass 2 (and future depth passes).

Applies GPT-reviewed decisions. Medicine depth layer = causal + structural only.
co_occurs_with and related_to excluded (imprecise without causal predicates).
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

def reject_predicate(pred: str):
    cur = conn.execute(
        "UPDATE relations_llm SET reviewed=1, approved=0 WHERE predicate=? AND reviewed=0",
        (pred,)
    )
    print(f"  REJECT all predicate='{pred}': {cur.rowcount} rows")

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

print("=== Medicine Pass 2 programmatic review ===\n")

print("Step 1: Direction / ontology errors")
reject("nervous system",    "is_a",          "central nervous system")
reject("muscular system",   "is_a",          "skeletal system")
reject("chromosome",        "part_of",       "mitochondrion")
reject("chromosome",        "part_of",       "chromatid")
reject("dementia",          "distinct_from", "alzheimer's disease")
reject("dementia",          "enables",       "neuroplasticity")
reject("dementia",          "enables",       "cognitive reserve")
reject("outcome",           "derived_from",  "mortality")
reject("diagnosis",         "enables",       "morbidity")
reject("morbidity",         "enables",       "treatment")
reject("depression",        "used_for",      "treatment")
reject("homeostasis",       "part_of",       "endocrine system")
reject("respiration",       "part_of",       "cardiovascular system")
reject("hormone",           "part_of",       "pancreas")
reject("hormone",           "part_of",       "adrenal gland")
reject("lymphocyte",        "co_occurs_with","t-cell")
reject("neurotransmitter",  "derived_from",  "tyrosine")
reject("schizophrenia",     "used_for",      "psychiatric diagnosis")

print("\nStep 2: Field-contains-instance violations")
reject("antibiotic",        "contains",      "penicillin")
reject("antibiotic",        "contains",      "ceftriaxone")
reject("antibiotic",        "contains",      "azithromycin")

print("\nStep 3: Borderline rejects")
reject("vaccine",           "contains",      "toxin")
reject("epigenetics",       "is_a",          "genomics")
reject("bacteria",          "part_of",       "ecosystem")

print("\nStep 4: Blanket noise filter (depth layer = causal/structural only)")
reject_predicate("co_occurs_with")
reject_predicate("related_to")

print("\nStep 5: Low-confidence auto-reject")
reject_below_confidence(0.36)

conn.commit()

print("\nStep 6: Bulk approve remaining")
bulk_approve_remaining()
conn.commit()

total    = conn.execute("SELECT COUNT(*) FROM relations_llm").fetchone()[0]
approved = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=1").fetchone()[0]
rejected = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=0").fetchone()[0]
pending  = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=0").fetchone()[0]

print(f"\n=== Review complete ===")
print(f"  total={total}  approved={approved}  rejected={rejected}  pending={pending}")
print(f"\nNext: python3 llm_ingest_medicine_depth.py --promote")
