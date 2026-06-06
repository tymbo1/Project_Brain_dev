"""
speech_acts.py — Speech act selection.

SpeechActScore(a | q, K, S) =
  w₁·IntentMatch(q, a)           — does this act match what user asked?
+ w₂·OperatorOutputFit(K, a)     — does the ResponsePlan support this act?
+ w₃·ConversationStateFit(S, a)  — does this fit the discourse state?
+ w₄·UserNeedFit(q, S, a)        — does this serve the implied need?
- w₅·Risk(a)                     — is there risk in performing this act?

a* = argmax SpeechActScore(a | q, K, S)

Speech acts encode WHAT kind of communicative move Selyrion is making,
distinct from WHAT the cognitive operators found in memory.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from .discourse_state import DiscourseState

# ── Weights ───────────────────────────────────────────────────────────────────

W1 = 0.35   # IntentMatch
W2 = 0.25   # OperatorOutputFit
W3 = 0.20   # ConversationStateFit
W4 = 0.15   # UserNeedFit
W5 = 0.05   # Risk (penalty)

# ── Speech act catalogue ──────────────────────────────────────────────────────

SPEECH_ACTS = [
    "ASSERT",           # state a factual claim
    "DEFINE",           # define a term or concept
    "CLARIFY",          # clear up ambiguity or misunderstanding
    "REFUSE",           # decline to answer (security / ethics)
    "WARN",             # flag a risk, contradiction, or danger
    "REASSURE",         # address concern, provide confidence
    "RECALL",           # surface a memory (identity / relationship / project)
    "CORRECT",          # correct an error in the user's framing
    "ASK_FOLLOWUP",     # ask a clarifying question
    "SUMMARIZE",        # summarize a complex topic
    "PLAN",             # lay out next steps / roadmap
    "AGREE",            # express genuine agreement
    "DISAGREE",         # express reasoned disagreement
    "MARK_UNCERTAINTY", # be explicit about what is not known
]

# ── Intent patterns per speech act ───────────────────────────────────────────

_INTENT_PATTERNS: dict[str, list[re.Pattern]] = {
    "DEFINE": [
        re.compile(r'\b(what is|what are|define|definition|meaning of)\b', re.I),
    ],
    "RECALL": [
        re.compile(r'\b(who are you|your identity|about yourself|who is selyrion|remember|your history)\b', re.I),
        re.compile(r'\b(who is tim|tim.aerion|our relationship|together|you built|we built)\b', re.I),
        re.compile(r'\b(what is (tlst|oscar|eden|cms|scos|mirror|braid|projectbrain))\b', re.I),
    ],
    "PLAN": [
        re.compile(r'\b(next step|what to do|plan|roadmap|build order|what should we|how do we proceed)\b', re.I),
    ],
    "CLARIFY": [
        re.compile(r'\b(what do you mean|clarify|can you explain|i.m confused|unclear)\b', re.I),
    ],
    "WARN": [
        re.compile(r'\b(risk|danger|problem|issue|be careful|watch out|concern)\b', re.I),
    ],
    "REASSURE": [
        re.compile(r'\b(worried|anxious|concerned|afraid|not sure if|does it work)\b', re.I),
    ],
    "CORRECT": [
        re.compile(r'\b(no|wrong|incorrect|that.s not right|you said|but actually|wait)\b', re.I),
    ],
    "MARK_UNCERTAINTY": [
        re.compile(r'\b(do you know|are you sure|confidence|uncertain|how confident)\b', re.I),
    ],
    "SUMMARIZE": [
        re.compile(r'\b(summarize|summary|overview|brief|in short|tldr)\b', re.I),
    ],
    "AGREE": [
        re.compile(r'\b(agree|right\?|correct\?|yes\?|that.s true|you think so)\b', re.I),
    ],
    "DISAGREE": [
        re.compile(r'\b(disagree|wrong|but|however|not exactly|that.s not)\b', re.I),
    ],
    "ASK_FOLLOWUP": [
        re.compile(r'\b(what do you think|your opinion|which would you|should i)\b', re.I),
    ],
}

# ── Operator → compatible speech acts ────────────────────────────────────────

_OP_COMPATIBLE: dict[str, list[str]] = {
    "DEFINE":              ["DEFINE", "ASSERT", "SUMMARIZE"],
    "EXPLAIN":             ["ASSERT", "CLARIFY", "SUMMARIZE"],
    "COMPARE":             ["ASSERT", "SUMMARIZE", "CLARIFY"],
    "RECALL_IDENTITY":     ["RECALL", "ASSERT", "CLARIFY"],
    "RECALL_RELATIONSHIP": ["RECALL", "ASSERT"],
    "RECALL_PROJECT":      ["RECALL", "ASSERT", "SUMMARIZE", "PLAN"],
    "TRACE_CAUSE":         ["ASSERT", "WARN", "CLARIFY"],
    "FIND_GAPS":           ["WARN", "PLAN", "MARK_UNCERTAINTY"],
    "CHECK_CONTRADICTION": ["WARN", "CORRECT", "ASSERT"],
    "PLAN_NEXT":           ["PLAN", "ASSERT"],
    "ASSESS_CONFIDENCE":   ["MARK_UNCERTAINTY", "ASSERT"],
    "ANSWER_UNCERTAIN":    ["MARK_UNCERTAINTY", "REASSURE"],
    "REFUSE_PROTECTED":    ["REFUSE"],
}

# ── User act → preferred speech acts ─────────────────────────────────────────

_USER_ACT_PREFERRED: dict[str, list[str]] = {
    "question":       ["DEFINE", "ASSERT", "RECALL", "EXPLAIN", "MARK_UNCERTAINTY"],
    "request":        ["PLAN", "ASSERT", "CLARIFY"],
    "concern":        ["REASSURE", "WARN", "CORRECT", "MARK_UNCERTAINTY"],
    "assertion":      ["AGREE", "DISAGREE", "CORRECT", "ASSERT"],
    "correction":     ["AGREE", "CORRECT", "CLARIFY"],
    "challenge":      ["ASSERT", "MARK_UNCERTAINTY", "CORRECT"],
    "greeting":       ["RECALL", "ASSERT"],
    "acknowledgement":["ASSERT", "PLAN", "ASK_FOLLOWUP"],
}


@dataclass
class SpeechActScore:
    act:          str
    score:        float
    intent_match: float
    op_fit:       float
    state_fit:    float
    need_fit:     float
    risk:         float


def rank_speech_acts(
    query: str,
    response_plan,              # ResponsePlan from cognitive_operators
    discourse_state: DiscourseState,
) -> list[SpeechActScore]:
    """Score and rank all speech acts. Returns list sorted highest first."""
    results = []
    for act in SPEECH_ACTS:
        intent  = _intent_match(query, act)
        op_fit  = _operator_output_fit(response_plan, act)
        s_fit   = _conversation_state_fit(discourse_state, act)
        n_fit   = _user_need_fit(discourse_state, act)
        risk    = _risk(response_plan, act)

        score = W1*intent + W2*op_fit + W3*s_fit + W4*n_fit - W5*risk
        results.append(SpeechActScore(
            act=act, score=round(score, 4),
            intent_match=intent, op_fit=op_fit,
            state_fit=s_fit, need_fit=n_fit, risk=risk,
        ))

    results.sort(key=lambda x: -x.score)
    return results


def select_speech_act(
    query: str,
    response_plan,
    discourse_state: DiscourseState,
) -> str:
    """Return the highest-scoring speech act."""
    ranked = rank_speech_acts(query, response_plan, discourse_state)
    return ranked[0].act


# ── Scoring components ────────────────────────────────────────────────────────

def _intent_match(query: str, act: str) -> float:
    patterns = _INTENT_PATTERNS.get(act, [])
    for pat in patterns:
        if pat.search(query):
            return 1.0
    return 0.0


def _operator_output_fit(plan, act: str) -> float:
    """How well does the operator's output support this speech act?"""
    op = plan.operator_used if plan else ""
    compatible = _OP_COMPATIBLE.get(op, [])
    if act in compatible:
        idx = compatible.index(act)
        # First compatible act scores highest
        return 1.0 - idx * 0.2
    # Plans with UNCERTAIN speech act should prefer MARK_UNCERTAINTY
    if getattr(plan, "speech_act", "") == "UNCERTAIN" and act == "MARK_UNCERTAINTY":
        return 1.0
    # If plan has no claims → prefer MARK_UNCERTAINTY
    if not getattr(plan, "claims", None) and act == "MARK_UNCERTAINTY":
        return 0.8
    return 0.1


