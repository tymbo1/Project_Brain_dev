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
        """If the plan has no substantive content, mark the gap honestly."""
        has_content = any(
            u.type not in ("uncertainty", "hedge", "follow_up", "emotional_tone")
            and not u.is_empty()
            for u in plan.meaning_units
        )
        if not has_content:
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

        # Add follow-up for uncertain answers or first turn
        if plan.speech_act in ("MARK_UNCERTAINTY",) or plan.discourse_state.depth == 0:
            followup = _generate_followup(plan)
            if followup:
                plan.meaning_units.append(MeaningUnit(
                    type="follow_up",
                    content=followup,
                    salience=0.3,
                    must_include=False,
                    stance="empathetic",
                ))
            plan.next_turn_affordance = "ask"


def _generate_followup(plan: UtterancePlan) -> str:
    """Generate a contextually appropriate follow-up cue."""
    topic = plan.discourse_state.topic
    if plan.speech_act == "MARK_UNCERTAINTY":
        if topic:
            return f"Is there something specific about {topic} you'd like to explore together?"
        return "Can you give me more context? That will help me search my memory more precisely."
    if plan.discourse_state.depth == 0:
        return ""  # first turn — don't pepper with questions
    return ""
