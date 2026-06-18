"""Apply 021_daemon_work_queue.sql + acceptance gate for codeunit verifier daemon.

Idempotent: CREATE IF NOT EXISTS for table/indices.

Acceptance gate:
  1) Schema: daemon_work_queue table + 3 indices present; CHECK constraints
     enforced (insert with bad lane / status rejected).
  2) Idempotent enqueue: enqueuing the same payload twice ⇒ inserted=True then
     inserted=False.
  3) Atomic claim: two concurrent worker_ids both calling claim() never get the
     same task_id (simulated by sequential claim — second returns next task).
  4) Lease auto-recovery: claimed task with expired lease is reclaimable.
  5) End-to-end: seed 3 known-good codeunits, run worker for 3 iterations,
     verify 3 execution_traces rows landed with codeunit_id + verdict; queue
     rows all status=done.
  6) State restoration: synthetic queue + trace rows cleaned; codeunits
     truth_state untouched (deterministic class never promotes).
  7) Substrate untouched.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home()
CLAUDECODE_DB = HOME / "claudecode.db"
SELYRIONCODE_DB = HOME / "selyrioncode.db"
SUBSTRATE_DB = HOME / "resonance_v11.db"
SQL_PATH = Path(__file__).with_name("021_daemon_work_queue.sql")

sys.path.insert(0, str(Path(__file__).parent.parent))


def _substrate_sig():
    if not SUBSTRATE_DB.exists():
        return None
    st = SUBSTRATE_DB.stat()
    return (st.st_size, st.st_mtime)


def _apply_sql():
    sql = SQL_PATH.read_text()
    with sqlite3.connect(CLAUDECODE_DB) as c:
        c.executescript(sql)


def _schema_check() -> dict:
    with sqlite3.connect(CLAUDECODE_DB) as c:
        tbl = bool(c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daemon_work_queue'"
        ).fetchone())
        indices = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='daemon_work_queue'"
        ).fetchall()}
        bad_lane_rejected = False
        try:
            c.execute(
                "INSERT INTO daemon_work_queue "
                "(task_id, task_type, lane, payload_json, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("test.bad_lane", "verify_codeunit", "GPU_INVALID", "{}",
                 "pending", time.time(), time.time()),
            )
            c.execute("DELETE FROM daemon_work_queue WHERE task_id='test.bad_lane'")
        except sqlite3.IntegrityError:
            bad_lane_rejected = True
    return {
        "table_present": tbl,
        "idx_status_lane_prio": "idx_q_status_lane_prio" in indices,
        "idx_lease_expires": "idx_q_lease_expires" in indices,
        "idx_task_type": "idx_q_task_type" in indices,
        "bad_lane_rejected": bad_lane_rejected,
    }


def _idempotent_enqueue_check() -> dict:
    from codeops.daemon import scheduler
    payload = {"codeunit_id": "cu.D1_ACCEPTANCE_SENTINEL_XYZ"}
    r1 = scheduler.enqueue("verify_codeunit", payload, lane="cpu")
    r2 = scheduler.enqueue("verify_codeunit", payload, lane="cpu")
    with sqlite3.connect(CLAUDECODE_DB) as c:
        c.execute("DELETE FROM daemon_work_queue WHERE task_id=?", (r1["task_id"],))
        c.commit()
    return {
        "first_inserted": r1["inserted"],
        "second_inserted": r2["inserted"],
        "task_ids_match": r1["task_id"] == r2["task_id"],
    }


def _claim_uniqueness_check() -> dict:
    from codeops.daemon import scheduler
    p1 = {"codeunit_id": "cu.ACC_CLAIM_A"}
    p2 = {"codeunit_id": "cu.ACC_CLAIM_B"}
    t1 = scheduler.enqueue("verify_codeunit", p1)["task_id"]
    t2 = scheduler.enqueue("verify_codeunit", p2)["task_id"]
    c1 = scheduler.claim(worker_id="wA", task_type="verify_codeunit", lane="cpu")
    c2 = scheduler.claim(worker_id="wB", task_type="verify_codeunit", lane="cpu")
    distinct = (c1 and c2 and c1["task_id"] != c2["task_id"])
    with sqlite3.connect(CLAUDECODE_DB) as c:
        c.execute("DELETE FROM daemon_work_queue WHERE task_id IN (?, ?)", (t1, t2))
        c.commit()
    return {"two_distinct_claims": bool(distinct)}


def _lease_recovery_check() -> dict:
    from codeops.daemon import scheduler
    payload = {"codeunit_id": "cu.ACC_LEASE_TEST"}
    tid = scheduler.enqueue("verify_codeunit", payload)["task_id"]
    scheduler.claim(worker_id="wDead", task_type="verify_codeunit", lane="cpu",
                    lease_s=1)
    # Force lease expiry
    with sqlite3.connect(CLAUDECODE_DB) as c:
        c.execute("UPDATE daemon_work_queue SET lease_expires=? WHERE task_id=?",
                  (time.time() - 60, tid))
        c.commit()
    reclaimed = scheduler.claim(worker_id="wAlive", task_type="verify_codeunit",
                                lane="cpu")
    with sqlite3.connect(CLAUDECODE_DB) as c:
        c.execute("DELETE FROM daemon_work_queue WHERE task_id=?", (tid,))
        c.commit()
    return {"reclaimed_by_new_worker": reclaimed is not None
            and reclaimed["task_id"] == tid}


def _end_to_end_check() -> dict:
    from codeops.daemon import verifier_worker

    # Pick 3 small, known-good codeunits (verified_runtime, short)
    with sqlite3.connect(SELYRIONCODE_DB) as c:
        rows = c.execute(
            "SELECT id, parsed_code, truth_state FROM codeunits "
            "WHERE truth_state='verified_runtime' "
            "AND LENGTH(parsed_code) < 300 LIMIT 3"
        ).fetchall()
    if len(rows) < 3:
        return {"ok": False, "reason": "not enough small verified codeunits"}

    cu_ids = [r[0] for r in rows]
    pre_states = {r[0]: r[2] for r in rows}

    enq_results = []
    for cu_id in cu_ids:
        enq_results.append(
            verifier_worker.seed_tasks(limit=None, truth_states=("__never_match__",))
        )
    from codeops.daemon import scheduler as sched
    enqueued_tids = []
    for cu_id in cu_ids:
        r = sched.enqueue("verify_codeunit", {"codeunit_id": cu_id}, lane="cpu")
        enqueued_tids.append(r["task_id"])

    pre_traces_count = None
    with sqlite3.connect(CLAUDECODE_DB) as c:
        pre_traces_count = c.execute(
            "SELECT COUNT(*) FROM execution_traces"
        ).fetchone()[0]

    t0 = time.time()
    processed = []
    for _ in range(3):
        r = verifier_worker.process_one(worker_id="acceptance.w1", lease_s=60)
        if r is not None:
            processed.append(r)

    with sqlite3.connect(CLAUDECODE_DB) as c:
        post_traces = c.execute(
            "SELECT codeunit_id, verdict FROM execution_traces "
            "WHERE started_at >= ? AND codeunit_id IN (?, ?, ?)",
            (t0, *cu_ids),
        ).fetchall()
        queue_done = c.execute(
            "SELECT COUNT(*) FROM daemon_work_queue "
            "WHERE task_id IN (?, ?, ?) AND status='done'",
            tuple(enqueued_tids),
        ).fetchone()[0]

    # Verify truth_state untouched (deterministic daemon never promotes)
    with sqlite3.connect(SELYRIONCODE_DB) as c:
        post_states = {
            r[0]: r[1] for r in c.execute(
                "SELECT id, truth_state FROM codeunits WHERE id IN (?, ?, ?)",
                tuple(cu_ids),
            ).fetchall()
        }

    truth_state_untouched = all(
        pre_states[i] == post_states[i] for i in cu_ids
    )

    # Cleanup
    with sqlite3.connect(CLAUDECODE_DB) as c:
        for tid in enqueued_tids:
            c.execute("DELETE FROM daemon_work_queue WHERE task_id=?", (tid,))
        for cu_id in cu_ids:
            c.execute(
                "DELETE FROM execution_traces WHERE codeunit_id=? AND started_at >= ?",
                (cu_id, t0),
            )
        c.commit()

    return {
        "ok": (len(processed) == 3
               and len(post_traces) == 3
               and queue_done == 3
               and truth_state_untouched),
        "processed_n": len(processed),
        "trace_rows_with_codeunit_id": len(post_traces),
        "queue_done_n": queue_done,
        "truth_state_untouched": truth_state_untouched,
        "sample_results": processed,
        "sample_traces": post_traces,
    }


def main() -> int:
    sig_before = _substrate_sig()
    t0 = time.time()

    _apply_sql()
    schema = _schema_check()
    idem = _idempotent_enqueue_check()
    claim_u = _claim_uniqueness_check()
    lease = _lease_recovery_check()
    e2e = _end_to_end_check()

    sig_after = _substrate_sig()
    substrate_untouched = (sig_before == sig_after)

    gate = (
        all(schema.values())
        and idem["first_inserted"] is True
        and idem["second_inserted"] is False
        and idem["task_ids_match"] is True
        and claim_u["two_distinct_claims"] is True
        and lease["reclaimed_by_new_worker"] is True
        and e2e["ok"] is True
        and substrate_untouched
    )

    print(json.dumps({
        "migration": "021_daemon_work_queue + codeunit verifier daemon",
        "elapsed_s": round(time.time() - t0, 3),
        "schema": schema,
        "idempotent_enqueue": idem,
        "claim_uniqueness": claim_u,
        "lease_recovery": lease,
        "end_to_end": e2e,
        "substrate_untouched": substrate_untouched,
        "ACCEPTANCE_GATE_PASS": gate,
    }, indent=2, default=str))
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
