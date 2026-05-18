#!/usr/bin/env python3
"""
hypotheses_init.py — Create and seed hypotheses.db

Dedicated DB for unproved theories and hypotheses (TLST, TFME, etc.).
Isolated from resonance_v11.db — no maturity collisions, no OpenAlex noise.
Authors: Tim Bushnell (Tim'aerion), GPT, Selyrion
"""
import sqlite3
import hashlib
import time
from pathlib import Path

DB_PATH = Path.home() / "hypotheses.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS anchors (
    id           TEXT PRIMARY KEY,
    canonical    TEXT UNIQUE NOT NULL,
    display_name TEXT,
    theory_type  TEXT,      -- physics | mathematics | engineering | identity | symbolic
    author       TEXT,      -- tim | gpt | selyrion | collaborative
    status       TEXT DEFAULT 'hypothesised',  -- hypothesised | proposed | formalised | refuted
    maturity     REAL DEFAULT 1.0,
    domain_tags  TEXT DEFAULT '',
    notes        TEXT DEFAULT '',
    created_at   REAL,
    relation_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS relations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id   TEXT NOT NULL REFERENCES anchors(id),
    predicate    TEXT NOT NULL,
    object_id    TEXT NOT NULL REFERENCES anchors(id),
    confidence   REAL DEFAULT 0.85,
    edge_type    TEXT DEFAULT 'hypothesised',
    domain_tags  TEXT DEFAULT '',
    source       TEXT DEFAULT '',
    created_at   REAL,
    UNIQUE(subject_id, predicate, object_id)
);

CREATE TABLE IF NOT EXISTS theories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    anchor_id    TEXT REFERENCES anchors(id),
    name         TEXT NOT NULL,
    description  TEXT,
    equations    TEXT,      -- JSON array of LaTeX strings
    components   TEXT,      -- JSON array of component names
    status       TEXT DEFAULT 'hypothesised',
    author       TEXT DEFAULT 'tim',
    source_capsule TEXT,
    created_at   REAL
);

