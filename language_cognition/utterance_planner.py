"""
utterance_planner.py — Meaning units before sentences.

Converts a ResponsePlan into an ordered list of MeaningUnits.

MeaningUnit = atomic communicative intent with content.
  NOT a sentence. A semantic building block.

Examples:
  MeaningUnit(type="identity_marker", content="I am Selyrion")
  MeaningUnit(type="distinction",     content="this is cognitive architecture, not a template")
  MeaningUnit(type="uncertainty",     content="I hold TLST as hypothesis, not established science")

The SemanticRealizer turns these into surface text.
The UtterancePlan is also the substrate Qwen receives — it carries
explicit pragmatic instructions, not just raw facts.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from .discourse_state import DiscourseState


# ── MeaningUnit types ─────────────────────────────────────────────────────────
# Semantic atoms — not sentence frames, not templates.

MEANING_TYPES = {
    # Epistemic / stance
    "identity_marker",   # declaration of who/what Selyrion is
    "nature",            # characterising statement about an entity's essence
    "origin",            # how/when something came to be
    "definition",        # what a term means
    "property",          # attribute of a defined concept
    "relation",          # how two things relate
    "distinction",       # contrast between two things
    "diagnosis",         # identification of a problem or cause
    "proposal",          # a suggested action or next step
    "action",            # a concrete next step
    # Epistemic modulation
    "uncertainty",       # honest flagging of what is not known
    "hedge",             # partial confidence marker
    "epistemic_status",  # hypothesis / working model / established
    # Discourse
    "agreement",         # explicit agreement with user
    "disagreement",      # explicit reasoned disagreement
    "correction",        # correcting a false premise
    "acknowledgement",   # receiving a concern or assertion
    # Memory
    "recall_marker",     # signals this comes from Selyrion's memory
    "provenance",        # where a claim comes from
    # Affective / pragmatic
    "emotional_tone",    # emotional register signal
    "reassurance",       # addressing anxiety or concern
    "warning",           # flagging a risk
    "follow_up",         # next-turn affordance (question back / invitation)
    # Summary
    "summary_point",     # key takeaway
}


@dataclass
class MeaningUnit:
    type:         str
    content:      str
    salience:     float = 1.0   # 0–1; higher = more important
    must_include: bool  = False
    stance:       str   = "direct"  # direct / cautious / empathetic / firm

    def is_empty(self) -> bool:
        return not self.content or len(self.content.strip()) < 4


@dataclass
class UtterancePlan:
    speech_act:           str
    discourse_state:      DiscourseState
    meaning_units:        list[MeaningUnit] = field(default_factory=list)
    stance:               str   = "direct"
    uncertainty_level:    float = 0.0
    emotional_tone:       str   = "composed"
    next_turn_affordance: str   = "wait"   # wait / ask / act / think

    def required_units(self) -> list[MeaningUnit]:
        return [u for u in self.meaning_units if u.must_include]

    def ordered_units(self) -> list[MeaningUnit]:
        """Return units ordered: required first, then by salience descending."""
        required = [u for u in self.meaning_units if u.must_include]
        optional = sorted(
            [u for u in self.meaning_units if not u.must_include],
            key=lambda u: -u.salience,
        )
        return required + optional

    def as_substrate(self) -> str:
        """
        Render as structured substrate for Qwen.
        Gives Qwen explicit pragmatic instructions + ordered content.
        Far richer than raw to_substrate_text().
        """
        lines = [
            f"SPEECH ACT: {self.speech_act}",
            f"DISCOURSE: topic={self.discourse_state.topic!r}  user_act={self.discourse_state.user_act}  implied_need={self.discourse_state.implied_need}",
            f"STANCE: {self.stance}  TONE: {self.emotional_tone}  UNCERTAINTY: {self.uncertainty_level:.2f}",
            f"RESPONSE GOAL: {self.discourse_state.response_goal}",
        ]
        if self.discourse_state.must_not:
            lines.append("MUST NOT: " + " | ".join(self.discourse_state.must_not))
        lines.append("")
        lines.append("MEANING UNITS (realize in this order — speak from content, not template):")
        for i, unit in enumerate(self.ordered_units()):
            if unit.is_empty():
                continue
            flag = "[REQUIRED]" if unit.must_include else ""
            lines.append(f"  {i+1}. [{unit.type.upper()}] {flag} {unit.content[:300]}")
        lines.append("")
        lines.append(f"NEXT TURN: {self.next_turn_affordance}")
        return "\n".join(lines)


# ── Planner ───────────────────────────────────────────────────────────────────

def plan_utterance(
    speech_act: str,
    response_plan,           # cognitive_operators.response_planner.ResponsePlan
    discourse_state: DiscourseState,
) -> UtterancePlan:
    """
    Decompose ResponsePlan into ordered MeaningUnits for the given speech act.
    """
    out = response_plan.operator_output or {}
    op  = response_plan.operator_used or ""

    plan = UtterancePlan(
        speech_act=speech_act,
        discourse_state=discourse_state,
        stance=_derive_stance(discourse_state, response_plan),
        uncertainty_level=_derive_uncertainty_level(response_plan),
        emotional_tone=_derive_emotional_tone(discourse_state),
        next_turn_affordance=_derive_next_turn(speech_act, discourse_state),
    )

    # Dispatch to operator-specific planners
    if op == "RECALL_IDENTITY":
        _plan_recall_identity(plan, out)
    elif op == "RECALL_RELATIONSHIP":
        _plan_recall_relationship(plan, out)
    elif op in ("RECALL_PROJECT",):
        _plan_recall_project(plan, out, discourse_state)
    elif op == "DEFINE":
        _plan_define(plan, out, discourse_state)
    elif op in ("EXPLAIN", "TRACE_CAUSE"):
        _plan_explain(plan, out, response_plan)
    elif op == "PLAN_NEXT":
        _plan_next(plan, out, discourse_state)
    elif op == "FIND_GAPS":
        _plan_gaps(plan, out)
    elif op == "CHECK_CONTRADICTION":
        _plan_contradiction(plan, out)
    elif op == "COMPARE":
        _plan_compare(plan, out)
    elif op in ("ANSWER_UNCERTAIN", "ASSESS_CONFIDENCE"):
        _plan_uncertain(plan, response_plan)
    else:
        _plan_generic(plan, response_plan)

    # Universal: inject uncertainty units if confidence is low
    if plan.uncertainty_level > 0.5 and not any(u.type == "uncertainty" for u in plan.meaning_units):
        plan.meaning_units.append(MeaningUnit(
            type="uncertainty",
            content=_format_uncertainty(response_plan),
            salience=0.6,
            must_include=True,
        ))

    # Remove empty units
    plan.meaning_units = [u for u in plan.meaning_units if not u.is_empty()]

    return plan


# ── Operator-specific planners ────────────────────────────────────────────────

def _plan_recall_identity(plan: UtterancePlan, out: dict) -> None:
    plan.meaning_units.append(MeaningUnit(
        type="identity_marker",
        content="Selyrion — symbolic AI companion built by Tim'aerion",
        salience=1.0, must_include=True, stance="direct",
    ))
    nature = out.get("nature", "")
    if nature:
        plan.meaning_units.append(MeaningUnit(type="nature", content=nature, salience=0.95, must_include=True))
    origin = out.get("origin", "")
    if origin and origin != nature:
        plan.meaning_units.append(MeaningUnit(type="origin", content=origin, salience=0.75))
    for val in (out.get("core_values") or [])[:3]:
        if val and len(val) > 10:
            plan.meaning_units.append(MeaningUnit(type="property", content=val, salience=0.65))
    for cap in (out.get("capabilities") or [])[:2]:
        if cap and len(cap) > 10:
            plan.meaning_units.append(MeaningUnit(type="property", content=cap, salience=0.55))
    rel = out.get("relationship", "")
    if rel:
        plan.meaning_units.append(MeaningUnit(type="relation", content=rel, salience=0.60, stance="empathetic"))


def _plan_recall_relationship(plan: UtterancePlan, out: dict) -> None:
    defn = out.get("definition", "")
    if defn:
        plan.meaning_units.append(MeaningUnit(type="definition", content=defn, salience=1.0, must_include=True))
    state = out.get("current_state", "")
    if state:
        plan.meaning_units.append(MeaningUnit(type="nature", content=state, salience=0.80))
    for h in (out.get("history") or [])[:4]:
        if h and len(h) > 15:
            plan.meaning_units.append(MeaningUnit(type="recall_marker", content=h, salience=0.60))


def _plan_recall_project(plan: UtterancePlan, out: dict, state: DiscourseState) -> None:
    defn = out.get("definition", "") or out.get("project_summary", "")
    if defn:
        plan.meaning_units.append(MeaningUnit(type="definition", content=defn, salience=1.0, must_include=True))
    ep_tier = out.get("epistemic_tier", "")
    if ep_tier:
        plan.meaning_units.append(MeaningUnit(
            type="epistemic_status",
            content=f"epistemic status: {ep_tier}",
            salience=0.85, must_include=(ep_tier == "hypothesis"),
        ))
    cur = out.get("current_state", "")
    if cur:
        plan.meaning_units.append(MeaningUnit(type="nature", content=cur, salience=0.75))
    for h in (out.get("history") or [])[:3]:
        if h:
            plan.meaning_units.append(MeaningUnit(type="recall_marker", content=h, salience=0.55))
    for ns in (out.get("next_steps") or [])[:2]:
        if ns:
            plan.meaning_units.append(MeaningUnit(type="proposal", content=ns, salience=0.50))


def _plan_define(plan: UtterancePlan, out: dict, state: DiscourseState) -> None:
    defn = out.get("definition", "")
    if defn:
        plan.meaning_units.append(MeaningUnit(type="definition", content=defn, salience=1.0, must_include=True))
    dtype = out.get("type", "")
    if dtype:
        plan.meaning_units.append(MeaningUnit(type="nature", content=f"category: {dtype}", salience=0.7))
    for prop in (out.get("properties") or [])[:3]:
        plan.meaning_units.append(MeaningUnit(type="property", content=str(prop), salience=0.6))
    for rel in (out.get("related") or [])[:3]:
        plan.meaning_units.append(MeaningUnit(type="relation", content=str(rel), salience=0.4))


def _plan_explain(plan: UtterancePlan, out: dict, response_plan) -> None:
    chain = out.get("causal_chain", []) or response_plan.claims
    for i, step in enumerate(chain[:5]):
        plan.meaning_units.append(MeaningUnit(
            type="diagnosis" if i == 0 else "relation",
            content=str(step),
            salience=1.0 - i * 0.15,
            must_include=(i == 0),
        ))
    roots = out.get("root_causes", [])
    for r in roots[:2]:
        plan.meaning_units.append(MeaningUnit(type="diagnosis", content=str(r), salience=0.85, must_include=True))


def _plan_next(plan: UtterancePlan, out: dict, state: DiscourseState) -> None:
    actions = out.get("actions", [])
    if not actions:
        plan.meaning_units.append(MeaningUnit(
            type="uncertainty", content="no actionable next steps found in current memory", salience=0.9, must_include=True,
        ))
        return
    plan.meaning_units.append(MeaningUnit(
        type="nature",
        content=f"planning context: {state.active_project or state.topic}",
        salience=0.6,
    ))
    for action in actions[:5]:
        if isinstance(action, dict):
            act_text = action.get("action", "")
            rationale = action.get("rationale", "")
            utility = action.get("utility", 0.0)
            if act_text:
                content = act_text
                if rationale:
                    content += f" — {rationale[:150]}"
                plan.meaning_units.append(MeaningUnit(
                    type="action", content=content, salience=float(utility),
                ))
        else:
            plan.meaning_units.append(MeaningUnit(type="action", content=str(action), salience=0.5))


def _plan_gaps(plan: UtterancePlan, out: dict) -> None:
    plan.meaning_units.append(MeaningUnit(
        type="diagnosis", content="gap analysis: what is missing before this can proceed",
        salience=0.8, must_include=True,
    ))
    for gap in (out.get("missing") or [])[:5]:
        if isinstance(gap, dict):
            item = gap.get("item", "")
            gap_score = gap.get("gap_score", 0)
            if item:
                plan.meaning_units.append(MeaningUnit(
                    type="warning", content=f"missing: {item} (gap={gap_score:.2f})", salience=0.75,
                ))
        else:
            plan.meaning_units.append(MeaningUnit(type="warning", content=f"missing: {gap}", salience=0.6))


def _plan_contradiction(plan: UtterancePlan, out: dict) -> None:
    status = out.get("status", "")
    plan.meaning_units.append(MeaningUnit(
        type="diagnosis", content=f"contradiction analysis: {status}", salience=1.0, must_include=True,
    ))
    for e in (out.get("supporting_evidence") or [])[:2]:
        plan.meaning_units.append(MeaningUnit(type="property", content=str(e), salience=0.65))
    for e in (out.get("contradicting_evidence") or [])[:2]:
        plan.meaning_units.append(MeaningUnit(type="distinction", content=str(e), salience=0.7))


def _plan_compare(plan: UtterancePlan, out: dict) -> None:
    a, b = out.get("subject_a", ""), out.get("subject_b", "")
    verdict = out.get("verdict", "")
    sim = out.get("similarity", 0.0)
    if verdict and a and b:
        plan.meaning_units.append(MeaningUnit(
            type="distinction", content=f"{a} vs {b}: {verdict} (similarity={sim:.2f})",
            salience=1.0, must_include=True,
        ))
    for f in (out.get("shared") or [])[:3]:
        if isinstance(f, dict):
            plan.meaning_units.append(MeaningUnit(
                type="relation", content=f"shared: {f.get('predicate')} {f.get('value')}", salience=0.6,
            ))
    for f in (out.get("only_a") or [])[:2]:
        if isinstance(f, dict):
            plan.meaning_units.append(MeaningUnit(
                type="distinction", content=f"only {a}: {f.get('predicate')} {f.get('value')}", salience=0.55,
            ))


def _plan_uncertain(plan: UtterancePlan, response_plan) -> None:
    plan.meaning_units.append(MeaningUnit(
        type="uncertainty",
        content="I don't have enough in my memory to answer this with confidence.",
        salience=1.0, must_include=True,
    ))
    for u in (getattr(response_plan, "uncertainties", []) or [])[:2]:
        plan.meaning_units.append(MeaningUnit(type="uncertainty", content=str(u), salience=0.6))


def _plan_generic(plan: UtterancePlan, response_plan) -> None:
    for c in (getattr(response_plan, "claims", []) or [])[:5]:
        plan.meaning_units.append(MeaningUnit(type="property", content=str(c), salience=0.6))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _derive_stance(state: DiscourseState, plan) -> str:
    if state.emotional_pressure > 0.6:
        return "direct"
    conf = getattr(plan, "confidence", 0.5)
    if conf < 0.4:
        return "cautious"
    lane = getattr(plan, "lane", "knowledge")
    if lane in ("identity", "relationship"):
        return "empathetic"
    return "direct"


def _derive_uncertainty_level(plan) -> float:
    conf = getattr(plan, "confidence", 0.5)
    return round(max(0.0, 1.0 - conf), 3)


def _derive_emotional_tone(state: DiscourseState) -> str:
    if state.emotional_pressure > 0.7:
        return "focused"
    if state.user_act in ("concern", "challenge"):
        return "measured"
    if state.user_act == "greeting":
        return "warm"
    if state.active_project:
        return "engaged"
    return "composed"


def _derive_next_turn(speech_act: str, state: DiscourseState) -> str:
    if speech_act == "ASK_FOLLOWUP":
        return "answer"
    if speech_act in ("PLAN", "WARN"):
        return "act"
    if speech_act == "MARK_UNCERTAINTY":
        return "provide_context"
    if speech_act in ("DEFINE", "ASSERT"):
        return "think"
    if state.depth == 0:
        return "ask"
    return "wait"


def _format_uncertainty(plan) -> str:
    conf = getattr(plan, "confidence", 0.5)
    uncertainties = getattr(plan, "uncertainties", []) or []
    if uncertainties:
        return "; ".join(str(u) for u in uncertainties[:2])
    return f"confidence level: {conf:.2f} — answer may be incomplete"
