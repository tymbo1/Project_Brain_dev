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
import re
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
    sense_frames:         dict  = field(default_factory=dict)  # word → [SenseHint]
    expression_hint:      "ExpressionHint | None" = None  # A′ seam #1: capsule-derived stance/cadence features

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
        domain = self.discourse_state.active_domain or self.discourse_state.persistent_domain
        if domain:
            lines.append(f"DOMAIN: {domain}")
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
    sense_frames: dict | None = None,
) -> UtterancePlan:
    """
    Decompose ResponsePlan into ordered MeaningUnits for the given speech act.
    sense_frames: dict[word → list[SenseHint]] from LexicalAnalysis — enriches
    definition/nature units with domain scoping, contrast, and polysemy notices.
    Explicit operator output always takes precedence over lexical enrichment.
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
        sense_frames=sense_frames or {},
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

    # Sense-frame enrichment: domain scoping, contrast, polysemy notice
    # Runs after operator planners so it only supplements, never overrides.
    if sense_frames:
        _enrich_with_sense_frames(plan, sense_frames, discourse_state, response_plan)

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
            pred = f.get("predicate", "").replace("_", " ").strip()
            val  = f.get("value", "").replace("_", " ").strip()
            if pred and val:
                plan.meaning_units.append(MeaningUnit(
                    type="relation", content=f"Both relate to {val} ({pred}).", salience=0.6,
                ))
    for f in (out.get("only_a") or [])[:2]:
        if isinstance(f, dict):
            pred = f.get("predicate", "").replace("_", " ").strip()
            val  = f.get("value", "").replace("_", " ").strip()
            if pred and val and a:
                plan.meaning_units.append(MeaningUnit(
                    type="distinction", content=f"{a} uniquely {pred} {val}.", salience=0.55,
                ))


_RAW_SCHEMA_PATTERNS = (
    "confidence:", "category:", "shared:", "related_to", "↔", "_linnarssonia_",
    "_kutorgina_", "similarity=", "predicate:", "anchor_id:", "no_memory",
)

def _is_raw_schema(s: str) -> bool:
    sl = s.lower()
    return any(p in sl for p in _RAW_SCHEMA_PATTERNS)

def _plan_uncertain(plan: UtterancePlan, response_plan) -> None:
    plan.meaning_units.append(MeaningUnit(
        type="uncertainty",
        content="I don't have enough in my memory to answer this with confidence.",
        salience=1.0, must_include=True,
    ))
    for u in (getattr(response_plan, "uncertainties", []) or [])[:2]:
        s = str(u)
        if not _is_raw_schema(s):
            plan.meaning_units.append(MeaningUnit(type="uncertainty", content=s, salience=0.6))


def _plan_generic(plan: UtterancePlan, response_plan) -> None:
    for c in (getattr(response_plan, "claims", []) or [])[:5]:
        plan.meaning_units.append(MeaningUnit(type="property", content=str(c), salience=0.6))


# ── Sense-frame enrichment ────────────────────────────────────────────────────

# Acts where we must NOT inject lexical enrichment — content is authoritative
_NO_ENRICH_TYPES = frozenset({"identity_marker", "correction", "diagnosis", "uncertainty", "warning"})

# Query phrases that already disambiguate the domain — no clarification needed
_DISAMBIG_PHRASES = re.compile(
    r'\b(in (linguistics|computing|programming|computer science|mathematics|physics|medicine|psychology|philosophy|biology|music|law|finance|economics|chemistry|engineering|ordinary|common|everyday|this context|this project))\b',
    re.I,
)


def _choose_sense_by_domain(hints: list, active_domain: str | None) -> tuple:
    """
    Return (SenseHint, reason_str) for the hint that best matches active_domain.
    reason_str: exact_match / alias_match / substring_match / primary_fallback / no_domain
    Falls back to primary (first) hint only when no domain match exists.
    """
    if not hints:
        return None, "no_hints"
    if not active_domain:
        return hints[0], "no_domain"
    ad = active_domain.lower()
    # Exact match
    for h in hints:
        if h.domain and h.domain.lower() == ad:
            return h, "exact_match"
    # Partial containment ("computer science" ↔ "computing", "economics" ↔ "finance")
    _DOMAIN_ALIASES = {
        "economics": ["finance", "financial", "economic"],
        "computer science": ["computing", "programming", "software"],
        "linguistics": ["language", "phonology", "syntax", "morphology"],
        "medicine": ["medical", "clinical", "pharmacology"],
    }
    for canonical, aliases in _DOMAIN_ALIASES.items():
        if ad == canonical or ad in aliases:
            for h in hints:
                if h.domain and (h.domain.lower() == canonical
                                 or h.domain.lower() in aliases):
                    return h, "alias_match"
    # Substring match
    for h in hints:
        if h.domain and (ad in h.domain.lower() or h.domain.lower() in ad):
            return h, "substring_match"
    return hints[0], "primary_fallback"


# Words that appear in questions but are not the semantic focus
_QUERY_STRUCTURE_WORDS = frozenset({
    "difference", "differences", "meaning", "meanings", "definition", "definitions",
    "kind", "kinds", "type", "types", "sort", "sorts", "way", "ways", "thing", "things",
    "aspect", "aspects", "sense", "senses", "example", "examples", "concept", "concepts",
    "term", "terms", "word", "words", "between", "among", "versus", "compare",
})


def _enrich_with_sense_frames(
    plan: UtterancePlan,
    sense_frames: dict,
    state: DiscourseState,
    response_plan,
) -> None:
    """
    Post-operator enrichment using OEWN sense data.

    Guards:
      - Speech act is RECALL, CORRECT, REFUSE, REASSURE, WARN → skip all enrichment
      - User already disambiguated ("in linguistics" etc.) → skip polysemy follow_up only
        (domain framing and contrast still fire — they're informative, not redundant)

    What this adds:
      1. Domain-scoped definition — if active_domain known, annotate domain-matched sense
      2. Cross-domain contrast — if key term has senses in ≥2 distinct domains
      3. Polysemy follow-up — if high polysemy AND no established domain AND low confidence
    """
    if not sense_frames:
        return
    if plan.speech_act in ("RECALL", "CORRECT", "REFUSE", "REASSURE"):
        return

    query_text = state.topic + " " + state.response_goal
    user_disambiguated = bool(_DISAMBIG_PHRASES.search(query_text))

    confidence = getattr(response_plan, "confidence", 0.5)
    active_domain = state.active_domain or state.persistent_domain

    # Pick the most sense-rich content term as focus, excluding query-structure words
    candidates = {w: h for w, h in sense_frames.items()
                  if w.lower() not in _QUERY_STRUCTURE_WORDS}
    if not candidates:
        candidates = sense_frames
    focus_word, focus_hints = max(
        candidates.items(), key=lambda kv: len(kv[1]), default=(None, [])
    )
    if not focus_word or not focus_hints:
        return

    # ── 1. Domain-scoped definition ───────────────────────────────────────────
    # Use domain-aware sense selection — primary sense may not match active_domain.
    has_definition = any(u.type in ("definition", "nature") for u in plan.meaning_units
                         if u.type not in _NO_ENRICH_TYPES)
    if active_domain and has_definition:
        best, reason = _choose_sense_by_domain(focus_hints, active_domain)
        if best and best.gloss:
            domain_gloss = best.gloss.rstrip(".")
            plan.meaning_units.append(MeaningUnit(
                type="property",
                content=f"In {active_domain}: {domain_gloss}.",
                salience=0.72,
                stance="direct",
            ))
            _emit_sense_trace(
                query=state.topic or "",
                focus_term=focus_word,
                active_domain=active_domain,
                hints=focus_hints,
                chosen=best,
                reason=reason,
                confidence=confidence,
                source_layer="utterance_planner.step1",
            )

    # ── 2. Cross-domain contrast ──────────────────────────────────────────────
    # Add a distinction unit when top senses have different explicit domains.
    # Requires both domains to be non-null to avoid "ordinary vs X" noise.
    if len(focus_hints) >= 2:
        d1 = focus_hints[0].domain
        d2 = next((h.domain for h in focus_hints[1:] if h.domain and h.domain != d1), None)
        if d1 and d2:
            g1 = focus_hints[0].gloss.rstrip(".")[:100]
            g2 = next(h.gloss for h in focus_hints[1:] if h.domain == d2).rstrip(".")[:100]
            plan.meaning_units.append(MeaningUnit(
                type="distinction",
                content=f"'{focus_word}' differs by domain — in {d1}: {g1}; in {d2}: {g2}.",
                salience=0.60,
                stance="direct",
            ))

    # ── 3. Polysemy follow-up ─────────────────────────────────────────────────
    # Offer clarification only when: polysemy is high, no domain is established,
    # confidence is not strong, and query is genuinely ambiguous.
    avg_polysemy = sum(len(v) for v in sense_frames.values()) / len(sense_frames)
    if (avg_polysemy >= 4
            and not active_domain
            and not user_disambiguated
            and confidence < 0.65
            and plan.speech_act in ("DEFINE", "CLARIFY", "ASSERT")
            and not any(u.type == "follow_up" for u in plan.meaning_units)):
        gloss_a = focus_hints[0].gloss[:80].rstrip(".")
        gloss_b = focus_hints[1].gloss[:80].rstrip(".") if len(focus_hints) > 1 else ""
        if gloss_b and gloss_a != gloss_b:
            plan.meaning_units.append(MeaningUnit(
                type="follow_up",
                content=f"Did you mean '{focus_word}' as in '{gloss_a}', or more as '{gloss_b}'?",
                salience=0.50,
                stance="direct",
            ))
        plan.next_turn_affordance = "answer"


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
    uncertainties = getattr(plan, "uncertainties", []) or []
    if uncertainties:
        return "; ".join(str(u) for u in uncertainties[:2])
    return "my memory on this topic may be incomplete"


def _emit_sense_trace(
    query: str,
    focus_term: str,
    active_domain: str | None,
    hints: list,
    chosen,
    reason: str,
    confidence: float,
    source_layer: str,
) -> None:
    """Emit a sense-choice trace to the audit log. No-ops when audit disabled."""
    try:
        from lexical_cognition.sense_audit import (
            write_trace, make_rejected_senses, SenseChoiceTrace, is_enabled
        )
        if not is_enabled():
            return
        trace = SenseChoiceTrace(
            query=query,
            focus_term=focus_term,
            active_domain=active_domain,
            domain_hints=[h.domain for h in hints if h.domain],
            chosen_sense_id=getattr(chosen, "sense_id", "") or "",
            chosen_gloss=getattr(chosen, "gloss", "") or "",
            chosen_domain=getattr(chosen, "domain", None),
            rejected_senses=make_rejected_senses(hints, chosen, reason),
            reason=reason,
            confidence=confidence,
            source_layer=source_layer,
        )
        write_trace(trace)
    except Exception:
        pass  # audit must never crash the pipeline
