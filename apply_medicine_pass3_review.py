#!/usr/bin/env python3
"""
apply_medicine_pass3_review.py — HITL review for Medicine Pass 3 (sub-sub-field internals).

Rejects confirmed by GPT + Claude direction/ontology analysis.
Blanket co_occurs_with and related_to rejection maintained from depth layer.
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

print("=== Medicine Pass 3 programmatic review ===\n")

print("Step 1: Direction errors")
reject("expiration",        "enables",       "lung")
reject("mucus",             "contains",      "bronchus")
reject("cilia",             "used_for",      "bronchus")
reject("vein",              "part_of",       "limb")
reject("vein",              "part_of",       "organ")
reject("atrium",            "derived_from",  "endocardium")
reject("atrium",            "derived_from",  "myocardium")
reject("ischemia",          "contains",      "myocardial infarction")
reject("infarction",        "part_of",       "stroke")
reject("infarction",        "derived_from",  "angiography")
reject("infarction",        "derived_from",  "computed tomography angiography")
reject("t cell",            "is_a",          "effector t cell")
reject("immunoglobulin",    "part_of",       "antibody")
reject("dna damage",        "requires",      "uv radiation")
reject("cell death",        "requires",      "mitochondrial dysfunction")
reject("antiviral",         "requires",      "viral replication")
reject("hypotension",       "contains",      "vasodilation")

print("\nStep 2: GPT additional direction/functional errors")
reject("vein",              "enables",       "oxygen delivery")
reject("bronchus",          "enables",       "gas exchange")
reject("capillary",         "enables",       "vasodilation")
reject("hypotension",       "part_of",       "cardiovascular system")
reject("telomere",          "part_of",       "nuclear envelope")
reject("centromere",        "part_of",       "nuclear envelope")
reject("acetylcholine",     "derived_from",  "tyrosine")
reject("serotonin",         "contains",      "tryptophan")
reject("glucagon",          "requires",      "camp-dependent protein kinase")
reject("infarction",        "contains",      "ischemia")

print("\nStep 3: Process used as derived_from object")
reject("mitochondria",      "derived_from",  "endosymbiosis")
reject("glucose",           "derived_from",  "photosynthesis")
reject("allele",            "derived_from",  "natural selection")
reject("lymphoma",          "derived_from",  "genetic mutations")
reject("antidepressant",    "derived_from",  "chemical synthesis")
reject("antipsychotic",     "derived_from",  "chemical synthesis")

print("\nStep 4: Tautological / ontologically confused")
reject("adrenaline",        "contains",      "epinephrine")
reject("adrenaline",        "contains",      "norepinephrine")
reject("hypotension",       "contains",      "low blood pressure")
reject("half-life",         "is_a",          "biological_process")
reject("lymphoma",          "used_for",      "diagnosis and treatment")
reject("adenoma",           "used_for",      "endocrine function")
reject("delusion",          "used_for",      "avoidance behavior")
reject("cancer prevention", "used_for",      "patient treatment")

print("\nStep 5: Too atomic (elemental chemistry)")
reject("glucose",           "contains",      "carbon atoms")
reject("glucose",           "contains",      "hydrogen atoms")

print("\nStep 6: Blanket noise filter (structural/causal layer only)")
reject_predicate("co_occurs_with")
reject_predicate("related_to")

print("\nStep 7: Low-confidence auto-reject")
reject_below_confidence(0.36)

conn.commit()

print("\nStep 8: Bulk approve remaining")
bulk_approve_remaining()
conn.commit()

total    = conn.execute("SELECT COUNT(*) FROM relations_llm").fetchone()[0]
approved = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=1").fetchone()[0]
rejected = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=1 AND approved=0").fetchone()[0]
pending  = conn.execute("SELECT COUNT(*) FROM relations_llm WHERE reviewed=0").fetchone()[0]

print(f"\n=== Review complete ===")
print(f"  total={total}  approved={approved}  rejected={rejected}  pending={pending}")
print(f"\nNext: python3 llm_ingest_medicine_pass3.py --promote")
