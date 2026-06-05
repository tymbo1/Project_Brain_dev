"""
working_memory.py — WorkingMemoryPacket operator.

Compresses the activated graph from activation_engine into a typed working
memory packet that cognitive operators can reason over.

K(q) = {top_nodes, top_edges, top_paths, source_lane, confidence,
         contradictions, missing_requirements}

Node inclusion:   v ∈ K  if A(v) ≥ θ_node
Edge inclusion:   e ∈ K  if A(u)·W(e)·A(v) ≥ θ_edge
Path score (log): log A₀(v₀) + Σ_i log W(e_i) - λ·len(π)
"""

from __future__ import annotations
import math
import re
from dataclasses import dataclass, field
from typing import Any

# ── Thresholds ────────────────────────────────────────────────────────────────
THETA_NODE = 0.05   # minimum normalised A(v) to include node in K
THETA_EDGE = 0.002  # minimum A(u)·W(e)·A(v) to include edge
LAMBDA_PATH = 0.35  # depth decay for path scoring (matches activation_engine)
MAX_TOP_NODES  = 30
MAX_TOP_EDGES  = 60
MAX_TOP_PATHS  = 10

# Predicates that signal contradiction / support in the graph
_CONTRA_PREDS = frozenset({
    "contradicts", "negates", "incompatible_with", "refutes",
    "conflicts_with", "disproven_by", "opposes",
})
_CAUSAL_PREDS = frozenset({
    "causes", "enables", "requires", "leads_to", "prevents",
    "produces", "depends_on", "stabilises", "destabilises",
})
_DEFN_PREDS = frozenset({
    "is_a", "type_of", "defined_as", "has_property",
    "part_of", "used_for", "contrasts_with", "subtype_of",
    "instance_of", "also_known_as",
})


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class MemoryNode:
    canonical: str
    activation: float          # normalised A(v), 0–1
    predicates: list[str] = field(default_factory=list)
    anchor_type: str = ""
    domain: str = ""
    lane: str = "knowledge"


@dataclass
class MemoryEdge:
    subject: str
    predicate: str
    obj: str
    strength: float            # normalised, 0–1
    edge_score: float = 0.0    # A(u)·W·A(v)
    is_contradiction: bool = False


@dataclass
class MemoryPath:
    nodes: list[str]
    edges: list[MemoryEdge]
    log_score: float
    path_type: str = "associative"  # causal / definitional / associative


@dataclass
class WorkingMemoryPacket:
    query: str
    source_lane: str                        # identity / relationship / project / knowledge
    top_nodes:  list[MemoryNode]            = field(default_factory=list)
    top_edges:  list[MemoryEdge]            = field(default_factory=list)
    top_paths:  list[MemoryPath]            = field(default_factory=list)
    contradictions: list[MemoryEdge]        = field(default_factory=list)
    missing_requirements: list[str]         = field(default_factory=list)
    raw_chains: list[str]                   = field(default_factory=list)
    packet_confidence: float                = 0.0
    operator_hint: str                      = ""   # pre-filled by memory_router if known

    # ── Quick-access helpers ───────────────────────────────────────────────────

    def node_activation(self, canonical: str) -> float:
        for n in self.top_nodes:
            if n.canonical == canonical:
                return n.activation
        return 0.0

    def edges_for(self, subject: str) -> list[MemoryEdge]:
        return [e for e in self.top_edges if e.subject == subject]

    def predicates_present(self) -> set[str]:
        return {e.predicate for e in self.top_edges}

    def has_predicate_class(self, pred_set: frozenset) -> bool:
        return bool(self.predicates_present() & pred_set)

    def causal_edges(self) -> list[MemoryEdge]:
        return [e for e in self.top_edges if e.predicate in _CAUSAL_PREDS]

    def definitional_edges(self) -> list[MemoryEdge]:
        return [e for e in self.top_edges if e.predicate in _DEFN_PREDS]

    def is_empty(self) -> bool:
        return len(self.top_nodes) == 0 and len(self.raw_chains) == 0


# ── Chain parsing ─────────────────────────────────────────────────────────────

_STRENGTH_RE = re.compile(r'\|\s*strength:\s*(\d+\.?\d*)', re.IGNORECASE)

def _parse_chain(chain: str) -> tuple[str, str, str, float] | None:
    """Parse 'subj | pred | obj | strength: N' → (subj, pred, obj, normalised_strength).

    Accepts both integer (92) and decimal (0.92) strength values.
    Integers > 1 are treated as percentages and divided by 100.
    """
    m = _STRENGTH_RE.search(chain)
    if m:
        raw = float(m.group(1))
        normalised = min(raw / 100.0 if raw > 1.0 else raw, 1.0)
    else:
        normalised = 0.5

    core = _STRENGTH_RE.sub("", chain).strip().rstrip("|").strip()
    parts = [p.strip() for p in core.split("|")]
    if len(parts) < 3:
        return None
    subj, pred, obj = parts[0], parts[1], parts[2]
    if not subj or not pred or not obj:
        return None
    return subj, pred, obj, normalised


# ── Builder ───────────────────────────────────────────────────────────────────