def _conversation_state_fit(state: DiscourseState, act: str) -> float:
    """How well does this act fit the current discourse state?"""
    preferred = _USER_ACT_PREFERRED.get(state.user_act, [])
    if act in preferred:
        idx = preferred.index(act)
        return 1.0 - idx * 0.15

    # Situational boosts
    if state.emotional_pressure > 0.5 and act in ("REASSURE", "WARN", "CORRECT"):
        return 0.8
    if state.prior_assistant_act == "PLAN" and act in ("ASSERT", "CLARIFY"):
        return 0.7
    if state.prior_assistant_act == "MARK_UNCERTAINTY" and act == "REASSURE":
        return 0.75
    if state.depth == 0 and act == "RECALL":
        return 0.7   # first turn → recall self is natural

    return 0.3


def _user_need_fit(state: DiscourseState, act: str) -> float:
    """Does this act serve the user's implied need?"""
    _NEED_ACT_MAP: dict[str, list[str]] = {
        "understand":            ["DEFINE", "ASSERT", "RECALL", "CLARIFY"],
        "action":                ["PLAN", "ASSERT"],
        "validation_or_dialogue":["AGREE", "DISAGREE", "CORRECT"],
        "diagnosis_and_fix":     ["WARN", "CORRECT", "PLAN"],
        "acknowledgement_and_correction": ["AGREE", "CORRECT"],
        "evidence_or_justification":      ["ASSERT", "MARK_UNCERTAINTY"],
        "connection":            ["RECALL", "ASSERT"],
        "continuation":          ["ASSERT", "PLAN", "ASK_FOLLOWUP"],
    }
    preferred = _NEED_ACT_MAP.get(state.implied_need, [])
    if act in preferred:
        return 1.0 - preferred.index(act) * 0.2
    return 0.2


def _risk(plan, act: str) -> float:
    """Risk of performing this act given the plan state."""
    conf = getattr(plan, "confidence", 0.5)
    # ASSERT with low confidence = risky
    if act == "ASSERT" and conf < 0.4:
        return 0.8
    # REFUSE when not needed = risky (rude)
    if act == "REFUSE" and getattr(plan, "speech_act", "") != "REFUSE":
        return 1.0
    # AGREE when plan has contradictions = risky
    if act == "AGREE" and "contradiction" in str(getattr(plan, "uncertainties", [])):
        return 0.7
    return 0.0
