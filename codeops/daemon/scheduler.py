"""codeops/daemon/scheduler.py — atomic claim / heartbeat / release.

Single scheduler-led queue. SQLite atomic UPDATE…WHERE makes claim race-safe
without external locks. Leases auto-expire so a crashed worker's task returns
to the pool.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

CLAUDECODE_DB = Path.home() / "claudecode.db"

LANES = ("cpu", "io", "gpu", "benchmark")
DEFAULT_LEASE_S = 300        # 5 minutes
DEFAULT_MAX_ATTEMPTS = 3


def _task_id(task_type: str, payload: dict) -> str:
    """Deterministic ID — same (type, payload) ⇒ same task_id ⇒ idempotent enqueue."""
    body = task_type + "|" + json.dumps(payload, sort_keys=True)
    return "task." + hashlib.sha1(body.encode()).hexdigest()[:14]


def enqueue(task_type: str, payload: dict, *, lane: str = "cpu",
            priority: int = 0) -> dict:
    if lane not in LANES:
        raise ValueError(f"bad lane: {lane!r}")
    tid = _task_id(task_type, payload)
    now = time.time()
    with sqlite3.connect(CLAUDECODE_DB) as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO daemon_work_queue "
            "(task_id, task_type, lane, payload_json, priority, status, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (tid, task_type, lane, json.dumps(payload), priority,
             "pending", now, now),
        )
        c.commit()
        inserted = cur.rowcount > 0
    return {"task_id": tid, "inserted": inserted}


def claim(*, worker_id: str, task_type: str | None = None,
          lane: str | None = None,
          lease_s: int = DEFAULT_LEASE_S) -> dict | None:
    """Atomically claim ONE pending task (or a claimed task with expired lease)."""
    now = time.time()
    new_expiry = now + lease_s
    filters = ["(status='pending' OR (status='claimed' AND lease_expires < ?))"]
    params: list = [now]
    if task_type is not None:
        filters.append("task_type=?"); params.append(task_type)
    if lane is not None:
        if lane not in LANES:
            raise ValueError(f"bad lane: {lane!r}")
        filters.append("lane=?"); params.append(lane)
    where = " AND ".join(filters)

    with sqlite3.connect(CLAUDECODE_DB) as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            f"SELECT task_id, task_type, payload_json, attempts FROM daemon_work_queue "
            f"WHERE {where} "
            f"ORDER BY priority DESC, created_at ASC LIMIT 1",
            params,
        ).fetchone()
        if row is None:
            c.execute("COMMIT")
            return None
        tid, ttype, payload_json, attempts = row
        c.execute(
            "UPDATE daemon_work_queue SET status='claimed', claimed_by=?, "
            "claimed_at=?, lease_expires=?, attempts=attempts+1, updated_at=? "
            "WHERE task_id=?",
            (worker_id, now, new_expiry, now, tid),
        )
        c.execute("COMMIT")
    return {"task_id": tid, "task_type": ttype,
            "payload": json.loads(payload_json),
            "attempts": attempts + 1}


def heartbeat(task_id: str, worker_id: str, lease_s: int = DEFAULT_LEASE_S) -> bool:
    """Extend lease. Returns False if task no longer claimed by this worker."""
    now = time.time()
    with sqlite3.connect(CLAUDECODE_DB) as c:
        cur = c.execute(
            "UPDATE daemon_work_queue SET lease_expires=?, updated_at=? "
            "WHERE task_id=? AND claimed_by=? AND status='claimed'",
            (now + lease_s, now, task_id, worker_id),
        )
        c.commit()
    return cur.rowcount == 1


def complete(task_id: str, worker_id: str, result: dict | None = None) -> bool:
    now = time.time()
    with sqlite3.connect(CLAUDECODE_DB) as c:
        cur = c.execute(
            "UPDATE daemon_work_queue SET status='done', result_json=?, "
            "lease_expires=NULL, updated_at=? "
            "WHERE task_id=? AND claimed_by=? AND status='claimed'",
            (json.dumps(result) if result is not None else None, now,
             task_id, worker_id),
        )
        c.commit()
    return cur.rowcount == 1


def fail(task_id: str, worker_id: str, error: str,
         *, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
         retry: bool = True) -> dict:
    """Mark failed. If retry=True and attempts < max_attempts, return to pending."""
    now = time.time()
    with sqlite3.connect(CLAUDECODE_DB) as c:
        row = c.execute(
            "SELECT attempts FROM daemon_work_queue WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if row is None:
            return {"action": "missing"}
        attempts = row[0]
        if retry and attempts < max_attempts:
            c.execute(
                "UPDATE daemon_work_queue SET status='pending', claimed_by=NULL, "
                "claimed_at=NULL, lease_expires=NULL, error_msg=?, updated_at=? "
                "WHERE task_id=? AND claimed_by=?",
                (error[:1000], now, task_id, worker_id),
            )
            action = "requeued"
        else:
            c.execute(
                "UPDATE daemon_work_queue SET status='failed', error_msg=?, "
                "lease_expires=NULL, updated_at=? "
                "WHERE task_id=? AND claimed_by=?",
                (error[:1000], now, task_id, worker_id),
            )
            action = "failed"
        c.commit()
    return {"action": action, "attempts": attempts}


def queue_stats(task_type: str | None = None) -> dict:
    with sqlite3.connect(CLAUDECODE_DB) as c:
        sql = "SELECT status, COUNT(*) FROM daemon_work_queue"
        params: tuple = ()
        if task_type is not None:
            sql += " WHERE task_type=?"
            params = (task_type,)
        sql += " GROUP BY status"
        rows = c.execute(sql, params).fetchall()
    return {s: n for s, n in rows}
