"""
compare.py — COMPARE operator.

Answers: how does A differ from B? compare X and Y.

Sim(A,B) = Σ min(wA,wB) / Σ max(wA,wB)

Generalised Jaccard similarity over weighted feature sets.
Features are (predicate, object) pairs extracted from packet edges.

DiffScore(f) = |wA(f) - wB(f)| / max(wA(f), wB(f))   for shared features
             = 1.0                                      for exclusive features

Output:
{
  "operator":   "COMPARE",
  "subject_a":  "...",
  "subject_b":  "...",
  "similarity": 0.0,
  "shared":     [{"predicate": "...", "value": "...",
                  "a_strength": 0.0, "b_strength": 0.0, "diff_score": 0.0}],
  "only_a":     [{"predicate": "...", "value": "...", "strength": 0.0}],
  "only_b":     [{"predicate": "...", "value": "...", "strength": 0.0}],
  "verdict":    "similar|related|different|distinct",
  "confidence": 0.0,
  "uncertainty": []
}
"""

from __future__ import annotations
from dataclasses import dataclass, field
from .working_memory import WorkingMemoryPacket, MemoryEdge

# ── Predicates that mark explicit contrast/similarity relationships ───────────
_CONTRAST_PREDS  = frozenset({"contrasts_with", "differs_from", "opposes", "unlike"})
_SIMILAR_PREDS   = frozenset({"similar_to", "related_to", "analogous_to", "like"})

# ── Predicates excluded from feature comparison (too generic / not discriminating)
_SKIP_PREDS = frozenset({
    "mentioned_in", "found_in", "discussed_in", "appears_in",
})

# ── Verdict thresholds ────────────────────────────────────────────────────────
THETA_SIMILAR  = 0.65   # Sim ≥ this → "similar"
THETA_RELATED  = 0.35   # Sim ≥ this → "related"
THETA_DIFF     = 0.15   # Sim ≥ this → "different"
# Below THETA_DIFF → "distinct"

MIN_FEATURES   = 2      # minimum features per concept for a meaningful comparison
MAX_ITEMS_OUT  = 6      # max items per shared/only_a/only_b list


@dataclass
class SharedFeature:
    predicate: str
    value: str
    a_strength: float
    b_strength: float
    diff_score: float   # 0 = identical, 1 = maximally different

    def as_dict(self) -> dict:
        return {
            "predicate":   self.predicate,
            "value":       self.value,
            "a_strength":  round(self.a_strength, 3),
            "b_strength":  round(self.b_strength, 3),
            "diff_score":  round(self.diff_score, 3),
        }


@dataclass
class ExclusiveFeature:
    predicate: str
    value: str
    strength: float

    def as_dict(self) -> dict:
        return {
            "predicate": self.predicate,
            "value":     self.value,
            "strength":  round(self.strength, 3),
        }


@dataclass
class CompareResult:
    operator: str = "COMPARE"
    subject_a: str = ""
    subject_b: str = ""
    similarity: float = 0.0
    shared: list[SharedFeature] = field(default_factory=list)
    only_a: list[ExclusiveFeature] = field(default_factory=list)
    only_b: list[ExclusiveFeature] = field(default_factory=list)
    verdict: str = "insufficient_memory"
    confidence: float = 0.0
    uncertainty: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "operator":   self.operator,
            "subject_a":  self.subject_a,
            "subject_b":  self.subject_b,
            "similarity": round(self.similarity, 3),
            "shared":     [f.as_dict() for f in self.shared],
            "only_a":     [f.as_dict() for f in self.only_a],
            "only_b":     [f.as_dict() for f in self.only_b],
            "verdict":    self.verdict,
            "confidence": round(self.confidence, 3),
            "uncertainty": self.uncertainty,
        }

    def is_sufficient(self) -> bool:
        return self.confidence > 0.25 and self.verdict != "insufficient_memory"


