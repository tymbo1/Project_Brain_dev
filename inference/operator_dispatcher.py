"""
Phase 2.0.1 — operator dispatcher.

Locked policy (PHASE_2_0_scaffolding_spec.md §11):
- Q-2.0.A: operator_registry lives in resonance_v11.db.
- Q-2.0.C: enabled=0 is enforced at the dispatcher layer (single chokepoint).

2.0.1 scope (and only this):
- expose dispatch(name, args, context=None) -> OperatorResult
- reject unknown operators
- reject enabled=0 operators
- execute noop_passthrough (sanity probe)

Out of scope until later 2.0.x:
- working-memory creation / lookup
- trace record emission (2.0.3 wires trace)
- unification (2.0.4)
- any operator with truth_policy != 'never_writes'
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from inference import operator_trace

DB_PATH = os.environ.get(
    "RESONANCE_DB_PATH", "/home/timbushnell/resonance_v11.db"
)


class OperatorError(Exception):
    """Base class for dispatcher refusals."""


class UnknownOperator(OperatorError):
    pass


class OperatorDisabled(OperatorError):
    pass


@dataclass
class OperatorResult:
    name: str
    outcome: str                       # "ok" | "rejected" | "error"
    output: Any = None
    duration_ms: float = 0.0
    reason: str | None = None
    meta: dict = field(default_factory=dict)


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _lookup(name: str) -> sqlite3.Row | None:
    with _open_db() as conn:
        cur = conn.execute(
            "SELECT name, category, truth_policy, cost_class, grounding, enabled "
            "FROM operator_registry WHERE name = ?",
            (name,),
        )
        return cur.fetchone()


def _noop_passthrough(args: dict, context: dict | None = None) -> dict:
    return {"echo": args}


_HANDLERS: dict[str, Callable[[dict, dict | None], Any]] = {
    "noop_passthrough": _noop_passthrough,
}


def dispatch(name: str, args: dict | None = None,
             context: dict | None = None) -> OperatorResult:
    args = args or {}
    t0 = time.perf_counter()

    row = _lookup(name)
    if row is None:
        dt_ms = (time.perf_counter() - t0) * 1000.0
        operator_trace.emit(
            operator=name, inputs=args, outputs={},
            outcome="rejected_unknown", duration_ms=dt_ms,
            reason="operator not declared",
        )
        raise UnknownOperator(f"operator not declared: {name!r}")

    if int(row["enabled"]) == 0:
        dt_ms = (time.perf_counter() - t0) * 1000.0
        operator_trace.emit(
            operator=name, inputs=args, outputs={},
            outcome="rejected_disabled", duration_ms=dt_ms,
            reason="enabled=0",
        )
        raise OperatorDisabled(
            f"operator {name!r} is registered but enabled=0"
        )

    handler = _HANDLERS.get(name)
    if handler is None:
        dt_ms = (time.perf_counter() - t0) * 1000.0
        operator_trace.emit(
            operator=name, inputs=args, outputs={},
            outcome="rejected_no_handler", duration_ms=dt_ms,
            reason="enabled but no handler bound",
        )
        raise UnknownOperator(
            f"operator {name!r} enabled but no handler bound at dispatcher"
        )

    output = handler(args, context)
    dt_ms = (time.perf_counter() - t0) * 1000.0
    operator_trace.emit(
        operator=name, inputs=args,
        outputs=output if isinstance(output, dict) else {"value": output},
        outcome="ok", duration_ms=dt_ms,
        provenance={"truth_policy": row["truth_policy"],
                    "cost_class": row["cost_class"]},
    )
    return OperatorResult(
        name=name,
        outcome="ok",
        output=output,
        duration_ms=dt_ms,
        meta={"truth_policy": row["truth_policy"],
              "cost_class": row["cost_class"]},
    )


def list_registered(enabled_only: bool = False) -> list[dict]:
    q = "SELECT name, category, truth_policy, cost_class, enabled FROM operator_registry"
    if enabled_only:
        q += " WHERE enabled = 1"
    q += " ORDER BY enabled DESC, name"
    with _open_db() as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]
