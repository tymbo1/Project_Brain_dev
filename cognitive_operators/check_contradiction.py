"""
check_contradiction.py — CHECK_CONTRADICTION operator.

ContradictionScore(claim) = max_{π∈ContradictionPaths} PathScore(π)

SupportScore(claim) =
  1 - Π_i (1 - Confidence(e_i)·SourceTrust(e_i))

Truth posture:
  ContradictionScore > θ_contra   → "contradicted"
  SupportScore > θ_support        → "supported"
  else                            → "insufficient_memory"

Output:
{
  "operator":           "CHECK_CONTRADICTION",
  "claim":              "...",
  "status":             "supported|contradicted|insufficient_memory",
  "contradiction_score": 0.0,
  "support_score":      0.0,
  "contradicting_evidence": [],
  "supporting_evidence":    [],
  "confidence":         0.0
}
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from .working_memory import WorkingMemoryPacket, MemoryEdge

THETA_CONTRA  = 0.35   # contradiction score above this → claim contradicted
THETA_SUPPORT = 0.55   # support score above this → claim supported

_CONTRA_PREDS = frozenset({
    "contradicts", "negates", "incompatible_with", "refutes",
    "conflicts_with", "disproven_by", "opposes", "falsifies",
})
_SUPPORT_PREDS = frozenset({
    "supports", "confirms", "validates", "is_consistent_with",
    "is_a", "causes", "enables", "requires", "leads_to",
    "defined_as", "has_property", "produced_by",
})

# Default source trust by origin field (edge.predicate used as proxy here)
_SOURCE_TRUST_DEFAULT = 0.7


@dataclass
class ContradictionResult:
    operator: str = "CHECK_CONTRADICTION"
    claim: str = ""
    status: str = "insufficient_memory"
    contradiction_score: float = 0.0
    support_score: float = 0.0
    contradicting_evidence: list[str] = field(default_factory=list)
    supporting_evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return {
            "operator":               self.operator,
            "claim":                  self.claim,
            "status":                 self.status,
            "contradiction_score":    round(self.contradiction_score, 3),
            "support_score":          round(self.support_score, 3),
            "contradicting_evidence": self.contradicting_evidence,
            "supporting_evidence":    self.supporting_evidence,
            "confidence":             round(self.confidence, 3),
        }

    def is_certain(self) -> bool:
        return self.confidence > 0.6 and self.status != "insufficient_memory"


def run(packet: WorkingMemoryPacket, claim: str = "") -> ContradictionResult:
    """Execute CHECK_CONTRADICTION over the working memory packet."""
    subject = claim or packet.query
    result = ContradictionResult(claim=subject)

    if packet.is_empty():
        return result

    contra_edges: list[tuple[float, MemoryEdge]] = []
    support_edges: list[tuple[float, MemoryEdge]] = []

    for edge in packet.top_edges:
        score = _edge_evidence_score(edge)

        if edge.is_contradiction or edge.predicate in _CONTRA_PREDS:
            contra_edges.append((score, edge))
        elif edge.predicate in _SUPPORT_PREDS:
            support_edges.append((score, edge))

    # ContradictionScore = max path score over contradiction edges
    contra_scores = [s for s, _ in contra_edges]
    result.contradiction_score = max(contra_scores) if contra_scores else 0.0

    # SupportScore = probabilistic union: 1 - Π(1 - s_i)
    if support_edges:
        complement = 1.0
        for s, _ in support_edges:
            complement *= (1.0 - s)
        result.support_score = 1.0 - complement
    else:
        result.support_score = 0.0

    # ── Truth posture ─────────────────────────────────────────────────────────
    if result.contradiction_score > THETA_CONTRA:
        result.status = "contradicted"
    elif result.support_score > THETA_SUPPORT:
        result.status = "supported"
    else:
        result.status = "insufficient_memory"

    # ── Evidence lists ────────────────────────────────────────────────────────
    contra_edges.sort(key=lambda x: -x[0])
    support_edges.sort(key=lambda x: -x[0])

    result.contradicting_evidence = [
        f"{e.subject} {e.predicate} {e.obj} (strength={e.strength:.2f})"
        for _, e in contra_edges[:5]
    ]
    result.supporting_evidence = [
        f"{e.subject} {e.predicate} {e.obj} (strength={e.strength:.2f})"
        for _, e in support_edges[:5]
    ]

    # ── Confidence: how decisive is the verdict? ──────────────────────────────
    score_gap = abs(result.support_score - result.contradiction_score)
    evidence_weight = min((len(contra_edges) + len(support_edges)) / 10.0, 1.0)
    result.confidence = round(min(score_gap * evidence_weight + 0.2, 1.0), 3)

    return result


def _edge_evidence_score(edge: MemoryEdge) -> float:
    """Score an edge as evidence: strength × source_trust × confidence proxy."""
    return edge.strength * _SOURCE_TRUST_DEFAULT * edge.edge_score ** 0.3