def build_packet(
    query: str,
    chains: list[str],
    source_lane: str = "knowledge",
    operator_hint: str = "",
    missing_requirements: list[str] | None = None,
) -> WorkingMemoryPacket:
    """
    Convert raw activation chains into a WorkingMemoryPacket.

    Args:
        query:         The original query string.
        chains:        Raw chain strings from ActivationEngine.infer().
        source_lane:   Memory lane selected by router.
        operator_hint: If router already knows the operator, pass it here.
        missing_requirements: Slot gaps from memory_router or higher caller.
    """
    parsed: list[tuple[str, str, str, float]] = []
    for c in chains:
        r = _parse_chain(c)
        if r:
            parsed.append(r)

    if not parsed and not chains:
        return WorkingMemoryPacket(
            query=query,
            source_lane=source_lane,
            raw_chains=chains,
            operator_hint=operator_hint,
        )

    # ── Build node activation map ─────────────────────────────────────────────
    # A(v) = max strength across all edges touching v, normalised
    node_act: dict[str, float] = {}
    node_preds: dict[str, set[str]] = {}

    for subj, pred, obj, strength in parsed:
        node_act[subj] = max(node_act.get(subj, 0.0), strength)
        node_act[obj]  = max(node_act.get(obj,  0.0), strength * 0.8)  # slight decay for objects
        node_preds.setdefault(subj, set()).add(pred)
        node_preds.setdefault(obj,  set()).add(pred)

    # Seed query node always present at full activation
    node_act[query] = max(node_act.get(query, 0.0), 1.0)

    # Normalise activations
    max_act = max(node_act.values()) if node_act else 1.0
    if max_act > 0:
        node_act = {k: v / max_act for k, v in node_act.items()}

    # ── Top nodes (by activation) ─────────────────────────────────────────────
    top_nodes = []
    for canon, act in sorted(node_act.items(), key=lambda x: -x[1]):
        if act < THETA_NODE:
            break
        if len(top_nodes) >= MAX_TOP_NODES:
            break
        top_nodes.append(MemoryNode(
            canonical=canon,
            activation=act,
            predicates=sorted(node_preds.get(canon, set())),
        ))

    # ── Top edges (by edge_score = A(u)·strength·A(v)) ───────────────────────
    edges: list[MemoryEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()

    for subj, pred, obj, strength in parsed:
        key = (subj, pred, obj)
        if key in seen_edges:
            continue
        seen_edges.add(key)

        a_u = node_act.get(subj, 0.0)
        a_v = node_act.get(obj,  0.0)
        edge_score = a_u * strength * a_v

        if edge_score < THETA_EDGE and strength < 0.3:
            continue

        is_contra = pred in _CONTRA_PREDS
        edges.append(MemoryEdge(
            subject=subj,
            predicate=pred,
            obj=obj,
            strength=strength,
            edge_score=edge_score,
            is_contradiction=is_contra,
        ))

    edges.sort(key=lambda e: -e.edge_score)
    top_edges = edges[:MAX_TOP_EDGES]
    contradictions = [e for e in top_edges if e.is_contradiction]

    # ── Top paths (BFS over parsed triples, scored in log space) ─────────────
    adj: dict[str, list[tuple[str, str, float]]] = {}  # subj → [(pred, obj, strength)]
    for subj, pred, obj, strength in parsed:
        adj.setdefault(subj, []).append((pred, obj, strength))

    top_paths = _find_top_paths(query, adj, node_act, parsed)

    # ── Packet confidence: mean A(v) over top-5 nodes × edge coverage ────────
    if top_nodes:
        top5_act = sum(n.activation for n in top_nodes[:5]) / min(5, len(top_nodes))
        edge_cov = min(len(top_edges) / 20.0, 1.0)
        packet_confidence = round((top5_act * 0.7 + edge_cov * 0.3), 3)
    else:
        packet_confidence = 0.0

    return WorkingMemoryPacket(
        query=query,
        source_lane=source_lane,
        top_nodes=top_nodes,
        top_edges=top_edges,
        top_paths=top_paths,
        contradictions=contradictions,
        missing_requirements=missing_requirements or [],
        raw_chains=chains,
        packet_confidence=packet_confidence,
        operator_hint=operator_hint,
    )


def _find_top_paths(
    query: str,
    adj: dict[str, list[tuple[str, str, float]]],
    node_act: dict[str, float],
    parsed: list[tuple[str, str, str, float]],
    max_depth: int = 5,
) -> list[MemoryPath]:
    """BFS in log-score space to find top-K paths from query node."""
    from collections import deque

    # Seed log score = log(A₀(query))
    seed_act = node_act.get(query, 1.0)
    seed_log = math.log(max(seed_act, 1e-9))

    # (log_score, current_node, path_nodes, path_edges)
    queue: deque[tuple[float, str, list[str], list[tuple[str, str, str, float]]]] = deque()
    queue.append((seed_log, query, [query], []))

    best_paths: list[MemoryPath] = []
    visited_prefixes: set[tuple[str, ...]] = set()

    while queue and len(best_paths) < MAX_TOP_PATHS * 3:
        log_score, current, path_nodes, path_edges = queue.popleft()

        prefix = tuple(path_nodes)
        if prefix in visited_prefixes:
            continue
        visited_prefixes.add(prefix)

        if len(path_edges) > 0:
            # Classify path type
            preds = {pe[1] for pe in path_edges}
            if preds & _CAUSAL_PREDS:
                ptype = "causal"
            elif preds & _DEFN_PREDS:
                ptype = "definitional"
            else:
                ptype = "associative"

            mem_edges = [
                MemoryEdge(subject=pe[0], predicate=pe[1], obj=pe[2], strength=pe[3])
                for pe in path_edges
            ]
            best_paths.append(MemoryPath(
                nodes=list(path_nodes),
                edges=mem_edges,
                log_score=log_score,
                path_type=ptype,
            ))

        if len(path_nodes) >= max_depth:
            continue

        for pred, next_node, strength in adj.get(current, []):
            if next_node in path_nodes:
                continue
            w_log = math.log(max(strength, 1e-9))
            new_log = log_score + w_log - LAMBDA_PATH
            queue.append((
                new_log,
                next_node,
                path_nodes + [next_node],
                path_edges + [(current, pred, next_node, strength)],
            ))

    best_paths.sort(key=lambda p: -p.log_score)
    return best_paths[:MAX_TOP_PATHS]
