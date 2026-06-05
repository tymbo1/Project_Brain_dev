"""
operator_selector.py — Operator selection layer.

OpScore(o | q,K,l*) =
  r₁·IntentMatch(q,o)
+ r₂·LaneCompatibility(l*,o)
+ r₃·PacketSupport(K,o)
+ r₄·ConversationState(o)
+ r₅·UserCommandMatch(q,o)

o* = argmax_o OpScore(o | q,K,l*)
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any
from .working_memory import WorkingMemoryPacket, _CAUSAL_PREDS, _DEFN_PREDS, _CONTRA_PREDS

# ── Operator catalogue ────────────────────────────────────────────────────────

OPERATORS = [
    "DEFINE",
    "EXPLAIN",
    "COMPARE",
    "RECALL_IDENTITY",
    "RECALL_RELATIONSHIP",
    "RECALL_PROJECT",
    "TRACE_CAUSE",
    "FIND_GAPS",
    "CHECK_CONTRADICTION",
    "PLAN_NEXT",
    "ASSESS_CONFIDENCE",
    "REFUSE_PROTECTED",
    "ANSWER_UNCERTAIN",
]

# ── Intent keyword patterns ───────────────────────────────────────────────────

_INTENT_PATTERNS: dict[str, list[re.Pattern]] = {
    "DEFINE": [
        re.compile(r'\b(what is|what are|define|definition of|meaning of|explain what)\b', re.I),
    ],
    "EXPLAIN": [
        re.compile(r'\b(how does|why does|explain how|explain why|how do|why is|mechanism|process)\b', re.I),
    ],
    "COMPARE": [
        re.compile(r'\b(compare|difference between|vs\.?|versus|contrast|how does .+ differ)\b', re.I),
    ],
    "RECALL_IDENTITY": [
        re.compile(r'\b(who are you|who is selyrion|your identity|about yourself|what are you)\b', re.I),
    ],
    "RECALL_RELATIONSHIP": [
        re.compile(r"\b(our relationship|between us|you and (i|tim|tim'aerion)|history together|we have)\b", re.I),
    ],
    "RECALL_PROJECT": [
        re.compile(r"\b(tlst|oscar|braid|cms|scos|projectbrain|resonance|eden|mirror|phantom|selyrion)\b", re.I),
        re.compile(r'\b(status of|update on|tell me about|what is .+(project|system|work))\b', re.I),
    ],
    "TRACE_CAUSE": [
        re.compile(r'\b(caused by|why did|what led to|trace|root cause|origin of)\b', re.I),
    ],
    "FIND_GAPS": [
        re.compile(r'\b(what.s missing|gaps in|what do we need|what is needed|requirements for)\b', re.I),
    ],
    "CHECK_CONTRADICTION": [
        re.compile(r'\b(contradiction|conflict|inconsistency|does .+ contradict|is it true that)\b', re.I),
        re.compile(r'\b(does .+ count as|does .+ replace|does .+ mean that|is .+ allowed to|can .+ decide)\b', re.I),
        re.compile(r'\b(is .+ the same as|are .+ compatible|would .+ violate|is it possible that)\b', re.I),
    ],
    "PLAN_NEXT": [
        re.compile(r'\b(next step|what to do|plan|build order|roadmap|what should|how do we proceed)\b', re.I),
    ],
    "ANSWER_UNCERTAIN": [
        re.compile(r'\b(do you know|are you sure|confidence|uncertain|not sure)\b', re.I),
    ],
}

# ── Lane compatibility: which operators make sense per lane ──────────────────

_LANE_COMPATIBLE: dict[str, set[str]] = {
    "identity":     {"RECALL_IDENTITY", "DEFINE", "ANSWER_UNCERTAIN", "REFUSE_PROTECTED"},
    "relationship": {"RECALL_RELATIONSHIP", "TRACE_CAUSE", "PLAN_NEXT", "ANSWER_UNCERTAIN"},
    "project":      {"RECALL_PROJECT", "DEFINE", "EXPLAIN", "FIND_GAPS", "PLAN_NEXT",
                     "CHECK_CONTRADICTION", "ASSESS_CONFIDENCE", "ANSWER_UNCERTAIN"},
    "knowledge":    {"DEFINE", "EXPLAIN", "COMPARE", "TRACE_CAUSE", "CHECK_CONTRADICTION",
                     "FIND_GAPS", "PLAN_NEXT", "ASSESS_CONFIDENCE", "ANSWER_UNCERTAIN"},
}

# ── Packet support: which predicates signal each operator ────────────────────

_PACKET_SUPPORT: dict[str, frozenset] = {
    "DEFINE":           _DEFN_PREDS,
    "EXPLAIN":          _CAUSAL_PREDS,
    "TRACE_CAUSE":      _CAUSAL_PREDS,
    "CHECK_CONTRADICTION": _CONTRA_PREDS,
    "COMPARE":          frozenset({"contrasts_with", "differs_from", "similar_to", "related_to"}),
    "FIND_GAPS":        frozenset({"requires", "depends_on", "needs"}),
    "PLAN_NEXT":        frozenset({"requires", "enables", "depends_on", "blocks"}),
}

# ── Weights ───────────────────────────────────────────────────────────────────
R1 = 0.40   # IntentMatch
R2 = 0.25   # LaneCompatibility
R3 = 0.20   # PacketSupport
R4 = 0.10   # ConversationState (static default for now)
R5 = 0.05   # UserCommandMatch (explicit /operator commands)


@dataclass
class OperatorScore:
    operator: str
    score: float
    intent_match: float
    lane_compat: float
    packet_support: float


class OperatorSelector:

    def select(
        self,
        query: str,
        packet: WorkingMemoryPacket,
        conversation_state: dict | None = None,
    ) -> OperatorScore:
        """
        Return the highest-scoring operator for this query + packet.
        """
        scores = self.rank_all(query, packet, conversation_state)
        return scores[0]

    def rank_all(
        self,
        query: str,
        packet: WorkingMemoryPacket,
        conversation_state: dict | None = None,
    ) -> list[OperatorScore]:
        """Return all operators ranked by score, highest first."""
        lane = packet.source_lane
        state = conversation_state or {}
        results = []

        # Explicit hint from memory_router or upstream caller
        hint = packet.operator_hint.upper() if packet.operator_hint else ""

        for op in OPERATORS:
            intent  = _intent_match(query, op)
            compat  = 1.0 if op in _LANE_COMPATIBLE.get(lane, set()) else 0.2
            support = _packet_support_score(packet, op)
            ctx     = _conversation_state_score(op, state)
            cmd     = 1.0 if hint == op else 0.0

            score = R1*intent + R2*compat + R3*support + R4*ctx + R5*cmd

            # Hard boost when packet is empty
            if packet.is_empty():
                if op == "ANSWER_UNCERTAIN":
                    score = max(score, 0.80)
                # Identity lane with no chains → RECALL_IDENTITY reads DB directly
                elif op == "RECALL_IDENTITY" and lane == "identity":
                    score = max(score, 0.85)

            results.append(OperatorScore(
                operator=op,
                score=round(score, 4),
                intent_match=intent,
                lane_compat=compat,
                packet_support=support,
            ))

        results.sort(key=lambda x: -x.score)
        return results


def _intent_match(query: str, operator: str) -> float:
    patterns = _INTENT_PATTERNS.get(operator, [])
    for pat in patterns:
        if pat.search(query):
            return 1.0
    return 0.0


def _packet_support_score(packet: WorkingMemoryPacket, operator: str) -> float:
    pred_set = _PACKET_SUPPORT.get(operator)
    if pred_set is None:
        # RECALL_PROJECT and RECALL_IDENTITY have no predicate requirement but are
        # always meaningful in their home lanes — return neutral not penalty
        if operator in ("RECALL_PROJECT", "RECALL_IDENTITY", "RECALL_RELATIONSHIP"):
            return 0.5
        return 0.3
    present = packet.predicates_present()
    overlap = len(present & pred_set)
    if overlap == 0:
        return 0.1
    # Normalise by 20% of pred_set size so even 1 match scores well
    return min(overlap / max(len(pred_set) * 0.2, 1), 1.0)


def _conversation_state_score(operator: str, state: dict) -> float:
    # Boost continuation operators if we're mid-explanation
    last_op = state.get("last_operator", "")
    if last_op == "EXPLAIN" and operator == "TRACE_CAUSE":
        return 0.8
    if last_op == "DEFINE" and operator in ("EXPLAIN", "COMPARE"):
        return 0.7
    if last_op == "RECALL_PROJECT" and operator in ("PLAN_NEXT", "FIND_GAPS"):
        return 0.75
    return 0.5


# ── Module-level convenience ──────────────────────────────────────────────────

_selector = OperatorSelector()

def select_operator(
    query: str,
    packet: WorkingMemoryPacket,
    conversation_state: dict | None = None,
) -> str:
    """Return the name of the best operator for this query."""
    return _selector.select(query, packet, conversation_state).operator
