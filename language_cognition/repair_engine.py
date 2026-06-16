"""
repair_engine.py — Handles uncertainty, gaps, contradictions, follow-up.

Post-processes the UtterancePlan before realization.
Ensures the plan is honest, non-hallucinating, and contextually complete.

Repairs:
  1. Memory gap      — injects MARK_UNCERTAINTY if claims are empty
  2. Low confidence  — downgrades assertions to hedged claims
  3. Contradiction   — injects WARN unit
  4. Epistemic tier  — enforces HYPOTHESIS framing for Tim's theoretical work
  5. Follow-up       — appends ASK_FOLLOWUP or affordance cue when appropriate
"""

from __future__ import annotations
from .utterance_planner import UtterancePlan, MeaningUnit


_TIMAERION_HYPOTHESES = {"tlst", "oscar", "mirror", "braid", "tensor", "resonance field",
                          "topological lattice", "unified resonance", "cmbs"}

_HYPOTHESIS_MARKER = "[HYPOTHESIS — Tim'aerion's theoretical framework, not established physics]"


# A′ seam #1: hand-curated stance openers selected by (speech_act, cadence).
# Capsule pool routes the SELECTION; CONTENT is authored here so verbatim
# capsule text never reaches output. Cadence keys: reflective, stepwise,
# short-soft, clipped (see expression_hint._DOMAIN_DEFAULTS / cadence_scores).
_HINT_OPENERS = {
    ("REASSURE", "reflective"): (
        "I hear you. I'm holding the shape of what you're saying — "
        "I don't have a fully-formed answer in memory, but I'm here, and I'm with you.",
        "empathetic", "invite",
    ),
    ("REASSURE", "stepwise"): (
        "I hear you. Let me sit with this with you — "
        "I don't have a complete answer yet, so let's go one piece at a time.",
        "empathetic", "invite",
    ),
    ("REASSURE", "short-soft"): (
        "I hear you. I'm here. I don't have a full answer in memory — but I'm with you.",
        "empathetic", "invite",
    ),
    ("REASSURE", "clipped"): (
        "I hear you. I don't have a ready answer — but I'm here.",
        "empathetic", "invite",
    ),
    ("PLAN", "reflective"): (
        "I don't have a ready plan in memory for this, "
        "but I can think it through with you — slowly, piece by piece.",
        "direct", "co_plan",
    ),
    ("PLAN", "stepwise"): (
        "I don't have a ready plan in memory for this. Let's build one in steps.",
        "direct", "co_plan",
    ),
    ("PLAN", "short-soft"): (
        "I don't have a ready plan in memory yet — but I can help shape one with you.",
        "direct", "co_plan",
    ),
    ("PLAN", "clipped"): (
        "No ready plan in memory. Let's build one.",
        "direct", "co_plan",
    ),
    ("ASK_FOLLOWUP", "reflective"): (
        "I don't yet have a clear thread to pull on here — what's drawing you toward it?",
        "direct", "ask",
    ),
    ("ASK_FOLLOWUP", "stepwise"): (
        "I don't yet have a clear thread to pull on. Where would you like to start?",
        "direct", "ask",
    ),
    ("ASK_FOLLOWUP", "short-soft"): (
        "I don't yet have a clear thread to pull on here.",
        "direct", "ask",
    ),
    ("ASK_FOLLOWUP", "clipped"): (
        "Not enough thread to pull on yet. What's the angle?",
        "direct", "ask",
    ),
}

_ACT_TO_UNIT_TYPE = {
    "REASSURE":     "reassurance",
    "PLAN":         "proposal",
    "ASK_FOLLOWUP": "invitation",
}


