"""
assess_confidence.py — ASSESS_CONFIDENCE operator.

Conf(answer) = σ(
  c₁·EvidenceStrength
+ c₂·Coverage
+ c₃·Coherence(K)
+ c₄·SourceTrustMean
- c₅·ContradictionScore
- c₆·ExtrapolationPenalty
- c₇·MissingDataPenalty
)

Thresholds by domain:
  identity / personal:  0.85
  project memory:       0.75
  knowledge:            0.70
  creative/speculative: 0.45

EvidenceStrength = 1 - Π_i(1 - Confidence(e_i)·SourceTrust(e_i))

Coherence(K) =
  Σ_{i,j∈K} A(i)·A(j)·RelationStrength(i,j)
  /
  Σ_{i,j∈K} A(i)·A(j)
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from .working_memory import WorkingMemoryPacket

# ── Weights ───────────────────────────────────────────────────────────────────
C1 = 0.25   # EvidenceStrength
C2 = 0.20   # Coverage
C3 = 0.20   # Coherence
C4 = 0.15   # SourceTrustMean
C5 = 0.10   # ContradictionScore (penalty)
C6 = 0.05   # ExtrapolationPenalty
C7 = 0.05   # MissingDataPenalty

_SOURCE_TRUST_DEFAULT = 0.70

# ── Per-lane confidence thresholds ────────────────────────────────────────────
CONFIDENCE_THRESHOLDS = {
    "identity":     0.85,
    "relationship": 0.80,
    "project":      0.75,
    "knowledge":    0.70,
    "creative":     0.45,
    "speculative":  0.45,
}


@dataclass
class ConfidenceResult:
    operator: str = "ASSESS_CONFIDENCE"
    subject: str = ""
    confidence: float = 0.0
    threshold: float = 0.70
    passes_threshold: bool = False
    evidence_strength: float = 0.0
    coverage: float = 0.0
    coherence: float = 0.0
    source_trust_mean: float = 0.0
    contradiction_score: float = 0.0
    extrapolation_penalty: float = 0.0
    missing_data_penalty: float = 0.0
    lane: str = "knowledge"
    uncertainty_label: str = ""

    def as_dict(self) -> dict:
        return {
            "operator":            self.operator,
            "subject":             self.subject,
            "confidence":          round(self.confidence, 3),
            "threshold":           self.threshold,
            "passes_threshold":    self.passes_threshold,
            "evidence_strength":   round(self.evidence_strength, 3),
            "coverage":            round(self.coverage, 3),
            "coherence":           round(self.coherence, 3),
            "contradiction_score": round(self.contradiction_score, 3),
            "uncertainty_label":   self.uncertainty_label,
        }


def run(
    packet: WorkingMemoryPacket,
    required_slots: list[str] | None = None,
    filled_slots: list[str] | None = None,
    contradiction_score: float = 0.0,
) -> ConfidenceResult:
    """
    Compute answer confidence from packet evidence.

    Args:
        packet:             Working memory packet.
        required_slots:     Slots the operator expected (e.g. DEFINE slots).
        filled_slots:       Slots that were actually filled.
        contradiction_score: Pre-computed from CHECK_CONTRADICTION if available.
    """
    lane = packet.source_lane
    result = ConfidenceResult(
        subject=packet.query,
        lane=lane,
        threshold=CONFIDENCE_THRESHOLDS.get(lane, 0.70),
    )

    if packet.is_empty():
        result.uncertainty_label = "no_memory"
        return result

    edges = packet.top_edges
    nodes = packet.top_nodes

    # ── EvidenceStrength: probabilistic union of edge evidence ────────────────
    if edges:
        complement = 1.0
        for e in edges[:20]:
            s = e.strength * _SOURCE_TRUST_DEFAULT
            complement *= (1.0 - s)
        result.evidence_strength = 1.0 - complement
    else:
        result.evidence_strength = 0.0

    # ── Coverage: filled / required slots ────────────────────────────────────
    if required_slots and filled_slots:
        result.coverage = len(set(filled_slots) & set(required_slots)) / max(len(required_slots), 1)
    else:
        # Proxy: top node count / expected depth
        result.coverage = min(len(nodes) / 10.0, 1.0)

    # ── Coherence(K): weighted edge density ──────────────────────────────────
    result.coherence = _compute_coherence(packet)

    # ── SourceTrustMean ───────────────────────────────────────────────────────
    # Using edge.strength as proxy (curated high-strength edges = higher trust)
    if edges:
        result.source_trust_mean = sum(e.strength for e in edges[:10]) / min(len(edges), 10)
    else:
        result.source_trust_mean = 0.0

    # ── Contradiction score ───────────────────────────────────────────────────
    if contradiction_score > 0:
        result.contradiction_score = contradiction_score
    elif packet.contradictions:
        result.contradiction_score = max(e.strength for e in packet.contradictions)
    else:
        result.contradiction_score = 0.0

    # ── Extrapolation penalty: depth-4+ chains = extrapolation ───────────────
    deep_paths = [p for p in packet.top_paths if len(p.nodes) > 4]
    result.extrapolation_penalty = min(len(deep_paths) / max(len(packet.top_paths), 1), 1.0)

    # ── Missing data penalty ──────────────────────────────────────────────────
    result.missing_data_penalty = min(
        len(packet.missing_requirements) * 0.15 +
        (1.0 - result.coverage) * 0.5,
        1.0,
    )

    # ── Logit aggregation → sigmoid ───────────────────────────────────────────
    logit = (
        C1 * result.evidence_strength
      + C2 * result.coverage
      + C3 * result.coherence
      + C4 * result.source_trust_mean
      - C5 * result.contradiction_score
      - C6 * result.extrapolation_penalty
      - C7 * result.missing_data_penalty
    )
    # Centre logit around 0; scale is 0–1 so multiply by 4 before sigmoid
    result.confidence = round(_sigmoid(logit * 4.0 - 2.0), 3)

    result.passes_threshold = result.confidence >= result.threshold
    result.uncertainty_label = _uncertainty_label(result.confidence, result.threshold)

    return result


def _compute_coherence(packet: WorkingMemoryPacket) -> float:
    """
    Coherence(K) = Σ A(i)·A(j)·Rel(i,j) / Σ A(i)·A(j)
    Approximated over top edges.
    """
    nodes = packet.top_nodes[:15]
    if len(nodes) < 2:
        return 0.5

    # Build activation lookup
    act = {n.canonical: n.activation for n in nodes}

    numerator   = 0.0
    denominator = 0.0

    for e in packet.top_edges[:30]:
        a_u = act.get(e.subject, 0.0)
        a_v = act.get(e.obj,     0.0)
        if a_u == 0.0 or a_v == 0.0:
            continue
        pair_weight = a_u * a_v
        numerator   += pair_weight * e.strength
        denominator += pair_weight

    if denominator == 0:
        return 0.5
    return min(numerator / denominator, 1.0)


def _sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _uncertainty_label(conf: float, threshold: float) -> str:
    if conf >= threshold:
        return "confident"
    if conf >= threshold * 0.7:
        return "partial"
    if conf >= 0.2:
        return "uncertain"
    return "no_memory"
