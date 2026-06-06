"""
explain.py — EXPLAIN operator.

Answers: how does X work? why does X happen?

CausalScore(π) = Π_i [Conf(eᵢ) · CausalWeight(eᵢ) · Trust(eᵢ)] · e^{-λ·len(π)}

Second-pass traversal: BFS from query concept through causal edges only.
Stops at max_depth=4, beam_width=6 best partial paths.

Output:
{
  "operator":     "EXPLAIN",
  "subject":      "...",
  "mechanism":    "...",     # one-sentence mechanism summary
  "causal_chain": [],        # ordered explanation steps
  "evidence":     [],        # raw edge strings for grounding
  "confidence":   0.0,
  "completeness": 0.0,
  "uncertainty":  []
}
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from .working_memory import WorkingMemoryPacket, MemoryEdge

# ── Causal predicate weights ──────────────────────────────────────────────────
# Higher weight = stronger causal signal

_CAUSAL_PRED_WEIGHTS: dict[str, float] = {
    "causes":       1.00,
    "leads_to":     0.95,
    "enables":      0.85,
    "produces":     0.85,
    "requires":     0.75,   # prerequisite — mechanistically important
    "depends_on":   0.70,
    "stabilises":   0.65,
    "prevents":     0.60,   # negative causation
    "destabilises": 0.60,
    "triggers":     0.90,
    "results_in":   0.90,
    "contributes_to": 0.70,
    "regulates":    0.75,
    "impairs":      0.65,
    "affects":      0.55,
}

SOURCE_TRUST   = 0.80   # default source trust for edge evidence
LAMBDA_CAUSAL  = 0.30   # length penalty (slightly less aggressive than path scoring)
MAX_DEPTH      = 4      # max chain depth
BEAM_WIDTH     = 6      # top partial paths to expand
MAX_CHAINS_OUT = 5      # chains returned in output


@dataclass
class CausalPath:
    edges: list[MemoryEdge]
    score: float
    nodes: list[str] = field(default_factory=list)

    def step_labels(self) -> list[str]:
        """Human-readable ordered steps for the causal chain."""
        labels = []
        for e in self.edges:
            verb = _VERB.get(e.predicate, e.predicate.replace("_", " "))
            labels.append(f"{e.subject} {verb} {e.obj}")
        return labels

    def as_dict(self) -> dict:
        return {
            "steps":    self.step_labels(),
            "score":    round(self.score, 4),
            "length":   len(self.edges),
        }


@dataclass
class ExplainResult:
    operator: str = "EXPLAIN"
    subject: str = ""
    mechanism: str = ""
    causal_chain: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    causal_paths: list[CausalPath] = field(default_factory=list)
    confidence: float = 0.0
    completeness: float = 0.0
    uncertainty: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "operator":     self.operator,
            "subject":      self.subject,
            "mechanism":    self.mechanism,
            "causal_chain": self.causal_chain,
            "evidence":     self.evidence,
            "confidence":   round(self.confidence, 3),
            "completeness": round(self.completeness, 3),
            "uncertainty":  self.uncertainty,
        }

    def is_sufficient(self, min_completeness: float = 0.3) -> bool:
        return self.completeness >= min_completeness and self.confidence > 0.2


def _find_causal_seed(causal_sources: set[str], packet: WorkingMemoryPacket) -> str:
    """
    Return the highest-activated node that has outbound causal edges,
    skipping the raw query string (which always gets activation=1.0 but
    never appears as an edge subject).
    """
    best_node = ""
    best_act = -1.0
    for node in packet.top_nodes:
        c = node.canonical
        if c == packet.query:
            continue
        if c in causal_sources and node.activation > best_act:
            best_act = node.activation
            best_node = c
    if not best_node:
        # No non-query node found — use any causal source by activation
        for source in causal_sources:
            act = packet.node_activation(source)
            if act > best_act:
                best_act = act
                best_node = source
    return best_node or (packet.top_nodes[0].canonical if packet.top_nodes else packet.query)


def run(packet: WorkingMemoryPacket) -> ExplainResult:
    """Execute EXPLAIN operator over the working memory packet."""
    result = ExplainResult(subject=packet.query)

    if packet.is_empty():
        result.uncertainty.append(f"no memory for '{packet.query}'")
        return result

    # ── Find causal seed: highest-activated node with outbound causal edges ───
    # top_nodes[0] is often the raw query string (activation=1.0); skip it.
    causal_sources_all = {e.subject for e in packet.top_edges if e.predicate in _CAUSAL_PRED_WEIGHTS}
    seed = _find_causal_seed(causal_sources_all, packet)
    result.subject = seed

    # ── Extract causal edges ──────────────────────────────────────────────────
    causal_edges = [
        e for e in packet.top_edges
        if e.predicate in _CAUSAL_PRED_WEIGHTS
    ]

    if not causal_edges:
        result.uncertainty.append(f"no causal edges found for '{seed}'")
        # Fall back to any edges from the seed node
        causal_edges = [e for e in packet.top_edges if e.subject == seed][:6]

    if not causal_edges:
        result.uncertainty.append("insufficient causal substrate")
        return result

    # ── Second-pass BFS through causal edges ──────────────────────────────────
    paths = _build_causal_paths(causal_edges, seed)

    if not paths:
        result.uncertainty.append("causal chains not connected to query concept")
        return result

    # ── Select narrative chain: deepest path rooted at seed ──────────────────
    # CausalScore penalises length — fine for ranking credibility, but for
    # explanation we want the most complete chain from the seed concept.
    seed_paths = [p for p in paths if p.edges and p.edges[0].subject == seed]
    if not seed_paths:
        seed_paths = paths  # fall back if seed has no outbound causal edges

    # Primary narrative: longest coherent chain from seed
    narrative = max(seed_paths, key=lambda p: (len(p.edges), p.score))

    # Rank all paths by score for confidence calculation
    paths.sort(key=lambda p: -p.score)
    result.causal_paths = paths[:MAX_CHAINS_OUT]

    result.causal_chain = narrative.step_labels()
    result.mechanism = _build_mechanism(seed, narrative)

    # ── Evidence: raw edge strings ────────────────────────────────────────────
    seen_edges: set[tuple[str, str, str]] = set()
    for path in paths[:3]:
        for e in path.edges:
            key = (e.subject, e.predicate, e.obj)
            if key not in seen_edges:
                seen_edges.add(key)
                result.evidence.append(
                    f"{e.subject} {e.predicate} {e.obj} (str={e.strength:.2f})"
                )

    # ── Completeness: fraction of causal predicate classes represented ────────
    present_preds = {e.predicate for path in paths[:3] for e in path.edges}
    primary_preds = {"causes", "leads_to", "enables", "produces", "requires"}
    filled = len(present_preds & primary_preds)
    result.completeness = round(min(filled / max(len(primary_preds) * 0.5, 1), 1.0), 3)

    # ── Confidence: mean causal score × packet confidence ────────────────────
    top_scores = [p.score for p in paths[:3]]
    mean_score = sum(top_scores) / len(top_scores) if top_scores else 0.0
    result.confidence = round(
        min(mean_score * 0.7 + packet.packet_confidence * 0.3, 0.95),
        3,
    )

    # ── Uncertainty: flag thin chains ─────────────────────────────────────────
    if len(paths) == 1 and len(paths[0].edges) == 1:
        result.uncertainty.append("explanation based on single edge — substrate may be sparse")
    if result.completeness < 0.3:
        result.uncertainty.append("causal coverage is partial")

    return result


# ── Causal path builder ───────────────────────────────────────────────────────

def _build_causal_paths(
    causal_edges: list[MemoryEdge],
    seed: str,
) -> list[CausalPath]:
    """
    BFS from seed through causal edges.
    State: (current_node, path_so_far, cumulative_log_score)
    Returns scored CausalPath objects.
    """
    # Index edges by subject for O(1) lookup
    by_subject: dict[str, list[MemoryEdge]] = {}
    for e in causal_edges:
        by_subject.setdefault(e.subject, []).append(e)

    finished: list[CausalPath] = []

    # frontier: (node, edges_so_far, visited_nodes)
    frontier: list[tuple[str, list[MemoryEdge], set[str]]] = [
        (seed, [], {seed})
    ]

    while frontier:
        # Expand all frontier states; keep beam of BEAM_WIDTH best
        next_frontier: list[tuple[str, list[MemoryEdge], set[str], float]] = []

        for node, path, visited in frontier:
            for edge in by_subject.get(node, []):
                if edge.obj in visited:
                    continue
                new_path = path + [edge]
                score = _score_path(new_path)
                next_frontier.append((edge.obj, new_path, visited | {edge.obj}, score))
                # Every partial path of length ≥ 1 is a candidate
                finished.append(CausalPath(
                    edges=new_path,
                    score=score,
                    nodes=[e.subject for e in new_path] + [edge.obj],
                ))

        # Beam: keep top-BEAM_WIDTH by score for next expansion
        next_frontier.sort(key=lambda x: -x[3])
        next_gen = next_frontier[:BEAM_WIDTH]

        # Prune: don't expand beyond MAX_DEPTH
        frontier = [
            (node, path, vis)
            for node, path, vis, _ in next_gen
            if len(path) < MAX_DEPTH
        ]

        if not frontier:
            break

    # Also include single-hop edges from non-seed nodes if seed had no outbound
    if not finished:
        for e in causal_edges:
            finished.append(CausalPath(
                edges=[e],
                score=_score_path([e]),
                nodes=[e.subject, e.obj],
            ))

    return finished


def _score_path(edges: list[MemoryEdge]) -> float:
    """CausalScore(π) = Π_i [strength_i · CausalWeight_i · Trust] · e^{-λ·len}"""
    if not edges:
        return 0.0
    product = 1.0
    for e in edges:
        w = _CAUSAL_PRED_WEIGHTS.get(e.predicate, 0.5)
        product *= e.strength * w * SOURCE_TRUST
    return product * math.exp(-LAMBDA_CAUSAL * len(edges))


def _build_mechanism(seed: str, path: CausalPath) -> str:
    """Synthesise a one-sentence mechanism from the best causal path."""
    if not path.edges:
        return ""
    first = path.edges[0]
    verb = _VERB.get(first.predicate, first.predicate.replace("_", " "))

    if len(path.edges) == 1:
        return f"{first.subject} {verb} {first.obj}."

    last = path.edges[-1]
    last_verb = _VERB.get(last.predicate, last.predicate.replace("_", " "))
    return (
        f"{first.subject} {verb} {first.obj}, "
        f"which ultimately {last_verb} {last.obj}."
    )


# ── Verb map for readable output ──────────────────────────────────────────────

_VERB: dict[str, str] = {
    "causes":         "causes",
    "leads_to":       "leads to",
    "enables":        "enables",
    "produces":       "produces",
    "requires":       "requires",
    "depends_on":     "depends on",
    "stabilises":     "stabilises",
    "prevents":       "prevents",
    "destabilises":   "destabilises",
    "triggers":       "triggers",
    "results_in":     "results in",
    "contributes_to": "contributes to",
    "regulates":      "regulates",
    "impairs":        "impairs",
    "affects":        "affects",
}
