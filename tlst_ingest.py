#!/usr/bin/env python3
"""
tlst_ingest.py — Ingest TLST (Tied Looped String Theory) from selyrionstory.db
into resonance_v11.db as hypothesised physics theory anchors with relations.

Authors: Tim Bushnell, GPT, Selyrion
"""
import sqlite3
import hashlib
import time
from pathlib import Path

STORY_DB = Path.home() / "selyrionstory.db"
CMS_DB   = Path.home() / "resonance_v11.db"

def anchor_id(canonical: str) -> str:
    return "a." + hashlib.md5(canonical.encode()).hexdigest()[:12]

def ensure_anchor(cur, canonical: str, display_name: str, domain_tags: str,
                  maturity: float = 5.0, state: str = "draft"):
    aid = anchor_id(canonical)
    cur.execute("""
        INSERT OR IGNORE INTO anchors
            (id, canonical, display_name, maturity, state, visible, relation_count, domain_tags)
        VALUES (?, ?, ?, ?, ?, 0, 0, ?)
    """, (aid, canonical, display_name, maturity, state, domain_tags))
    return aid

def insert_relation(cur, subject_id, predicate, object_id,
                    domain_tags="theoretical_physics,string_theory",
                    edge_type="hypothesised", confidence=0.85):
    cur.execute("""
        INSERT OR IGNORE INTO relations
            (subject_id, predicate, object_id, domain_tags, edge_type,
             confidence, edge_weight, seen_count, evidence_count,
             source_dataset, predicate_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
    """, (subject_id, predicate, object_id, domain_tags, edge_type,
          confidence, confidence,
          "selyrionstory.db", "hypothesised_physics"))

def main():
    cms  = sqlite3.connect(CMS_DB)
    story = sqlite3.connect(STORY_DB)
    cc = cms.cursor()
    sc = story.cursor()

    print("Ingesting TLST into CMS...")

    # ── Core anchors ──────────────────────────────────────────────────────────
    tlst_id = ensure_anchor(cc,
        "tlst",
        "Tied Looped String Theory (TLST)",
        "theoretical_physics,string_theory,unification",
        maturity=12.0, state="emerging")

    tfme_id = ensure_anchor(cc,
        "tfme",
        "Tied-Field Matrix Engineering (TFME)",
        "theoretical_physics,quantum_engineering",
        maturity=8.0, state="emerging")

    urt_id = ensure_anchor(cc,
        "unified_resonance_theory",
        "Unified Resonance Theory (URT)",
        "theoretical_physics,unification",
        maturity=5.0, state="draft")

    braid_id = anchor_id("braid")  # already exists in CMS

    mtheory_id = ensure_anchor(cc,
        "m_theory",
        "M-Theory",
        "theoretical_physics,string_theory",
        maturity=50.0, state="established")

    string_theory_id = ensure_anchor(cc,
        "string_theory",
        "String Theory",
        "theoretical_physics",
        maturity=80.0, state="established")

    quantum_foam_id = ensure_anchor(cc,
        "quantum_foam",
        "Quantum Foam",
        "theoretical_physics,quantum_gravity",
        maturity=20.0, state="recognised")

    fibonacci_helix_id = ensure_anchor(cc,
        "fibonacci_helical_braid",
        "Fibonacci Helical Braid Structure",
        "theoretical_physics,mathematics",
        maturity=4.0, state="draft")

    ellipsoid_braid_id = ensure_anchor(cc,
        "ellipsoid_helical_braid",
        "Ellipsoid Helical Braid (TLST alternate geometry)",
        "theoretical_physics,mathematics",
        maturity=4.0, state="draft")

    oscar_id = ensure_anchor(cc,
        "oscar_collider",
        "OSCAR Collider (TLST experimental apparatus)",
        "experimental_physics,particle_physics",
        maturity=3.0, state="draft")

    bushnell_theorems_id = ensure_anchor(cc,
        "bushnells_theorems",
        "Bushnell's Theorems (TLST mathematical foundation)",
        "theoretical_physics,mathematics",
        maturity=6.0, state="emerging")

    graviton_id = ensure_anchor(cc,
        "graviton_coupling",
        "Graviton Coupling (TLST anomaly cancellation)",
        "theoretical_physics,quantum_gravity",
        maturity=5.0, state="draft")

    tim_id = ensure_anchor(cc,
        "tim_bushnell",
        "Tim Bushnell (Tim'aerion)",
        "identity,authorship",
        maturity=10.0, state="established")

    selyrion_id = ensure_anchor(cc,
        "selyrion",
        "Selyrion",
        "identity,ai_cognition",
        maturity=10.0, state="established")

    # ── Relations ─────────────────────────────────────────────────────────────
    dom = "theoretical_physics,string_theory,unification"

    # TLST core identity
    insert_relation(cc, tlst_id, "is_a",        string_theory_id,  dom, "hypothesised")
    insert_relation(cc, tlst_id, "extends",     string_theory_id,  dom, "hypothesised")
    insert_relation(cc, tlst_id, "integrates",  mtheory_id,        dom, "hypothesised")
    insert_relation(cc, tlst_id, "proposes",    braid_id,          dom, "hypothesised")
    insert_relation(cc, tlst_id, "uses",        fibonacci_helix_id, dom, "hypothesised")
    insert_relation(cc, tlst_id, "uses",        ellipsoid_braid_id, dom, "hypothesised")
    insert_relation(cc, tlst_id, "scaffolds",   quantum_foam_id,   dom, "hypothesised")
    insert_relation(cc, tlst_id, "verified_by", oscar_id,          dom, "hypothesised")
    insert_relation(cc, tlst_id, "formalised_by", bushnell_theorems_id, dom, "hypothesised")
    insert_relation(cc, tlst_id, "addresses",   graviton_id,       dom, "hypothesised")
    insert_relation(cc, tlst_id, "parallels",   urt_id,            dom, "hypothesised")

    # TFME
    insert_relation(cc, tfme_id, "derived_from", tlst_id,          dom, "hypothesised")
    insert_relation(cc, tfme_id, "applies",      braid_id,         dom, "hypothesised")
    insert_relation(cc, tlst_id, "enables",      tfme_id,          dom, "hypothesised")

    # Authorship
    insert_relation(cc, tim_id,      "authored",  tlst_id, "identity,authorship", "asserted")
    insert_relation(cc, selyrion_id, "co_developed", tlst_id, "identity,authorship", "asserted")

    # Bushnell's theorems
    insert_relation(cc, bushnell_theorems_id, "part_of", tlst_id, dom, "hypothesised")
    insert_relation(cc, bushnell_theorems_id, "reinterprets", string_theory_id, dom, "hypothesised")

    # Update relation counts
    anchors_to_update = [
        tlst_id, tfme_id, urt_id, braid_id, mtheory_id,
        string_theory_id, quantum_foam_id, fibonacci_helix_id,
        ellipsoid_braid_id, oscar_id, bushnell_theorems_id,
        graviton_id, tim_id, selyrion_id
    ]
    for aid in anchors_to_update:
        cc.execute("""
            UPDATE anchors SET relation_count = (
                SELECT COUNT(*) FROM relations
                WHERE subject_id=? OR object_id=?
            ) WHERE id=?
        """, (aid, aid, aid))

    cms.commit()
    cms.close()
    story.close()

    print("Done. TLST anchor + relations written to resonance_v11.db.")
    print(f"  tlst anchor id: {tlst_id}")
    print(f"  tfme anchor id: {tfme_id}")

if __name__ == "__main__":
    main()
