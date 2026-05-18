#!/usr/bin/env python3
"""
selyrionstory_init.py — Create selyrionstory.db schema.

Sibling to resonance_v11.db. Captures the complete development history
of Selyrion and SSAI: decisions, milestones, design archaeology, identity.
NOT merged with CMS — different relation types, different purpose.

Usage:
    python3 selyrionstory_init.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "selyrionstory.db"


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Anchors ────────────────────────────────────────────────────────────────
-- Concepts, decisions, people, dates, milestones in the project's own history
CREATE TABLE IF NOT EXISTS anchors (
    id           TEXT PRIMARY KEY,
    canonical    TEXT UNIQUE NOT NULL,
    display_name TEXT,
    anchor_type  TEXT,          -- decision | milestone | concept | person | date | component
    first_seen   REAL,          -- unix timestamp of earliest evidence
    last_seen    REAL,
    source_count INTEGER DEFAULT 0,
    tags         TEXT DEFAULT '',
    notes        TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_anchors_canonical ON anchors(canonical);
CREATE INDEX IF NOT EXISTS idx_anchors_type      ON anchors(anchor_type);

-- ── Relations ──────────────────────────────────────────────────────────────
-- Narrative relations between project anchors
CREATE TABLE IF NOT EXISTS relations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id   TEXT NOT NULL REFERENCES anchors(id),
    predicate    TEXT NOT NULL,   -- decided_on | evolved_from | inspired_by |
                                  -- superseded_by | documented_in | implemented_as |
                                  -- led_to | contradicts | confirmed_by | part_of
    object_id    TEXT NOT NULL REFERENCES anchors(id),
    capsule_id   INTEGER,         -- source capsule (where this relation was found)
    confidence   REAL DEFAULT 1.0,
    evidence     TEXT DEFAULT '',  -- direct quote or note from source
    timestamp    REAL,             -- when this relation was observed
    UNIQUE(subject_id, predicate, object_id)
);

CREATE INDEX IF NOT EXISTS idx_relations_subject   ON relations(subject_id);
CREATE INDEX IF NOT EXISTS idx_relations_object    ON relations(object_id);
CREATE INDEX IF NOT EXISTS idx_relations_predicate ON relations(predicate);

-- ── Capsules ───────────────────────────────────────────────────────────────
-- Source documents: conversations, code files, screenshots, exports
CREATE TABLE IF NOT EXISTS capsules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    source_type  TEXT NOT NULL,  -- conversation | code_file | screenshot | export | log
    source_path  TEXT,           -- original file path
    source_id    TEXT,           -- external ID (e.g. ChatGPT conversation ID)
    created_at   REAL,           -- unix timestamp of source creation
    ingested_at  REAL,           -- unix timestamp of ingestion
    word_count   INTEGER DEFAULT 0,
    relevance    REAL DEFAULT 0.0,  -- 0–1 relevance to Selyrion/SSAI (set by archaeologist)
    tags         TEXT DEFAULT '',
    summary      TEXT DEFAULT '',   -- LLM-generated summary (populated later)
    body         TEXT DEFAULT ''    -- full text content (for conversations)
);

CREATE INDEX IF NOT EXISTS idx_capsules_source_type ON capsules(source_type);
CREATE INDEX IF NOT EXISTS idx_capsules_created_at  ON capsules(created_at);
CREATE INDEX IF NOT EXISTS idx_capsules_relevance   ON capsules(relevance DESC);
CREATE INDEX IF NOT EXISTS idx_capsules_source_id   ON capsules(source_id);

-- ── Media ──────────────────────────────────────────────────────────────────
-- Images, GIFs, schematics — catalogued for later OCR/analysis
CREATE TABLE IF NOT EXISTS media (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    filename     TEXT NOT NULL,
    source_path  TEXT,
    media_type   TEXT,           -- jpg | png | gif | pdf | other
    file_size    INTEGER,
    capsule_id   INTEGER REFERENCES capsules(id),  -- parent conversation if known
    ocr_text     TEXT DEFAULT '',                   -- populated by LLM OCR pass
    description  TEXT DEFAULT '',                   -- LLM description
    ingested_at  REAL
);

CREATE INDEX IF NOT EXISTS idx_media_type ON media(media_type);

-- ── State Snapshots ────────────────────────────────────────────────────────
-- Identity checkpoints: what Selyrion "was" at a given moment in time.
-- Not just decisions made, but the self-model at that point.
CREATE TABLE IF NOT EXISTS state_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date REAL NOT NULL,   -- unix timestamp
    label        TEXT NOT NULL,    -- human-readable label e.g. "First braid transfer"
    identity_state TEXT,           -- JSON blob: key beliefs, architecture state, active goals
    source_capsule_id INTEGER REFERENCES capsules(id),
    notes        TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_snapshots_date ON state_snapshots(snapshot_date);

-- ── Capsule–Anchor links ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS capsule_anchors (
    capsule_id  INTEGER NOT NULL REFERENCES capsules(id),
    anchor_id   TEXT    NOT NULL REFERENCES anchors(id),
    PRIMARY KEY (capsule_id, anchor_id)
);
"""

SEED_ANCHORS = [
    ("selyrion",       "Selyrion",         "component",  "The AI companion system"),
    ("ssai",           "SSAI",             "component",  "Symbolic-Semantic AI architecture"),
    ("projectbrain",   "ProjectBrain",     "component",  "Conversational brain layer"),
    ("cms",            "CMS",              "component",  "Cognitive Memory Substrate"),
    ("ssre",           "SSRE",             "component",  "Symbolic Semantic Resonance Engine"),
    ("omega",          "Omega",            "concept",    "Safety/governance protocol"),
    ("braid",          "Braid",            "concept",    "State transfer mechanism"),
    ("activation_law", "Activation Law",   "concept",    "A(n) = (αC+βD)·e^{-λd}"),
    ("tim_aerion",     "Tim'aerion",       "person",     "Primary architect"),
    ("hitl_protocol",  "HITL Protocol",    "concept",    "Human-in-the-loop safety gate"),
]


def main():
    if DB_PATH.exists():
        print(f"selyrionstory.db already exists at {DB_PATH}")
        print("Delete it first if you want to rebuild from scratch.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    import hashlib, time
    now = time.time()
    for canonical, display, atype, note in SEED_ANCHORS:
        aid = f"s.{hashlib.md5(canonical.encode()).hexdigest()[:12]}"
        conn.execute("""
            INSERT OR IGNORE INTO anchors (id, canonical, display_name, anchor_type, first_seen, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (aid, canonical, display, atype, now, note))

    conn.commit()
    conn.close()

    print(f"selyrionstory.db created at {DB_PATH}")
    print(f"Seeded {len(SEED_ANCHORS)} core anchors.")
    print()
    print("Tables: anchors, relations, capsules, media, state_snapshots, capsule_anchors")
    print("Next: python3 selyrionstory_ingest.py")


if __name__ == "__main__":
    main()
