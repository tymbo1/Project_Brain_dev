-- Phase E1 — daemon work queue (single scheduler, leased tasks, idempotent).
-- Lives in claudecode.db. Substrate (resonance_v11.db) never writes here.

CREATE TABLE IF NOT EXISTS daemon_work_queue (
    task_id        TEXT PRIMARY KEY,
    task_type      TEXT NOT NULL,
    lane           TEXT NOT NULL,                  -- 'cpu' | 'io' | 'gpu' | 'benchmark'
    payload_json   TEXT NOT NULL,
    priority       INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'pending', -- pending | claimed | done | failed
    claimed_by     TEXT,
    claimed_at     REAL,
    lease_expires  REAL,
    result_json    TEXT,
    error_msg      TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    CHECK (lane   IN ('cpu','io','gpu','benchmark')),
    CHECK (status IN ('pending','claimed','done','failed'))
);

CREATE INDEX IF NOT EXISTS idx_q_status_lane_prio
    ON daemon_work_queue(status, lane, priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_q_lease_expires
    ON daemon_work_queue(lease_expires) WHERE status='claimed';
CREATE INDEX IF NOT EXISTS idx_q_task_type
    ON daemon_work_queue(task_type);
