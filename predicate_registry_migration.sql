-- predicate_registry_migration.sql
-- Adds typed predicate registry to resonance_v11.db.
-- Run AFTER selyrionstory passes complete (no concurrent writes).
--
-- TWO LAYERS:
--   constraint  → drives CAP, ranking, constraint propagation
--   code        → drives SCPL, execution graph, code synthesis
--
-- predicate_map stays untouched (raw→canonical normalizer).
-- This table is the authoritative layer classifier.

BEGIN;

-- ── Predicate registry ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS predicates (
    name        TEXT PRIMARY KEY,
    layer       TEXT NOT NULL CHECK (layer IN ('constraint', 'code', 'ontology', 'relational')),
    description TEXT
);

-- ── Constraint layer (CAP / reasoning / execution truth) ─────────────────────

INSERT OR IGNORE INTO predicates VALUES
    ('fails_on',              'constraint', 'Component fails when input matches this condition'),
    ('produces_valid_output', 'constraint', 'Component produces valid output under this condition'),
    ('requires',              'constraint', 'Component requires this precondition to be satisfied'),
    ('incompatible_with',     'constraint', 'Component cannot coexist or be composed with subject'),
    ('preferred_over',        'constraint', 'Subject is preferred over object under normal conditions');

-- ── Code / execution layer (SCPL / graph construction / synthesis) ────────────

INSERT OR IGNORE INTO predicates VALUES
    ('consumes',   'code', 'Component consumes this input type or resource'),
    ('produces',   'code', 'Component produces this output type or resource'),
    ('transforms', 'code', 'Component transforms subject into object'),
    ('calls',      'code', 'Component calls or invokes subject at runtime'),
    ('depends_on', 'code', 'Component depends on subject for execution'),
    ('contains',   'code', 'Component structurally contains subject');

-- ── Existing predicates classified for completeness ──────────────────────────
-- (ontology = structural knowledge / is-a / taxonomy)
-- (relational = domain relations not driving CAP or SCPL)

INSERT OR IGNORE INTO predicates VALUES
    ('causes',      'relational', NULL),
    ('leads_to',    'relational', NULL),
    ('activates',   'relational', NULL),
    ('inhibits',    'relational', NULL),
    ('regulates',   'relational', NULL),
    ('enables',     'relational', NULL),
    ('is_a',        'ontology',   NULL),
    ('same_as',     'ontology',   NULL),
    ('part_of',     'ontology',   NULL),
    ('related_to',  'ontology',   NULL);

-- ── Add layer column to relations for fast filtering ─────────────────────────
-- Backfill from registry where predicate matches.
-- NULL = unclassified (existing data — fine, not required by CAP/SCPL yet).

ALTER TABLE relations ADD COLUMN predicate_layer TEXT;

UPDATE relations
SET predicate_layer = (
    SELECT layer FROM predicates WHERE predicates.name = relations.predicate
)
WHERE predicate_layer IS NULL;

CREATE INDEX IF NOT EXISTS idx_relations_predicate_layer
    ON relations(predicate_layer);

-- ── Register new predicates in predicate_map (raw = canonical for new ones) ──

INSERT OR IGNORE INTO predicate_map (raw_predicate, canonical_predicate) VALUES
    ('fails_on',              'fails_on'),
    ('produces_valid_output', 'produces_valid_output'),
    ('incompatible_with',     'incompatible_with'),
    ('preferred_over',        'preferred_over'),
    ('consumes',              'consumes'),
    ('calls',                 'calls'),
    ('depends_on',            'depends_on');

COMMIT;

-- ── Verify ────────────────────────────────────────────────────────────────────

SELECT layer, COUNT(*) as count FROM predicates GROUP BY layer ORDER BY layer;
SELECT predicate_layer, COUNT(*) as relations_count
    FROM relations
    GROUP BY predicate_layer
    ORDER BY predicate_layer;
