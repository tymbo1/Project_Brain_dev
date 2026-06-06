"""
find_gaps.py — FIND_GAPS operator.

Answers: what is missing from X? what do we need to complete X?

Gap(y) = Conf(x req y) · Importance(y) · (1 - Availability(y))

Where:
  Conf(x req y)   = edge.strength · PredicateWeight(p)   — how strongly x needs y
  Importance(y)   = fan-in weight — how many things need y
  Availability(y) = packet.node_activation(y)            — how well y is represented

High Gap(y) means: x critically needs y AND y is barely in the packet.

Output:
{
  "operator":   "FIND_GAPS",
  "subject":    "...",
  "missing":    [{"item": "...", "gap_score": 0.0, "required_by": "...",
                  "predicate": "...", "rationale": "..."}],
  "coverage":   0.0,   # 1 - gaps_above_threshold / total_required
  "confidence": 0.0,
  "uncertainty": []
}
"""

from __future__ import annotations
from dataclasses import dataclass, field
from .working_memory import WorkingMemoryPacket, MemoryEdge

# ── Gap predicate weights ─────────────────────────────────────────────────────

_GAP_PRED_WEIGHTS: dict[str, float] = {
    "requires":         1.00,
    "depends_on":       0.90,
    "needs":            0.85,
    "precondition_of":  0.85,
    "missing":          1.00,
    "lacks":            0.90,
    "blocks":           0.70,   # A blocks B → B is a gap preventing A
    "prerequisite_of":  0.88,
    "constrained_by":   0.75,
}

SOURCE_TRUST    = 0.80
THETA_GAP       = 0.20   # minimum Gap score to report
MAX_GAPS        = 8


@dataclass
class GapItem:
    item: str
    gap_score: float
    required_by: str
    predicate: str
    rationale: str = ""
    availability: float = 0.0
    importance: float = 0.0

    def as_dict(self) -> dict:
        return {
            "item":        self.item,
            "gap_score":   round(self.gap_score, 3),
            "required_by": self.required_by,
            "predicate":   self.predicate,
            "rationale":   self.rationale,
        }


@dataclass
class FindGapsResult:
    operator: str = "FIND_GAPS"
    subject: str = ""
    missing: list[GapItem] = field(default_factory=list)
    coverage: float = 0.0
    confidence: float = 0.0
    uncertainty: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "operator":    self.operator,
            "subject":     self.subject,
            "missing":     [g.as_dict() for g in self.missing],
            "coverage":    round(self.coverage, 3),
            "confidence":  round(self.confidence, 3),
            "uncertainty": self.uncertainty,
        }

    def is_sufficient(self) -> bool:
        return self.confidence > 0.2


def run(packet: WorkingMemoryPacket) -> FindGapsResult:
    """Execute FIND_GAPS over the working memory packet."""
    result = FindGapsResult(subject=packet.query)

    if packet.is_empty():
        result.uncertainty.append(f"no memory for '{packet.query}'")
        return result

    # ── Find the subject concept ──────────────────────────────────────────────
    # Best non-query node with outbound gap edges
    gap_sources = {e.subject for e in packet.top_edges if e.predicate in _GAP_PRED_WEIGHTS}
    seed = _find_gap_seed(gap_sources, packet)
    result.subject = seed

    # ── Extract gap edges ─────────────────────────────────────────────────────
    gap_edges = [
        e for e in packet.top_edges
        if e.predicate in _GAP_PRED_WEIGHTS
    ]

    if not gap_edges:
        result.uncertainty.append(f"no dependency edges found for '{seed}'")
        result.coverage = 1.0   # nothing required → nothing missing
        result.confidence = packet.packet_confidence * 0.4
        return result

    # ── Count fan-in for Importance ───────────────────────────────────────────
    # Importance(y) = how many distinct gap edges point to y
    fan_in: dict[str, int] = {}
    for e in gap_edges:
        fan_in[e.obj] = fan_in.get(e.obj, 0) + 1
    max_fan_in = max(fan_in.values()) if fan_in else 1

    # ── Score each required item ──────────────────────────────────────────────
    seen_items: set[str] = set()
    candidates: list[GapItem] = []

    for edge in gap_edges:
        item = edge.obj
        if item in seen_items:
            continue
        seen_items.add(item)

        pred_weight    = _GAP_PRED_WEIGHTS.get(edge.predicate, 0.5)
        conf_req       = edge.strength * pred_weight * SOURCE_TRUST
        importance     = fan_in.get(item, 1) / max_fan_in
        availability   = packet.node_activation(item)
        gap_score      = conf_req * importance * (1.0 - availability)

        rationale = _build_rationale(edge, availability, importance)

        candidates.append(GapItem(
            item=item,
            gap_score=round(gap_score, 4),
            required_by=edge.subject,
            predicate=edge.predicate,
            rationale=rationale,
            availability=availability,
            importance=importance,
        ))

    # ── Filter and rank ───────────────────────────────────────────────────────
    candidates.sort(key=lambda g: -g.gap_score)
    result.missing = [g for g in candidates if g.gap_score >= THETA_GAP][:MAX_GAPS]

    # ── Coverage: 1 - fraction of required items that are gaps ───────────────
    total_required = len(candidates)
    n_gaps = len(result.missing)
    result.coverage = round(1.0 - (n_gaps / max(total_required, 1)), 3)

    # ── Confidence ────────────────────────────────────────────────────────────
    if candidates:
        mean_gap = sum(g.gap_score for g in candidates) / len(candidates)
        result.confidence = round(
            min(packet.packet_confidence * 0.5 + mean_gap * 0.5 + 0.15, 0.95),
            3,
        )
    else:
        result.confidence = round(packet.packet_confidence * 0.5, 3)

    # ── Uncertainty flags ─────────────────────────────────────────────────────
    if result.coverage > 0.8 and n_gaps == 0:
        result.uncertainty.append("no significant gaps detected — substrate may be incomplete")
    if total_required < 2:
        result.uncertainty.append("few dependency edges — gap analysis is partial")

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_gap_seed(gap_sources: set[str], packet: WorkingMemoryPacket) -> str:
    """Highest-activated non-query node that has outbound gap edges."""
    best_node = ""
    best_act = -1.0
    for node in packet.top_nodes:
        c = node.canonical
        if c == packet.query:
            continue
        if c in gap_sources and node.activation > best_act:
            best_act = node.activation
            best_node = c
    if not best_node:
        for source in gap_sources:
            act = packet.node_activation(source)
            if act > best_act:
                best_act = act
                best_node = source
    return best_node or (packet.top_nodes[0].canonical if packet.top_nodes else packet.query)


def _build_rationale(edge: MemoryEdge, availability: float, importance: float) -> str:
    verb = edge.predicate.replace("_", " ")
    parts = [f"{edge.subject} {verb} {edge.obj}"]
    if availability < 0.1:
        parts.append("not in memory")
    elif availability < 0.4:
        parts.append("partially covered")
    if importance > 0.7:
        parts.append("high importance")
    return "; ".join(parts)