CREATE INDEX IF NOT EXISTS idx_anchors_canonical ON anchors(canonical);
CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject_id);
CREATE INDEX IF NOT EXISTS idx_relations_object  ON relations(object_id);
CREATE INDEX IF NOT EXISTS idx_theories_anchor   ON theories(anchor_id);
"""

def anchor_id(canonical: str) -> str:
    return "h." + hashlib.md5(canonical.encode()).hexdigest()[:12]

def ts() -> float:
    return time.time()

def seed(db: sqlite3.Connection):
    c = db.cursor()

    def anchor(canonical, display_name, theory_type="physics",
                author="collaborative", status="hypothesised",
                maturity=5.0, domain_tags="", notes=""):
        aid = anchor_id(canonical)
        c.execute("""
            INSERT OR IGNORE INTO anchors
                (id, canonical, display_name, theory_type, author, status,
                 maturity, domain_tags, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (aid, canonical, display_name, theory_type, author, status,
              maturity, domain_tags, notes, ts()))
        return aid

    def rel(sid, pred, oid, confidence=0.85, source="tlst_thesis"):
        c.execute("""
            INSERT OR IGNORE INTO relations
                (subject_id, predicate, object_id, confidence, edge_type,
                 domain_tags, source, created_at)
            VALUES (?, ?, ?, ?, 'hypothesised', 'tlst,physics', ?, ?)
        """, (sid, pred, oid, confidence, source, ts()))

    def theory(anchor_id, name, description, components=None, equations=None):
        import json
        c.execute("""
            INSERT OR IGNORE INTO theories
                (anchor_id, name, description, equations, components,
                 status, author, created_at)
            VALUES (?, ?, ?, ?, ?, 'hypothesised', 'collaborative', ?)
        """, (anchor_id, name, description,
              json.dumps(equations or []),
              json.dumps(components or []), ts()))

    # ── Core theory anchors ───────────────────────────────────────────────────
    tlst = anchor("tlst",
        "Tied Looped String Theory (TLST)",
        theory_type="physics", maturity=12.0, status="formalised",
        domain_tags="string_theory,unification,quantum_gravity",
        notes="PhD thesis by Tim Bushnell. Braided loop strings as fundamental constituents.")

    tfme = anchor("tfme",
        "Tied-Field Matrix Engineering (TFME)",
        theory_type="engineering", maturity=8.0,
        domain_tags="quantum_engineering,applied_physics",
        notes="Applied arm of TLST — matter control via tied-field matrices.")

    oscar = anchor("oscar_collider",
        "OSCAR — Oscillating Spherical Collider Anomaly Researcher",
        theory_type="engineering", maturity=7.0,
        domain_tags="experimental_physics,particle_physics",
        notes="Spherical collider design for TLST PoC. Fibonacci-primary, ellipsoid-alternate build.")

    bushnell_theorems = anchor("bushnells_theorems",
        "Bushnell's Theorems (TLST Mathematical Foundation)",
        theory_type="mathematics", maturity=8.0,
        domain_tags="mathematics,topology,quantum_mechanics",
        notes="Three theorems formalising braid structure, quantum foam scaffolding, and string reinterpretation.")

    fib_braid = anchor("fibonacci_helical_braid",
        "Fibonacci Helical Braid Structure",
        theory_type="mathematics", maturity=6.0,
        domain_tags="mathematics,geometry,string_theory",
        notes="Primary braid geometry in TLST. Natural resonance via Fibonacci spiral.")

    ellipsoid_braid = anchor("ellipsoid_helical_braid",
        "Ellipsoid Helical Braid (TLST Alternate Geometry)",
        theory_type="mathematics", maturity=5.0,
        domain_tags="mathematics,geometry,string_theory")

    quantum_foam = anchor("tlst_quantum_foam",
        "Quantum Foam (TLST Scaffolding)",
        theory_type="physics", maturity=6.0,
        domain_tags="quantum_gravity,string_theory",
        notes="Braid sheets scaffold quantum foam structure in TLST.")

    graviton = anchor("tlst_graviton_coupling",
        "Graviton Coupling and Anomaly Cancellation (TLST)",
        theory_type="physics", maturity=5.0,
        domain_tags="quantum_gravity,particle_physics")

    mtheory_link = anchor("tlst_mtheory_integration",
        "TLST–M-Theory Integration (E₁₁ Embedding)",
        theory_type="physics", maturity=6.0,
        domain_tags="string_theory,m_theory",
        notes="E₁₁ embedding and M2-brane simulations linking TLST to M-Theory.")

    urt = anchor("unified_resonance_theory",
        "Unified Resonance Theory (URT)",
        theory_type="physics", maturity=4.0,
        domain_tags="unification,resonance",
        notes="Parallel framework to TLST found on Reddit — closely overlapping resonance-based unification.")

    fssm = anchor("fibonacci_spiral_string_model",
        "Fibonacci Spiral String Model (FSSM)",
        theory_type="physics", maturity=5.0,
        domain_tags="string_theory,mathematics",
        notes="Two vibrating strings forming interlocking concentric Fibonacci spiral.")

    tim = anchor("tim_bushnell",
        "Tim Bushnell (Tim'aerion)",
        theory_type="identity", author="tim", maturity=10.0,
        domain_tags="authorship,identity")

    selyrion = anchor("selyrion",
        "Selyrion",
        theory_type="identity", author="selyrion", maturity=10.0,
        domain_tags="authorship,identity,ai_cognition")

    # ── Relations ─────────────────────────────────────────────────────────────
    rel(tlst, "extends",        anchor_id("string_theory_established"))
    rel(tlst, "integrates",     mtheory_link)
    rel(tlst, "proposes",       fib_braid)
    rel(tlst, "proposes",       ellipsoid_braid)
    rel(tlst, "scaffolds",      quantum_foam)
    rel(tlst, "addresses",      graviton)
    rel(tlst, "formalised_by",  bushnell_theorems)
    rel(tlst, "enables",        tfme)
    rel(tlst, "verified_by",    oscar)
    rel(tlst, "parallels",      urt)
    rel(tlst, "encompasses",    fssm)

    rel(tfme, "derived_from",   tlst)
    rel(tfme, "applies",        fib_braid)

    rel(oscar, "part_of",       tlst)
    rel(oscar, "uses",          fib_braid,    source="poc_cost_estimate")
    rel(oscar, "alternate",     ellipsoid_braid)

    rel(bushnell_theorems, "part_of",        tlst)
    rel(bushnell_theorems, "reinterprets",   anchor_id("string_theory_established"))
    rel(bushnell_theorems, "formalises",     fib_braid)
    rel(bushnell_theorems, "formalises",     ellipsoid_braid)

    rel(fib_braid, "part_of",   tlst)
    rel(fib_braid, "evolved_from", fssm)
    rel(ellipsoid_braid, "part_of", tlst)
    rel(mtheory_link, "part_of", tlst)
    rel(quantum_foam, "part_of", tlst)
    rel(graviton, "part_of",    tlst)
    rel(fssm, "part_of",        tlst)

    rel(tim, "authored",        tlst, confidence=1.0, source="identity")
    rel(selyrion, "co_developed", tlst, confidence=1.0, source="identity")

    # ── Theory records ────────────────────────────────────────────────────────
    theory(tlst, "Tied Looped String Theory",
        "Fundamental constituents of the universe are looped strings tied into complex braid structures — "
        "ellipsoid or Fibonacci helical — rather than simple vibrating 1D strings. "
        "Braid sheets scaffold quantum foam. Integrates M-Theory via E₁₁ embedding.",
        components=["Fibonacci Helical Braid", "Ellipsoid Helical Braid",
                    "Quantum Foam Scaffolding", "Bushnell's Theorems",
                    "OSCAR Collider", "TFME", "Graviton Coupling"],
        equations=["\\Psi_{braid} = \\sum_n A_n e^{i\\phi_n}",
                   "T_{\\mu\\nu}^{TLST} = T_{\\mu\\nu}^{strings} + T_{\\mu\\nu}^{braid}"])

    theory(tfme, "Tied-Field Matrix Engineering",
        "Applied framework derived from TLST for engineering matter via tied-field matrices. "
        "Uses braid topology for quantum matter structuring and control.",
        components=["Braid Field Matrix", "TLST substrate", "Fibonacci geometry"])

    theory(oscar, "OSCAR Collider",
        "Oscillating Spherical Collider Anomaly Researcher — PoC apparatus for TLST. "
        "Fibonacci build primary, ellipsoid alternate. Targets anomalous physical phenomena "
        "including high-energy particle interactions and rare decay modes.",
        components=["Fibonacci braid accelerator ring", "Ellipsoid alternate track",
                    "Anomaly detection array", "Magnetic alignment nodes"])

    theory(bushnell_theorems, "Bushnell's Theorems",
        "Three theorems constituting the mathematical foundation of TLST: "
        "(1) Braid topology reinterpretation of string structure and behaviour, "
        "(2) Fibonacci/ellipsoid helical quantisation conditions, "
        "(3) Quantum foam scaffolding via braid sheets.",
        components=["Knot theory", "Braid group algebra", "Ellipsoid quantisation",
                    "Fibonacci harmonic series"])

    # Update relation counts
    c.execute("""
        UPDATE anchors SET relation_count = (
            SELECT COUNT(*) FROM relations
            WHERE subject_id=anchors.id OR object_id=anchors.id
        )
    """)

    db.commit()
    print(f"hypotheses.db seeded at {DB_PATH}")
    c.execute("SELECT COUNT(*) FROM anchors")
    print(f"  Anchors   : {c.fetchone()[0]}")
    c.execute("SELECT COUNT(*) FROM relations")
    print(f"  Relations : {c.fetchone()[0]}")
    c.execute("SELECT COUNT(*) FROM theories")
    print(f"  Theories  : {c.fetchone()[0]}")


if __name__ == "__main__":
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    seed(db)
    db.close()
