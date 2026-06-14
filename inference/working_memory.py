"""
Phase 2.0.2 — working-memory scratch layer.

Scope (minimal, locked):
- create / read / delete lifecycle only
- strict-tree parent_set (Q-2.0.I)
- TTL fail-closed on read (Q-2.0.H guardrail)
- isolated DB at WORKMEM_DB_PATH (Q-2.0.G) — never opens the substrate
- nightly sweep helper (mark expired, optional purge)

Out of scope until later 2.0.x:
- operator dispatch into working sets (2.0.4 unification time)
- commit path back to substrate (deferred to 2.x)
- trace emission (2.0.3)
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Iterable

WORKMEM_DB_PATH = os.environ.get(
    "WORKMEM_DB_PATH", "/home/timbushnell/selyrion_workmem.db"
)

DEFAULT_TTL_SECONDS = int(os.environ.get("WORKMEM_DEFAULT_TTL", "86400"))


class WorkingMemoryError(Exception):
    pass


class WorkingSetNotFound(WorkingMemoryError):
    pass


class WorkingSetExpired(WorkingMemoryError):
    pass


class WorkingSetSealed(WorkingMemoryError):
    pass


class InvalidParent(WorkingMemoryError):
    pass


@dataclass
class WorkingSet:
    id: str
    purpose: str
    query: str
    created_by: str
    status: str
    expires_at: int
    parent_set: str | None
    created_at: int


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(WORKMEM_DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> int:
    return int(time.time())


def _row_to_ws(row: sqlite3.Row) -> WorkingSet:
    return WorkingSet(
        id=row["id"],
        purpose=row["purpose"],
        query=row["query"],
        created_by=row["created_by"],
        status=row["status"],
        expires_at=int(row["expires_at"]),
        parent_set=row["parent_set"],
        created_at=int(row["created_at"]),
    )


def create(purpose: str, query: str, created_by: str,
           parent_set: str | None = None,
           ttl_seconds: int | None = None) -> str:
    ttl = ttl_seconds if ttl_seconds is not None else DEFAULT_TTL_SECONDS
    if ttl <= 0:
        raise WorkingMemoryError("ttl_seconds must be positive")

    ws_id = "ws." + uuid.uuid4().hex[:12]
    now = _now()
    expires_at = now + ttl

    with _open() as conn:
        if parent_set is not None:
            prow = conn.execute(
                "SELECT id, status, expires_at FROM working_sets WHERE id = ?",
                (parent_set,),
            ).fetchone()
            if prow is None:
                raise InvalidParent(f"parent_set {parent_set!r} not found")
            # parent_set must be a real, live node — strict tree integrity
            if prow["status"] in ("expired", "deleted"):
                raise InvalidParent(
                    f"parent_set {parent_set!r} has status={prow['status']}"
                )
            if int(prow["expires_at"]) <= now:
                raise InvalidParent(
                    f"parent_set {parent_set!r} is past TTL"
                )

        conn.execute(
            "INSERT INTO working_sets "
            "(id, purpose, query, created_by, status, expires_at, parent_set, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ws_id, purpose, query, created_by, "open",
             expires_at, parent_set, now),
        )
        conn.commit()
    return ws_id


def read(ws_id: str) -> WorkingSet:
    """Fail-closed on expiry (Q-2.0.H guardrail)."""
    with _open() as conn:
        row = conn.execute(
            "SELECT * FROM working_sets WHERE id = ?",
            (ws_id,),
        ).fetchone()
        if row is None:
            raise WorkingSetNotFound(ws_id)
        now = _now()
        if row["status"] == "deleted":
            raise WorkingSetNotFound(f"{ws_id} deleted")
        if row["status"] == "expired" or int(row["expires_at"]) <= now:
            # passively mark expired and refuse read
            conn.execute(
                "UPDATE working_sets SET status = 'expired' WHERE id = ?",
                (ws_id,),
            )
            conn.commit()
            raise WorkingSetExpired(ws_id)
        return _row_to_ws(row)


def delete(ws_id: str, cascade: bool = True) -> int:
    """
    Mark a working set deleted. Children with this as parent are blocked
    by FK ON DELETE RESTRICT unless cascade=True (in which case children are
    deleted first, depth-first).
    Returns count of working_sets rows deleted.
    """
    with _open() as conn:
        row = conn.execute(
            "SELECT id FROM working_sets WHERE id = ?", (ws_id,)
        ).fetchone()
        if row is None:
            raise WorkingSetNotFound(ws_id)

        if cascade:
            children = [
                r["id"] for r in conn.execute(
                    "SELECT id FROM working_sets WHERE parent_set = ?",
                    (ws_id,),
                ).fetchall()
            ]
            total = 0
            for child in children:
                total += delete(child, cascade=True)
            conn.execute("DELETE FROM working_sets WHERE id = ?", (ws_id,))
            conn.commit()
            return total + 1
        else:
            try:
                conn.execute("DELETE FROM working_sets WHERE id = ?", (ws_id,))
                conn.commit()
                return 1
            except sqlite3.IntegrityError as exc:
                raise WorkingMemoryError(
                    f"delete blocked by children of {ws_id}: {exc}"
                )


def sweep_expired(purge: bool = False) -> dict:
    """
    Nightly sweep helper. Always marks past-TTL sets as expired.
    If purge=True, removes 'expired' and 'deleted' rows (and their cascade).
    """
    now = _now()
    with _open() as conn:
        marked = conn.execute(
            "UPDATE working_sets SET status = 'expired' "
            "WHERE status NOT IN ('expired','deleted') AND expires_at <= ?",
            (now,),
        ).rowcount
        purged = 0
        if purge:
            # delete leaf-up to respect FK RESTRICT
            while True:
                row = conn.execute(
                    "SELECT id FROM working_sets ws "
                    "WHERE ws.status IN ('expired','deleted') "
                    "AND NOT EXISTS (SELECT 1 FROM working_sets c "
                    "                WHERE c.parent_set = ws.id) "
                    "LIMIT 1"
                ).fetchone()
                if row is None:
                    break
                conn.execute(
                    "DELETE FROM working_sets WHERE id = ?", (row["id"],)
                )
                purged += 1
        conn.commit()
    return {"marked_expired": marked, "purged": purged, "ts": now}


def list_children(ws_id: str) -> list[str]:
    with _open() as conn:
        return [
            r["id"] for r in conn.execute(
                "SELECT id FROM working_sets WHERE parent_set = ? ORDER BY created_at",
                (ws_id,),
            ).fetchall()
        ]


def schema_version() -> str | None:
    with _open() as conn:
        row = conn.execute(
            "SELECT value FROM workmem_meta WHERE key = 'schema_version'"
        ).fetchone()
        return row["value"] if row else None
