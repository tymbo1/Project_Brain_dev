"""
trace_cause.py — TRACE_CAUSE operator.

Answers: why did X happen? what caused X? what led to X?

Mirror of EXPLAIN: traverses causal edges BACKWARD from the effect to find
root causes. EXPLAIN asks "how does X produce Y?"; TRACE_CAUSE asks
"what upstream chain led to X?".

CausalScore(π) = Π_i [Conf(eᵢ) · CausalWeight(eᵢ) · Trust(eᵢ)] · e^{-λ·len(π)}

Same scoring as EXPLAIN; backward BFS indexes edges by object instead of subject.
Output chain is reversed to read cause → effect.

Root causes = nodes that appear as edge subjects in the backward traversal
              but have no inbound causal edges themselves.

Output:
{
  "operator":     "TRACE_CAUSE",
  "effect":       "...",     # the thing we traced back from
  "root_causes":  [],        # terminal upstream nodes
  "causal_chain": [],        # ordered steps: root cause → effect
  "evidence":     [],        # raw edge strings
  "confidence":   0.0,
  "completeness": 0.0,
  "uncertainty":  []
}
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from .working_memory import WorkingMemoryPacket, MemoryEdge
from .explain import (
    _CAUSAL_PRED_WEIGHTS,
    SOURCE_TRUST,
    LAMBDA_CAUSAL,
    MAX_DEPTH,
    BEAM_WIDTH,
    MAX_CHAINS_OUT,
    _VERB,
    _score_path,
    CausalPath,
)


@dataclass
class TraceCauseResult:
    operator: str = "TRACE_CAUSE"
    effect: str = ""
    root_causes: list[str] = field(default_factory=list)
    causal_chain: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    completeness: float = 0.0
    uncertainty: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "operator":     self.operator,
            "effect":       self.effect,
            "root_causes":  self.root_causes,
            "causal_chain": self.causal_chain,
            "evidence":     self.evidence,
            "confidence":   round(self.confidence, 3),
            "completeness": round(self.completeness, 3),
            "uncertainty":  self.uncertainty,
        }

    def is_sufficient(self) -> bool:
        return bool(self.root_causes) and self.confidence > 0.2


def run(packet: WorkingMemoryPacket) -> TraceCauseResult:
    """Execute TRACE_CAUSE over the working memory packet."""
    result = TraceCauseResult()

    if packet.is_empty():
        result.uncertainty.append(f"no memory for '{packet.query}'")
        return result

    # ── Extract causal edges ──────────────────────────────────────────────────
    causal_edges = [
        e for e in packet.top_edges
        if e.predicate in _CAUSAL_PRED_WEIGHTS
    ]

    if not causal_edges:
        result.uncertainty.append("no causal edges in packet")
        return result

    # ── Find effect node: highest-activated node that is a causal target ──────
    # Prefer nodes that appear as obj in causal edges (they are effects).
    # Skip the raw query string.
    effect = _find_effect_node(causal_edges, packet)
    result.effect = effect

    # ── Backward BFS from effect ──────────────────────────────────────────────
    paths = _build_backward_paths(causal_edges, effect)

    if not paths:
        # No backward paths — try any path touching effect as object
        for e in causal_edges:
            if e.obj == effect:
                paths.append(CausalPath(
                    edges=[e],
                    score=_score_path([e]),
                    nodes=[e.subject, e.obj],
                ))

    if not paths:
        result.uncertainty.append(f"no upstream causes found for '{effect}'")
        return result

    # ── Select narrative: deepest backward path from effect ───────────────────
    effect_paths = [p for p in paths if p.edges and p.edges[0].obj == effect]
    if not effect_paths:
        effect_paths = paths

    narrative_rev = max(effect_paths, key=lambda p: (len(p.edges), p.score))

    # Backward path: edges stored from effect ← ... ← root
    # Reverse so output reads root → ... → effect
    forward_edges = list(reversed(narrative_rev.edges))

    result.causal_chain = [
        f"{e.subject} {_VERB.get(e.predicate, e.predicate.replace('_', ' '))} {e.obj}"
        for e in forward_edges
    ]

    # ── Root causes: terminal upstream nodes ─────────────────────────────────
    # All nodes that appear as subjects in the backward paths but not as objects
    causal_targets = {e.obj for e in causal_edges}
    all_upstream: set[str] = set()
    for path in paths:
        for e in path.edges:
            all_upstream.add(e.subject)

    # Root = upstream node with no inbound causal edges in packet
    roots = [n for n in all_upstream if n not in causal_targets and n != effect]
    if not roots:
        # Fall back: first node in the reversed chain
        roots = [forward_edges[0].subject] if forward_edges else []

    # Sort roots by activation (most activated first)
    roots.sort(key=lambda n: -packet.node_activation(n))
    result.root_causes = roots[:5]

    # ── Evidence ──────────────────────────────────────────────────────────────
    paths.sort(key=lambda p: -p.score)
    seen: set[tuple[str, str, str]] = set()
    for path in paths[:3]:
        for e in path.edges:
            key = (e.subject, e.predicate, e.obj)
            if key not in seen:
                seen.add(key)
                result.evidence.append(
                    f"{e.subject} {e.predicate} {e.obj} (str={e.strength:.2f})"
                )

    # ── Completeness: causal predicate diversity in found paths ───────────────
    present_preds = {e.predicate for p in paths[:3] for e in p.edges}
    primary_preds = {"causes", "leads_to", "enables", "produces", "requires"}
    result.completeness = round(
        min(len(present_preds & primary_preds) / max(len(primary_preds) * 0.5, 1), 1.0),
        3,
    )

    # ── Confidence ────────────────────────────────────────────────────────────
    top_scores = [p.score for p in paths[:3]]
    mean_score = sum(top_scores) / len(top_scores) if top_scores else 0.0
    result.confidence = round(
        min(mean_score * 0.7 + packet.packet_confidence * 0.3, 0.95),
        3,
    )

    # ── Uncertainty flags ─────────────────────────────────────────────────────
    if len(paths) == 1 and len(paths[0].edges) == 1:
        result.uncertainty.append("single-edge trace — substrate may be sparse")
    if not result.root_causes:
        result.uncertainty.append("could not identify terminal root causes")
    if result.completeness < 0.3:
        result.uncertainty.append("causal coverage is partial")

    return result


# ── Backward BFS ──────────────────────────────────────────────────────────────

def _build_backward_paths(
    causal_edges: list[MemoryEdge],
    effect: str,
) -> list[CausalPath]:
    """
    BFS backward from effect: at each step, find edges where edge.obj == current.
    Path edges are stored effect ← parent ← grandparent (reversed from cause→effect).
    """
    by_object: dict[str, list[MemoryEdge]] = {}
    for e in causal_edges:
        by_object.setdefault(e.obj, []).append(e)

    finished: list[CausalPath] = []
    frontier: list[tuple[str, list[MemoryEdge], set[str]]] = [
        (effect, [], {effect})
    ]

    while frontier:
        next_frontier: list[tuple[str, list[MemoryEdge], set[str], float]] = []

        for node, path, visited in frontier:
            for edge in by_object.get(node, []):
                if edge.subject in visited:
                    continue
                new_path = path + [edge]   # edges from effect backward
                score = _score_path(new_path)
                next_frontier.append(
                    (edge.subject, new_path, visited | {edge.subject}, score)
                )
                finished.append(CausalPath(
                    edges=new_path,
                    score=score,
                    nodes=[e.obj for e in new_path] + [edge.subject],
                ))

        next_frontier.sort(key=lambda x: -x[3])
        next_gen = next_frontier[:BEAM_WIDTH]
        frontier = [
            (node, path, vis)
            for node, path, vis, _ in next_gen
            if len(path) < MAX_DEPTH
        ]

        if not frontier:
            break

    return finished


# ── Effect finder ─────────────────────────────────────────────────────────────

def _find_effect_node(
    causal_edges: list[MemoryEdge],
    packet: WorkingMemoryPacket,
) -> str:
    """
    Find the target effect: highest-activated non-query node that appears as
    the object of causal edges (i.e. something is causing it).
    Falls back to highest-activated non-query source if no inbound targets found.
    """
    causal_targets = {e.obj for e in causal_edges}

    best_node = ""
    best_act  = -1.0
    for node in packet.top_nodes:
        c = node.canonical
        if c == packet.query:
            continue
        if c in causal_targets and node.activation > best_act:
            best_act  = node.activation
            best_node = c

    if not best_node:
        # Nothing found as an effect target — fall back to any activated node
        for node in packet.top_nodes:
            c = node.canonical
            if c != packet.query and node.activation > best_act:
                best_act  = node.activation
                best_node = c

    return best_node or (packet.top_nodes[0].canonical if packet.top_nodes else packet.query)
