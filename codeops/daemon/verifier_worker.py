"""codeops/daemon/verifier_worker.py — deterministic codeunit-verifier daemon.

Class: deterministic (no LLM, no judgment).
Lane:  cpu (subprocess execution via codeops.runner).
Writes: append-only to claudecode.db.execution_traces (via D2 path).
Does NOT write to selyrioncode.codeunits — promotion is a separate stateful
writer step (codeops.apply_promotions).

Task contract:
    task_type = "verify_codeunit"
    payload   = {"codeunit_id": "cu.…"}
    result    = {"verdict": "...", "elapsed_s": …, "attempts": …}
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from . import scheduler
from .. import orchestrator

SELYRIONCODE_DB = Path.home() / "selyrioncode.db"

TASK_TYPE = "verify_codeunit"
LANE = "cpu"


def _fetch_code(codeunit_id: str) -> tuple[str | None, str | None]:
    with sqlite3.connect(SELYRIONCODE_DB) as c:
        row = c.execute(
            "SELECT parsed_code, raw_input FROM codeunits WHERE id=?",
            (codeunit_id,),
        ).fetchone()
    if row is None:
        return None, None
    return row[0], row[1]


def process_one(*, worker_id: str, lease_s: int = scheduler.DEFAULT_LEASE_S,
                max_attempts_per_task: int = 1) -> dict | None:
    task = scheduler.claim(worker_id=worker_id, task_type=TASK_TYPE,
                           lane=LANE, lease_s=lease_s)
    if task is None:
        return None

    payload = task["payload"]
    cu_id = payload.get("codeunit_id")
    if not cu_id:
        scheduler.fail(task["task_id"], worker_id,
                       "payload missing codeunit_id", retry=False)
        return {"task_id": task["task_id"], "status": "failed",
                "reason": "missing codeunit_id"}

    code, _raw = _fetch_code(cu_id)
    if code is None:
        scheduler.fail(task["task_id"], worker_id,
                       f"codeunit {cu_id} not found", retry=False)
        return {"task_id": task["task_id"], "status": "failed",
                "reason": "codeunit not found"}

    t0 = time.time()
    try:
        run_result = orchestrator.run(
            code, lang="python",
            max_attempts=max_attempts_per_task,
            original_problem=f"verify_codeunit::{cu_id}",
            codeunit_id=cu_id,
        )
    except Exception as e:
        scheduler.fail(task["task_id"], worker_id, repr(e), retry=True)
        return {"task_id": task["task_id"], "status": "errored",
                "reason": repr(e)}

    elapsed = round(time.time() - t0, 3)
    result = {
        "codeunit_id": cu_id,
        "orchestrator_status": run_result.get("status"),
        "attempts": run_result.get("attempts"),
        "elapsed_s": elapsed,
    }
    scheduler.complete(task["task_id"], worker_id, result=result)
    return {"task_id": task["task_id"], "status": "done", **result}


def seed_tasks(*, limit: int | None = None,
               truth_states: tuple[str, ...] = ("proposed",),
               priority: int = 0) -> dict:
    """Enqueue verify_codeunit tasks for codeunits in the given truth_states.

    Idempotent — re-enqueuing the same cu_id is a no-op (deterministic task_id).
    """
    placeholders = ",".join("?" * len(truth_states))
    sql = f"SELECT id FROM codeunits WHERE truth_state IN ({placeholders})"
    params: list = list(truth_states)
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with sqlite3.connect(SELYRIONCODE_DB) as c:
        rows = c.execute(sql, params).fetchall()

    enq = 0
    skipped = 0
    for (cu_id,) in rows:
        r = scheduler.enqueue(TASK_TYPE, {"codeunit_id": cu_id},
                              lane=LANE, priority=priority)
        if r["inserted"]:
            enq += 1
        else:
            skipped += 1
    return {"candidates": len(rows), "enqueued": enq,
            "already_queued": skipped}


def run_loop(*, worker_id: str, iterations: int | None = None,
             idle_sleep_s: float = 5.0,
             lease_s: int = scheduler.DEFAULT_LEASE_S) -> dict:
    """Run the worker loop. iterations=None means run forever until interrupted."""
    processed = 0
    idle_polls = 0
    started = time.time()
    i = 0
    try:
        while iterations is None or i < iterations:
            r = process_one(worker_id=worker_id, lease_s=lease_s)
            if r is None:
                idle_polls += 1
                time.sleep(idle_sleep_s)
            else:
                processed += 1
            i += 1
    except KeyboardInterrupt:
        pass
    return {"worker_id": worker_id, "processed": processed,
            "idle_polls": idle_polls,
            "elapsed_s": round(time.time() - started, 3)}
