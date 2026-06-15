-- 010_p4_bite_a_capsule_domain_visibility.sql
-- P4 Bite A — promote metadata.domain into capsules.domain for language_expression rows.
-- Doctrine: substrate must be able to see its own expression shape. Real semantic domain
-- was hidden inside metadata JSON while capsules.domain collapsed to "linguistics".
-- See memory/project_p4_bite_a_domain_visibility.md for acceptance gate + reversibility.
--
-- Idempotent: re-running on already-promoted rows is a no-op.
-- Scope: ONLY capsule_type = 'language_expression'. Non-expression capsules untouched.

BEGIN;

UPDATE capsules
   SET domain = json_extract(metadata, '$.domain')
 WHERE capsule_type = 'language_expression'
   AND json_extract(metadata, '$.domain') IS NOT NULL
   AND (domain IS NULL OR domain != json_extract(metadata, '$.domain'));

-- Acceptance gate (run manually after apply, verify against expected map):
--   SELECT domain, COUNT(*) FROM capsules
--    WHERE capsule_type='language_expression' GROUP BY domain;
-- Expected per project_p4_capsule_density_measurement.md:
--   emotional_resonance=118  relational_warmth=107  intellectual_curiosity=93
--   creative_engagement=51   practical_grounding=40 humour_lightness=36
--   spiritual_inquiry=28     chess=14

COMMIT;