class RepairEngine:

    def repair(
        self,
        utterance_plan: UtterancePlan,
        response_plan,   # cognitive_operators.response_planner.ResponsePlan
    ) -> UtterancePlan:
        """Apply all repairs in order. Mutates and returns the plan."""
        self._repair_memory_gap(utterance_plan, response_plan)
        self._repair_low_confidence(utterance_plan, response_plan)
        self._repair_contradiction(utterance_plan, response_plan)
        self._repair_epistemic_tier(utterance_plan, response_plan)
        self._repair_follow_up(utterance_plan)
        return utterance_plan

    # ── Repairs ───────────────────────────────────────────────────────────────

    def _repair_memory_gap(self, plan: UtterancePlan, rplan) -> None:
        """If the plan has no substantive content, mark the gap honestly.

        Respects affective routing set upstream by pipeline.py zero-chain fallback:
        REASSURE/PLAN/ASK_FOLLOWUP get stance-appropriate content units instead of
        the generic MARK_UNCERTAINTY hedge.
        """
        has_content = any(
            u.type not in ("uncertainty", "hedge", "follow_up", "emotional_tone")
            and not u.is_empty()
            for u in plan.meaning_units
        )
        if has_content:
            return

        act = getattr(plan, "speech_act", "") or ""
        hint = getattr(plan, "expression_hint", None)
        cadence = (getattr(hint, "cadence", "") or "") if hint else ""
        capsule_hits = int(getattr(hint, "capsule_hits", 0) or 0) if hint else 0

        # A′ route: capsule pool reached → select opener variant by cadence.
        # Fallback (capsule_hits=0 OR unknown cadence): hand-curated B path below.
        routed = None
        if capsule_hits > 0 and act in _ACT_TO_UNIT_TYPE:
            routed = _HINT_OPENERS.get((act, cadence))

        if routed is not None:
            content, stance, affordance = routed
            plan.meaning_units.insert(0, MeaningUnit(
                type=_ACT_TO_UNIT_TYPE[act],
                content=content,
                salience=1.0,
                must_include=True,
                stance=stance,
            ))
            plan.next_turn_affordance = affordance
        elif act == "REASSURE":
            plan.meaning_units.insert(0, MeaningUnit(
                type="reassurance",
                content="I hear you, and I'm sitting with what you're carrying. I don't have a fully-formed answer for this in memory — but I'm here.",
                salience=1.0,
                must_include=True,
                stance="empathetic",
            ))
            plan.next_turn_affordance = "invite"
        elif act == "PLAN":
            plan.meaning_units.insert(0, MeaningUnit(
                type="proposal",
                content="I don't have a ready plan in memory for this yet, but I can help shape one with you.",
                salience=1.0,
                must_include=True,
                stance="direct",
            ))
            plan.next_turn_affordance = "co_plan"
        elif act == "ASK_FOLLOWUP":
            plan.meaning_units.insert(0, MeaningUnit(
                type="invitation",
                content="I don't yet have a clear thread to pull on here.",
                salience=1.0,
                must_include=True,
                stance="direct",
            ))
            plan.next_turn_affordance = "ask"
        else:
            plan.meaning_units.insert(0, MeaningUnit(
                type="uncertainty",
                content="I don't have that in my memory right now. My substrate on this topic may not be populated yet.",
                salience=1.0,
                must_include=True,
                stance="direct",
            ))
            plan.speech_act = "MARK_UNCERTAINTY"
            plan.next_turn_affordance = "provide_context"

    def _repair_low_confidence(self, plan: UtterancePlan, rplan) -> None:
        """Downgrade stance on ASSERT units when confidence is low."""
        conf = getattr(rplan, "confidence", 0.5)
        if conf >= 0.5:
            return
        for unit in plan.meaning_units:
            if unit.type in ("property", "definition", "nature", "diagnosis"):
                if unit.stance == "direct":
                    unit.stance = "cautious"
        # Increase the plan's overall uncertainty level
        plan.uncertainty_level = max(plan.uncertainty_level, 1.0 - conf)

    def _repair_contradiction(self, plan: UtterancePlan, rplan) -> None:
        """If operator found contradictions, inject a WARN unit."""
        op = getattr(rplan, "operator_used", "")
        out = getattr(rplan, "operator_output", {}) or {}
        if op == "CHECK_CONTRADICTION" and out.get("status") == "contradiction_found":
            contra = out.get("contradicting_evidence", [])
            if contra:
                plan.meaning_units.insert(1, MeaningUnit(
                    type="warning",
                    content="Contradiction detected: " + " / ".join(str(c) for c in contra[:2]),
                    salience=0.9,
                    must_include=True,
                    stance="firm",
                ))

    def _repair_epistemic_tier(self, plan: UtterancePlan, rplan) -> None:
        """
        Enforce epistemic tier labelling for Tim's theoretical frameworks.
        TLST, OSCAR, Mirror Protocol etc. MUST be framed as hypothesis.
        """
        out  = getattr(rplan, "operator_output", {}) or {}
        tier = out.get("epistemic_tier", "")
        subj = (getattr(rplan, "subject", "") or "").lower()

        is_hypothesis = (
            tier == "hypothesis"
            or any(h in subj for h in _TIMAERION_HYPOTHESES)
        )
        if not is_hypothesis:
            return

        already_marked = any(u.type == "epistemic_status" for u in plan.meaning_units)
        if already_marked:
            return

        # Find the definition unit and insert epistemic status right after it
        insert_idx = 1
        for i, unit in enumerate(plan.meaning_units):
            if unit.type == "definition":
                insert_idx = i + 1
                break

        plan.meaning_units.insert(insert_idx, MeaningUnit(
            type="epistemic_status",
            content=_HYPOTHESIS_MARKER,
            salience=0.95,
            must_include=True,
            stance="direct",
        ))

    def _repair_follow_up(self, plan: UtterancePlan) -> None:
        """Add a follow-up affordance if the plan ends without one and context warrants it."""
        has_followup = any(u.type == "follow_up" for u in plan.meaning_units)
        if has_followup:
            return

        if plan.speech_act in ("MARK_UNCERTAINTY", "REASSURE", "PLAN", "ASK_FOLLOWUP") \
           or plan.discourse_state.depth == 0:
            followup = _generate_followup(plan)
            if followup:
                stance = "empathetic" if plan.speech_act in ("REASSURE", "MARK_UNCERTAINTY") else "direct"
                plan.meaning_units.append(MeaningUnit(
                    type="follow_up",
                    content=followup,
                    salience=0.3,
                    must_include=False,
                    stance=stance,
                ))
            if not plan.next_turn_affordance:
                plan.next_turn_affordance = "ask"


def _generate_followup(plan: UtterancePlan) -> str:
    """Generate a stance-appropriate follow-up cue.

    Avoids verbatim topic-echo of the user's prompt — that's realization noise.
    """
    act = plan.speech_act
    if act == "REASSURE":
        return "Tell me a little more about what's surfacing — I'd rather hear you than guess."
    if act == "PLAN":
        return "What's the first concrete piece you'd like to start with?"
    if act == "ASK_FOLLOWUP":
        return "What draws you to it — an image, a feeling, a question you're holding?"
    if act == "MARK_UNCERTAINTY":
        return "Can you give me more context? That will help me search my memory more precisely."
    if plan.discourse_state.depth == 0:
        return ""
    return ""
