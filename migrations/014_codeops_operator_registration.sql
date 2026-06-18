-- 014_codeops_operator_registration.sql
-- Phase B — Selyrion Python adapter: register codeops as substrate operators.
-- Target DB: ~/resonance_v11.db (operator_registry from migration 008).
-- Declarations only. All enabled=0 per 2.0.1 doctrine. Dispatcher is chokepoint.
-- See SELYRION_PYTHON_ADAPTER.md §5 (Phase sequence) + selyrion_python_expertise_schema.md §11.

BEGIN;

INSERT OR IGNORE INTO operator_registry
    (name, category, input_schema, output_schema, truth_policy, cost_class,
     grounding, enabled, created_at, updated_at)
VALUES
    ('py.classify_error', 'infer',
     '{"stderr":"str"}',
     '{"error_class":"str","subtype":"str"}',
     'never_writes', 'O(1)',
     'py:codeops.parser.classify',
     0, strftime('%s','now'), strftime('%s','now')),

    ('py.check_security', 'constrain',
     '{"code":"str"}',
     '{"safe":"bool","reason":"str"}',
     'never_writes', 'O(n)',
     'py:codeops.sandbox.is_safe',
     0, strftime('%s','now'), strftime('%s','now')),

    ('py.propose_fix', 'compose',
     '{"code":"str","stderr":"str","error_class":"str","subtype":"str","cms_context":"str?"}',
     '{"fixed_code":"str","fix_desc":"str"}',
     'proposes', 'bounded',
     'py:codeops.fixer.apply',
     0, strftime('%s','now'), strftime('%s','now')),

    ('py.run_sandboxed', 'infer',
     '{"code":"str","lang":"str?"}',
     '{"stdout":"str","stderr":"str","returncode":"int","elapsed":"float","lang":"str"}',
     'observes', 'bounded',
     'py:codeops.runner.run',
     0, strftime('%s','now'), strftime('%s','now'));

COMMIT;
