-- 008_operator_registry.sql
-- Phase 2.0.1 — operator_registry table (declarations only, no execution semantics)
-- Doctrine: operators are typed, declared, enable-gated; dispatcher is the single chokepoint.
-- See PHASE_2_0_scaffolding_spec.md §11 for locked resolutions (Q-2.0.A..M).

BEGIN;

CREATE TABLE IF NOT EXISTS operator_registry (
    name           TEXT PRIMARY KEY,
    category       TEXT NOT NULL,
    input_schema   TEXT NOT NULL,
    output_schema  TEXT NOT NULL,
    truth_policy   TEXT NOT NULL
                    CHECK (truth_policy IN
                           ('never_writes','proposes','observes','asserts','retracts')),
    cost_class     TEXT NOT NULL
                    CHECK (cost_class IN
                           ('O(1)','O(k)','O(n)','bounded','unbounded')),
    grounding      TEXT NOT NULL,
    enabled        INTEGER NOT NULL DEFAULT 0
                    CHECK (enabled IN (0,1)),
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_operator_registry_enabled
    ON operator_registry (enabled);
CREATE INDEX IF NOT EXISTS idx_operator_registry_category
    ON operator_registry (category);

-- Initial declarations.
-- noop_passthrough is the sanity probe — only operator enabled at 2.0.1.
-- All others are PLACEHOLDERS (enabled=0); their grounding strings are
-- declarations only and are NOT expected to resolve until later phases.

INSERT OR IGNORE INTO operator_registry
    (name, category, input_schema, output_schema, truth_policy, cost_class,
     grounding, enabled, created_at, updated_at)
VALUES
    ('noop_passthrough', 'match',
     '{"args":{}}', '{"echo":"any"}',
     'never_writes', 'O(1)',
     'py:inference.operator_dispatcher._noop_passthrough',
     1, strftime('%s','now'), strftime('%s','now')),

    ('unify_pattern', 'match',
     '{"pattern":"list[edge_template]","graph_slice":"WorkingSet","type_env":"dict?","constraints":"dict?","k":"int=8"}',
     '{"bindings":"list[BindingSet]"}',
     'never_writes', 'bounded',
     'py:inference.unify.unify',
     0, strftime('%s','now'), strftime('%s','now')),

    ('working_set_create', 'compose',
     '{"purpose":"str","query":"str"}',
     '{"working_set_id":"str"}',
     'never_writes', 'O(1)',
     'py:inference.working_memory.create',
     0, strftime('%s','now'), strftime('%s','now')),

    ('causal_chain', 'infer',
     '{"working_set_id":"str","seed":"anchor_id","k":"int=4"}',
     '{"chains":"list[CausalChain]"}',
     'proposes', 'bounded',
     'py:operators.causal.causal_chain',
     0, strftime('%s','now'), strftime('%s','now')),

    ('contradiction_scan', 'infer',
     '{"working_set_id":"str"}',
     '{"contradictions":"list[Contradiction]"}',
     'proposes', 'bounded',
     'py:operators.contradiction.contradiction_scan',
     0, strftime('%s','now'), strftime('%s','now')),

    ('hierarchy_lift', 'compose',
     '{"working_set_id":"str","anchor":"anchor_id"}',
     '{"path":"list[anchor_id]"}',
     'proposes', 'O(k)',
     'py:operators.hierarchy.hierarchy_lift',
     0, strftime('%s','now'), strftime('%s','now')),

    ('analogy_map', 'compose',
     '{"source":"working_set_id","target":"working_set_id"}',
     '{"mapping":"AnalogyMap"}',
     'proposes', 'bounded',
     'py:operators.analogy.analogy_map',
     0, strftime('%s','now'), strftime('%s','now')),

    ('path_complete', 'infer',
     '{"working_set_id":"str","start":"anchor_id","end":"anchor_id","k":"int=4"}',
     '{"path":"list[edge]"}',
     'proposes', 'bounded',
     'py:operators.path.path_complete',
     0, strftime('%s','now'), strftime('%s','now')),

    ('constraint_prune', 'constrain',
     '{"working_set_id":"str","rules":"list[ConstraintRule]"}',
     '{"pruned":"int"}',
     'proposes', 'O(n)',
     'py:operators.constraint.constraint_prune',
     0, strftime('%s','now'), strftime('%s','now')),

    ('belief_revise', 'infer',
     '{"working_set_id":"str","evidence":"EvidencePacket"}',
     '{"revised":"list[BeliefDelta]"}',
     'retracts', 'bounded',
     'py:operators.belief.belief_revise',
     0, strftime('%s','now'), strftime('%s','now'));

COMMIT;

-- Verification block (informational only)
-- SELECT name, category, truth_policy, enabled FROM operator_registry ORDER BY enabled DESC, name;
