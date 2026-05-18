-- visual_schema_migration.sql
-- Adds visual perception layer to resonance_v11.db.
--
-- Extends relations + anchors for vision ingestion.
-- Adds hypotheses, events, sequences tables for temporal/predictive pipeline.
-- Registers visual predicates in predicate registry and predicate_map.
--
-- Safe to run after predicate_registry_migration.sql (already applied).
-- Idempotent: all ALTER TABLE uses column-existence guards via Python wrapper.
-- Run with: python3 apply_visual_migration.py

BEGIN;

-- ── Visual predicates — relational layer ─────────────────────────────────────

INSERT OR IGNORE INTO predicates VALUES
    ('interacts_with',     'relational', 'Agent interacts with object (action normalisation)'),
    ('spatial_on',         'relational', 'Object rests on surface'),
    ('spatial_under',      'relational', 'Object is below subject'),
    ('spatial_adjacent',   'relational', 'Objects are spatially adjacent'),
    ('spatial_support',    'relational', 'Object provides structural support to subject'),
    ('depicts',            'relational', 'Capsule or image depicts anchor concept'),
    ('co_occurs_with',     'relational', 'Concepts co-occur in visual context'),
    ('contains_visually',  'relational', 'Scene contains object visually'),
    ('has_attribute',      'relational', 'Anchor has visual attribute (colour, size, state)');

-- ── Register in predicate_map (raw = canonical for new visual predicates) ────

INSERT OR IGNORE INTO predicate_map (raw_predicate, canonical_predicate) VALUES
    ('interacts_with',    'interacts_with'),
    ('spatial_on',        'spatial_on'),
    ('spatial_under',     'spatial_under'),
    ('spatial_adjacent',  'spatial_adjacent'),
    ('spatial_support',   'spatial_support'),
    ('depicts',           'depicts'),
    ('co_occurs_with',    'co_occurs_with'),
    ('contains_visually', 'contains_visually'),
    ('has_attribute',     'has_attribute'),
    -- common raw VG predicates → normalised
    ('holding',   'interacts_with'),
    ('grasping',  'interacts_with'),
    ('carrying',  'interacts_with'),
    ('wearing',   'has_attribute'),
    ('has',       'has_attribute'),
    ('on',        'spatial_on'),
    ('under',     'spatial_under'),
    ('next to',   'spatial_adjacent'),
    ('beside',    'spatial_adjacent'),
    ('near',      'spatial_adjacent'),
    ('leaning on','spatial_support');

-- ── Hypotheses table — LAION sandbox, never truth ────────────────────────────

CREATE TABLE IF NOT EXISTS hypotheses (
    id           TEXT PRIMARY KEY,
    subject      TEXT NOT NULL,
    predicate    TEXT NOT NULL,
    object       TEXT NOT NULL,
    confidence   REAL DEFAULT 0.5,
    source       TEXT,               -- 'laion', 'clip', 'ssre'
    image_id     TEXT,
    created_at   REAL DEFAULT (unixepoch()),
    validated    INTEGER DEFAULT 0,  -- 0=unvalidated 1=confirmed 2=rejected
    ssre_score   REAL
);

CREATE INDEX IF NOT EXISTS idx_hypotheses_subject   ON hypotheses(subject);
CREATE INDEX IF NOT EXISTS idx_hypotheses_validated ON hypotheses(validated);

-- ── Events table — temporal event stream ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS events (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,      -- 'interaction_start', 'interaction_end', 'placement', etc.
    subject_id   TEXT,
    object_id    TEXT,
    instance_sub TEXT,               -- persistent tracking ID
    instance_obj TEXT,
    frame_id     INTEGER,
    vis_timestamp REAL,
    confidence   REAL DEFAULT 1.0,
    created_at   REAL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_events_frame     ON events(frame_id);
CREATE INDEX IF NOT EXISTS idx_events_type      ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_instances ON events(instance_sub, instance_obj);

-- ── Sequences table — pattern memory for prediction ──────────────────────────

CREATE TABLE IF NOT EXISTS sequences (
    id        TEXT PRIMARY KEY,
    event_1   TEXT NOT NULL,
    event_2   TEXT NOT NULL,
    event_3   TEXT NOT NULL,
    frequency INTEGER DEFAULT 1,
    UNIQUE(event_1, event_2, event_3)
);

CREATE INDEX IF NOT EXISTS idx_sequences_prefix ON sequences(event_1, event_2);

-- ── Backfill predicate_layer for new visual predicates ───────────────────────

UPDATE relations
SET predicate_layer = (
    SELECT layer FROM predicates WHERE predicates.name = relations.predicate
)
WHERE predicate_layer IS NULL;

COMMIT;

-- ── Verify ────────────────────────────────────────────────────────────────────

SELECT layer, COUNT(*) FROM predicates GROUP BY layer ORDER BY layer;
SELECT name FROM sqlite_master WHERE type='table' AND name IN ('hypotheses','events','sequences') ORDER BY name;