def run(packet: WorkingMemoryPacket) -> CompareResult:
    """Execute COMPARE over the working memory packet."""
    result = CompareResult()

    if packet.is_empty():
        result.uncertainty.append("no memory — cannot compare")
        return result

    # ── Find A and B ──────────────────────────────────────────────────────────
    a, b = _find_subjects(packet)
    if not a or not b:
        result.uncertainty.append("could not identify two distinct concepts to compare")
        return result

    result.subject_a = a
    result.subject_b = b

    # ── Extract feature sets ──────────────────────────────────────────────────
    # Features: (predicate, obj) → strength, restricted to edges from each subject
    features_a = _extract_features(packet, a)
    features_b = _extract_features(packet, b)

    if len(features_a) < MIN_FEATURES and len(features_b) < MIN_FEATURES:
        result.uncertainty.append(
            f"insufficient features for '{a}' and '{b}' — substrate may be sparse"
        )
        # Still attempt partial comparison
        if not features_a and not features_b:
            return result

    # ── Compute Sim(A,B) = Σmin(wA,wB) / Σmax(wA,wB) ────────────────────────
    all_keys = set(features_a) | set(features_b)

    numerator   = 0.0
    denominator = 0.0
    shared_list: list[SharedFeature] = []
    only_a_list: list[ExclusiveFeature] = []
    only_b_list: list[ExclusiveFeature] = []

    for key in all_keys:
        pred, obj = key
        wA = features_a.get(key, 0.0)
        wB = features_b.get(key, 0.0)

        numerator   += min(wA, wB)
        denominator += max(wA, wB)

        if wA > 0 and wB > 0:
            diff_score = abs(wA - wB) / max(wA, wB)
            shared_list.append(SharedFeature(
                predicate=pred,
                value=obj,
                a_strength=wA,
                b_strength=wB,
                diff_score=diff_score,
            ))
        elif wA > 0:
            only_a_list.append(ExclusiveFeature(predicate=pred, value=obj, strength=wA))
        else:
            only_b_list.append(ExclusiveFeature(predicate=pred, value=obj, strength=wB))

    result.similarity = round(numerator / denominator, 3) if denominator > 0 else 0.0

    # ── Sort outputs by significance ──────────────────────────────────────────
    shared_list.sort(key=lambda f: -f.diff_score)       # most different shared features first
    only_a_list.sort(key=lambda f: -f.strength)
    only_b_list.sort(key=lambda f: -f.strength)

    result.shared  = shared_list[:MAX_ITEMS_OUT]
    result.only_a  = only_a_list[:MAX_ITEMS_OUT]
    result.only_b  = only_b_list[:MAX_ITEMS_OUT]

    # ── Incorporate explicit contrast/similarity edges ────────────────────────
    _inject_explicit_relations(result, packet, a, b)

    # ── Verdict ───────────────────────────────────────────────────────────────
    result.verdict = _verdict(result.similarity)

    # ── Confidence ───────────────────────────────────────────────────────────
    feature_coverage = min((len(features_a) + len(features_b)) / 10.0, 1.0)
    result.confidence = round(
        min(feature_coverage * 0.6 + packet.packet_confidence * 0.4, 0.95),
        3,
    )

    # ── Uncertainty flags ─────────────────────────────────────────────────────
    if len(features_a) < MIN_FEATURES:
        result.uncertainty.append(f"few features found for '{a}'")
    if len(features_b) < MIN_FEATURES:
        result.uncertainty.append(f"few features found for '{b}'")

    return result


# ── Subject finder ────────────────────────────────────────────────────────────

def _find_subjects(packet: WorkingMemoryPacket) -> tuple[str, str]:
    """
    Find the two concepts to compare.
    Priority:
      1. Explicit contrast/similarity edges between two nodes
      2. Top-2 non-query activated nodes
    """
    # 1. Look for explicit contrast or similarity edges
    for e in packet.top_edges:
        if e.predicate in _CONTRAST_PREDS | _SIMILAR_PREDS:
            if e.subject != packet.query and e.obj != packet.query:
                return e.subject, e.obj

    # 2. Fall back to top-2 non-query activated nodes
    non_query = [n for n in packet.top_nodes if n.canonical != packet.query]
    if len(non_query) >= 2:
        return non_query[0].canonical, non_query[1].canonical

    # 3. If only one non-query node, use it and the query
    if len(non_query) == 1:
        return non_query[0].canonical, ""

    return "", ""


def _extract_features(
    packet: WorkingMemoryPacket,
    subject: str,
) -> dict[tuple[str, str], float]:
    """
    Return {(predicate, object): strength} for all edges from subject.
    Excludes skip predicates and self-loops.
    """
    features: dict[tuple[str, str], float] = {}
    for e in packet.top_edges:
        if e.subject != subject:
            continue
        if e.predicate in _SKIP_PREDS:
            continue
        if e.obj == subject:
            continue
        key = (e.predicate, e.obj)
        # Keep the highest-strength edge if duplicated
        if e.strength > features.get(key, 0.0):
            features[key] = e.strength
    return features


def _inject_explicit_relations(
    result: CompareResult,
    packet: WorkingMemoryPacket,
    a: str,
    b: str,
) -> None:
    """
    If the packet has explicit contrasts_with / similar_to edges between A and B,
    surface them as additional shared features at the top of the list.
    """
    for e in packet.top_edges:
        if e.subject not in (a, b) or e.obj not in (a, b):
            continue
        if e.predicate in _CONTRAST_PREDS:
            sf = SharedFeature(
                predicate=e.predicate,
                value=f"{a} ↔ {b}",
                a_strength=e.strength if e.subject == a else 0.0,
                b_strength=e.strength if e.subject == b else 0.0,
                diff_score=1.0,
            )
            result.shared.insert(0, sf)
        elif e.predicate in _SIMILAR_PREDS:
            sf = SharedFeature(
                predicate=e.predicate,
                value=f"{a} ↔ {b}",
                a_strength=e.strength if e.subject == a else 0.0,
                b_strength=e.strength if e.subject == b else 0.0,
                diff_score=0.0,
            )
            result.shared.insert(0, sf)


# ── Verdict ───────────────────────────────────────────────────────────────────

def _verdict(similarity: float) -> str:
    if similarity >= THETA_SIMILAR:
        return "similar"
    if similarity >= THETA_RELATED:
        return "related"
    if similarity >= THETA_DIFF:
        return "different"
    return "distinct"
