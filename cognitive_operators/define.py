"""
define.py — DEFINE operator.

Answers: what is X?

SlotScore(s,x) = A(x) · PredicateWeight(p_s) · Confidence(e) · SourceTrust(e)

Completeness_DEFINE = FilledRequiredSlots / RequiredSlots

Required slots:
  genus/type, core_function, key_properties, important_relations, uncertainty

Output:
{
  "operator": "DEFINE",
  "subject": "...",
  "type": "...",
  "definition": "...",
  "properties": [],
  "related": [],
  "confidence": 0.0,
  "completeness": 0.0,
  "uncertainty": []
}
"""

from __future__ import annotations
from dataclasses import dataclass, field
from .working_memory import WorkingMemoryPacket, MemoryEdge

# ── Predicate weights by slot ─────────────────────────────────────────────────

_SLOT_PREDICATES: dict[str, list[tuple[str, float]]] = {
    "genus":       [("is_a", 1.0), ("type_of", 0.9), ("subtype_of", 0.85),
                    ("instance_of", 0.8), ("classified_as", 0.75)],
    "function":    [("used_for", 1.0), ("enables", 0.9), ("produces", 0.85),
                    ("has_function", 0.9), ("performs", 0.85)],
    "properties":  [("has_property", 1.0), ("characterized_by", 0.9),
                    ("defined_as", 0.95), ("also_known_as", 0.7),
                    ("composed_of", 0.8), ("contains", 0.75)],
    "relations":   [("part_of", 0.9), ("related_to", 0.6), ("associated_with", 0.55),
                    ("contrasts_with", 0.8), ("similar_to", 0.7),
                    ("requires", 0.85), ("depends_on", 0.8)],
    "uncertainty": [("hypothesized_as", 0.9), ("proposed_by", 0.8),
                    ("uncertain_about", 0.9), ("debated_by", 0.7)],
}

_REQUIRED_SLOTS = ["genus", "function", "properties", "relations"]
_ALL_SLOTS      = _REQUIRED_SLOTS + ["uncertainty"]

# Build flat predicate→(slot, weight) lookup
_PRED_TO_SLOT: dict[str, tuple[str, float]] = {}
for _slot, _pairs in _SLOT_PREDICATES.items():
    for _pred, _w in _pairs:
        if _pred not in _PRED_TO_SLOT or _PRED_TO_SLOT[_pred][1] < _w:
            _PRED_TO_SLOT[_pred] = (_slot, _w)


@dataclass
class DefineResult:
    operator: str = "DEFINE"
    subject: str = ""
    type_str: str = ""
    definition: str = ""
    properties: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    confidence: float = 0.0
    completeness: float = 0.0
    slot_scores: dict = field(default_factory=dict)
    raw_evidence: list[MemoryEdge] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "operator":     self.operator,
            "subject":      self.subject,
            "type":         self.type_str,
            "definition":   self.definition,
            "properties":   self.properties,
            "related":      self.related,
            "uncertainty":  self.uncertainty,
            "confidence":   round(self.confidence, 3),
            "completeness": round(self.completeness, 3),
        }

    def is_sufficient(self, min_completeness: float = 0.4) -> bool:
        return self.completeness >= min_completeness and self.confidence > 0.2


def run(packet: WorkingMemoryPacket) -> DefineResult:
    """Execute DEFINE operator over the working memory packet."""
    result = DefineResult(subject=packet.query)

    if packet.is_empty():
        return result

    # Score every edge against slots
    slot_hits: dict[str, list[tuple[float, MemoryEdge]]] = {s: [] for s in _ALL_SLOTS}

    for edge in packet.top_edges:
        slot_info = _PRED_TO_SLOT.get(edge.predicate)
        if not slot_info:
            continue
        slot, pred_weight = slot_info
        # Score by activation of the subject node (highest for seed concept)
        a_subj = packet.node_activation(edge.subject)
        slot_score = a_subj * pred_weight * edge.strength
        slot_hits[slot].append((slot_score, edge))

    # Sort each slot by score
    for slot in slot_hits:
        slot_hits[slot].sort(key=lambda x: -x[0])

    # ── Fill slots ────────────────────────────────────────────────────────────
    result.slot_scores = {s: (slot_hits[s][0][0] if slot_hits[s] else 0.0) for s in _ALL_SLOTS}

    # genus/type
    # Use the top-activated subject as the canonical subject name
    if packet.top_nodes:
        result.subject = packet.top_nodes[0].canonical

    if slot_hits["genus"]:
        best_score, best_edge = slot_hits["genus"][0]
        result.type_str = best_edge.obj
        result.subject = best_edge.subject   # refine to the node that has the definition
        result.raw_evidence.append(best_edge)

    # function
    if slot_hits["function"]:
        funcs = [e.obj for _, e in slot_hits["function"][:3]]
        if funcs:
            result.definition = f"{packet.query} is used for {funcs[0]}"
            if len(funcs) > 1:
                result.definition += f", and {funcs[1]}"
            result.definition += "."
        result.raw_evidence.extend([e for _, e in slot_hits["function"][:3]])

    # properties
    result.properties = [e.obj for _, e in slot_hits["properties"][:5]]
    result.raw_evidence.extend([e for _, e in slot_hits["properties"][:5]])

    # relations
    result.related = [e.obj for _, e in slot_hits["relations"][:5]]

    # uncertainty
    result.uncertainty = [e.obj for _, e in slot_hits["uncertainty"][:3]]

    # ── Completeness ──────────────────────────────────────────────────────────
    filled = sum(1 for s in _REQUIRED_SLOTS if slot_hits[s])
    result.completeness = filled / len(_REQUIRED_SLOTS)

    # ── Confidence = mean of filled slot scores ───────────────────────────────
    filled_scores = [result.slot_scores[s] for s in _REQUIRED_SLOTS if result.slot_scores[s] > 0]
    if filled_scores:
        result.confidence = min(sum(filled_scores) / len(filled_scores), 1.0)
    else:
        result.confidence = packet.packet_confidence * 0.5

    return result
