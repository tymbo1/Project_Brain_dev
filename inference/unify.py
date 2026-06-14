"""
Phase 2.0.3 — unification / pattern-binding core.

Reads only from selyrion_workmem.db (via working_memory.WORKMEM_DB_PATH).
Never queries the substrate. Never writes anywhere.

Scope (minimal):
- variable binding across a chain of edge templates
- predicate literal or variable
- subject/object literal anchor_id or variable
- top-k by aggregated score, hard-ceiling enforced (Q-2.0.D guardrail)
- bridge-sacred behavior on pre-tagged edges (Q-2.0.F)
- truth_floor constraint with bridge override

Out of scope until later:
- typed wildcards (?X:anchor_type=...)
- soft/fuzzy unification
- multi-hop graph isomorphism beyond chained patterns
- role-consistent analogy mapping
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Iterable

from inference import operator_trace
from inference import working_memory as wm

HARD_CEILING_K = 64
DEFAULT_K = 8


class UnifyError(Exception):
    pass


@dataclass
class BindingSet:
    bindings: dict[str, object]
    matched_edges: list[dict]
    score: float
    hit_bridge: bool = False
    provenance: dict = field(default_factory=dict)


def _is_var(term) -> bool:
    return isinstance(term, str) and term.startswith("?")


def _parse_provenance(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _load_edges(ws_id: str) -> list[dict]:
    with sqlite3.connect(wm.WORKMEM_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, subject_id, predicate, object_id, local_truth, "
            "       local_confidence, provenance "
            "FROM working_set_edges WHERE working_set_id = ?",
            (ws_id,),
        ).fetchall()
    edges = []
    for r in rows:
        prov = _parse_provenance(r["provenance"])
        edges.append({
            "id": r["id"],
            "subject_id": r["subject_id"],
            "predicate": r["predicate"],
            "object_id": r["object_id"],
            "local_truth": r["local_truth"],
            "local_confidence": float(r["local_confidence"] or 0.0),
            "is_bridge": bool(prov.get("is_bridge", False)),
            "domain_purity": prov.get("domain_purity"),
            "provenance": prov,
        })
    return edges


def _edge_passes_truth_floor(edge: dict, truth_floor, bridges_sacred: bool) -> bool:
    if not truth_floor:
        return True
    if edge["local_truth"] in truth_floor:
        return True
    if bridges_sacred and edge["is_bridge"]:
        return True
    return False


def _try_bind(term, edge_val, bindings: dict) -> dict | None:
    """Return updated bindings dict or None on conflict."""
    if _is_var(term):
        cur = bindings.get(term)
        if cur is None:
            new = dict(bindings)
            new[term] = edge_val
            return new
        if cur == edge_val:
            return bindings
        return None
    return bindings if term == edge_val else None


def _match_template(template, edge, bindings: dict) -> dict | None:
    s_term, p_term, o_term = template
    b1 = _try_bind(s_term, edge["subject_id"], bindings)
    if b1 is None:
        return None
    if p_term == "*":
        b2 = b1
    else:
        b2 = _try_bind(p_term, edge["predicate"], b1)
    if b2 is None:
        return None
    b3 = _try_bind(o_term, edge["object_id"], b2)
    return b3


def _search(pattern, edges, bindings, matched, score, hit_bridge,
            results: list[BindingSet], hard_ceiling: int,
            truth_floor, bridges_sacred: bool) -> None:
    if len(results) >= hard_ceiling:
        return
    if not pattern:
        results.append(BindingSet(
            bindings=dict(bindings),
            matched_edges=list(matched),
            score=score,
            hit_bridge=hit_bridge,
        ))
        return
    head, *rest = pattern
    for edge in edges:
        if not _edge_passes_truth_floor(edge, truth_floor, bridges_sacred):
            continue
        new_bindings = _match_template(head, edge, bindings)
        if new_bindings is None:
            continue
        next_matched = matched + [edge]
        next_score = score * (edge["local_confidence"] or 1e-6)
        next_hit_bridge = hit_bridge or edge["is_bridge"]
        _search(rest, edges, new_bindings, next_matched,
                next_score, next_hit_bridge, results,
                hard_ceiling, truth_floor, bridges_sacred)
        if len(results) >= hard_ceiling:
            return


def unify(pattern: list[tuple], working_set_id: str,
          constraints: dict | None = None,
          k: int = DEFAULT_K) -> list[BindingSet]:
    """
    pattern: list of (subj_term, pred_term, obj_term)
      terms: "?Var" (binding variable), "*" (predicate wildcard),
             literal int (anchor_id), or literal str (predicate name).
    working_set_id: must be a live ws (read() fail-closes if expired).
    constraints (optional dict):
      - truth_floor: list[str] of allowed local_truth values
      - bridges_sacred: bool (default True) — bridge-tagged edges bypass truth_floor
      - hard_ceiling: int (default 64) — capped at module HARD_CEILING_K
    k: requested top-k (default 8); capped at min(constraints['hard_ceiling'], HARD_CEILING_K).
    """
    if not pattern:
        raise UnifyError("empty pattern")

    constraints = constraints or {}
    requested_ceiling = int(constraints.get("hard_ceiling", HARD_CEILING_K))
    hard_ceiling = min(requested_ceiling, HARD_CEILING_K)
    if hard_ceiling < 1:
        raise UnifyError("hard_ceiling must be >= 1")
    k_effective = max(1, min(int(k), hard_ceiling))

    truth_floor = constraints.get("truth_floor")
    bridges_sacred = bool(constraints.get("bridges_sacred", True))

    t0 = time.perf_counter()
    # fail-closed read; raises if working set is expired/missing
    wm.read(working_set_id)
    edges = _load_edges(working_set_id)

    results: list[BindingSet] = []
    _search(pattern, edges, bindings={}, matched=[], score=1.0,
            hit_bridge=False, results=results,
            hard_ceiling=hard_ceiling,
            truth_floor=truth_floor,
            bridges_sacred=bridges_sacred)

    results.sort(key=lambda b: b.score, reverse=True)
    final = results[:k_effective]
    dt_ms = (time.perf_counter() - t0) * 1000.0

    operator_trace.emit(
        operator="unify",
        inputs={"pattern_len": len(pattern),
                "k": k_effective,
                "hard_ceiling": hard_ceiling,
                "edge_count": len(edges),
                "truth_floor": list(truth_floor) if truth_floor else None,
                "bridges_sacred": bridges_sacred},
        outputs={"binding_count": len(final),
                 "any_bridge_hit": any(b.hit_bridge for b in final)},
        outcome="ok",
        working_set_id=working_set_id,
        score=final[0].score if final else 0.0,
        duration_ms=dt_ms,
    )
    return final
