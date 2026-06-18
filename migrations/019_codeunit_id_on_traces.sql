-- Phase D1 prep — link execution_traces evidence to selyrioncode.codeunits.
-- claudecode.db only; no substrate (resonance_v11.db) writes.

ALTER TABLE execution_traces ADD COLUMN codeunit_id TEXT;
CREATE INDEX IF NOT EXISTS idx_traces_codeunit_id ON execution_traces(codeunit_id);
