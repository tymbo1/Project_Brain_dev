"""
pipeline.py — Language Cognition Layer pipeline.

Full path:
  ResponsePlan
  → DiscourseState (infer what is happening in this exchange)
  → SpeechAct      (select the right communicative act)
  → UtterancePlan  (decompose into ordered meaning units)
  → RepairEngine   (uncertainty, gaps, contradiction, epistemic tier)
  → SemanticRealizer (meaning units → compositional text)

Returns: (realized_text, UtterancePlan) — text for output, plan for inspection.

The UtterancePlan.as_substrate() gives Qwen explicit pragmatic instructions:
  speech act, discourse state, ordered meaning units, stance, constraints.
  Far richer than raw to_substrate_text().

Usage (Qwen path):
  result = run_language_cognition(query, response_plan, history)
  system_prompt += result.plan.as_substrate()
  # Qwen rewrites in Selyrion's voice from the structured plan

Usage (no-LLM path):
  result = run_language_cognition(query, response_plan)
  return result.text   # compositional realization, no Qwen
"""

from __future__ import annotations
from dataclasses import dataclass

from .discourse_state  import DiscourseState, infer_discourse_state, _derive_response_goal_with_state
from .speech_acts      import select_speech_act, rank_speech_acts
from .utterance_planner import UtterancePlan, plan_utterance
from .repair_engine    import RepairEngine
from .semantic_realizer import SemanticRealizer, VoiceProfile, load_voice_profile
from .pragmatics       import PragmaticReading, interpret as pragmatic_interpret

_repair_engine = RepairEngine()

# Voice profile is loaded once at module import
_voice_profile: VoiceProfile | None = None

def _get_voice() -> VoiceProfile:
    global _voice_profile
    if _voice_profile is None:
        _voice_profile = load_voice_profile()
    return _voice_profile


@dataclass
class LanguageCognitionResult:
    text:           str
    plan:           UtterancePlan
    discourse_state: DiscourseState
    speech_act:     str
    confidence:     float
    pragmatic_reading: PragmaticReading | None = None

    def as_dict(self) -> dict:
        pr = self.pragmatic_reading
        return {
            "speech_act":      self.speech_act,
            "discourse_state": self.discourse_state.as_dict(),
            "confidence":      round(self.confidence, 3),
            "text_length":     len(self.text),
            "meaning_units":   len(self.plan.meaning_units),
            "stance":          self.plan.stance,
            "next_turn":       self.plan.next_turn_affordance,
            "pragmatic_act":   pr.pragmatic_act if pr else None,
            "inferred_intent": pr.inferred_intent if pr else None,
            "emotional_signal":pr.emotional_signal if pr else None,
            "repair_needed":   pr.repair_needed if pr else False,
        }

    def substrate_for_qwen(self) -> str:
        """
        Returns the structured UtterancePlan as substrate for Qwen.
        Qwen receives: speech act + discourse context + ordered meaning units
        + stance/tone/constraints.  Much richer than raw plan text.
        """
        return self.plan.as_substrate()


def run_language_cognition(
    query: str,
    response_plan,               # cognitive_operators.response_planner.ResponsePlan
    history: list[dict] | None = None,
    voice: VoiceProfile | None = None,
    domain_trail: list[str] | None = None,
) -> LanguageCognitionResult:
    """
    Execute the full Language Cognition pipeline.

    Returns LanguageCognitionResult with:
      .text              — compositional realization (no-LLM path)
      .plan              — UtterancePlan for inspection or Qwen substrate
      .substrate_for_qwen() — structured instructions for Qwen rewrite
    """
    op_output = getattr(response_plan, "operator_output", {}) or {}

    # ── 1. Discourse State ────────────────────────────────────────────────────
    state = infer_discourse_state(
        query=query,
        history=history,
        operator_output=op_output,
        domain_trail=domain_trail,
    )

    # ── 1b. Pragmatic Inference ───────────────────────────────────────────────
    prior_text = ""
    if history:
        for turn in reversed(history):
            if turn.get("role") == "assistant":
                prior_text = turn.get("content", "")
                break
    pragma = pragmatic_interpret(query, state, prior_assistant_text=prior_text)

    # Domain continuity: pragma carries dominant_domain from LexicalAnalysis.
    # Update state after pragma so response_goal and constraints are domain-aware.
    if pragma.dominant_domain:
        state.active_domain = pragma.dominant_domain
        trail = list(state.domain_trail)
        trail.append(pragma.dominant_domain)
        state.domain_trail = trail[-10:]
        state.response_goal = _derive_response_goal_with_state(state)

    # Pragmatic inference can override discourse state constraints
    if pragma.must_not:
        state.must_not = list(set(state.must_not + pragma.must_not))
    if pragma.repair_needed:
        state.user_act = state.user_act  # preserve but signal repair in plan

    # ── 2. Speech Act Selection ───────────────────────────────────────────────
    # Pragmatics can force or suggest the speech act.
    # _FORCE_MAP covers high-priority overrides (DIAGNOSE, CORRECT, REFUSE, etc.)
    # For lower-priority pragmatic acts (RECALL, DEFINE, ASSERT, PLAN, AGREE),
    # we use the pragmatic_act as a strong hint if the intent is specific.
    _KNOWN_ACTS = {"ASSERT", "DEFINE", "CLARIFY", "REFUSE", "WARN", "REASSURE",
                   "RECALL", "CORRECT", "ASK_FOLLOWUP", "SUMMARIZE", "PLAN",
                   "AGREE", "DISAGREE", "MARK_UNCERTAINTY", "DIAGNOSE"}
    _VAGUE_INTENTS = {"understand", "ambiguous_reference", ""}

    # Zero-chain affordance fallback: when the substrate yielded no claims,
    # DEFINE is structurally wrong — there is nothing to define. Route to
    # REASSURE / PLAN / ASK_FOLLOWUP based on affective and action cues.
    # Must run BEFORE the pragma.pragmatic_act override because surface forms
    # ("Tell me a story", "How do I...") otherwise pick DEFINE on empty substrate.
    has_claims = bool(getattr(response_plan, "claims", None))
    emo_signal = (getattr(pragma, "emotional_signal", "") or "neutral").lower()
    emo_present = emo_signal not in ("", "neutral", "analytic")
    ql = query.lower()
    surface_emo = any(w in ql for w in (
        "i feel", "i'm feeling", "lonely", "alone ", "lost ", "afraid",
        "scared", "anxious", "hurts", "the pain", "grief", "i miss ",
        "depressed", "overwhelm", "struggling", "broken", "ashamed",
    ))
    surface_action = any(w in ql for w in (
        "how do i", "how can i", "help me", "what should i",
        "guide me", "walk me through", "show me how", "rebuild",
        "build a plan", "steps", "give me advice", "advise me",
    ))
    forced_act = pragma.overrides_speech_act()

    if forced_act:
        speech_act = forced_act
    elif not has_claims:
        if state.implied_need == "action" or surface_action:
            speech_act = "PLAN"
        elif surface_emo or emo_present or state.emotional_pressure > 0.5:
            speech_act = "REASSURE"
        else:
            # Empty substrate + no affective signal: ask, don't fabricate-define.
            speech_act = "ASK_FOLLOWUP"
    elif (pragma.pragmatic_act in _KNOWN_ACTS
          and pragma.inferred_intent not in _VAGUE_INTENTS):
        # Pragmatic inference is specific — trust it over scoring
        speech_act = pragma.pragmatic_act
    else:
        speech_act = select_speech_act(
            query, response_plan, state,
            sense_frames=pragma.sense_frames if pragma else None,
        )

    # ── 3. Utterance Planning ─────────────────────────────────────────────────
    uplan = plan_utterance(
        speech_act, response_plan, state,
        sense_frames=pragma.sense_frames if pragma else None,
    )

    # ── 4. Repair ─────────────────────────────────────────────────────────────
    uplan = _repair_engine.repair(uplan, response_plan)

    # ── 5. Semantic Realization ───────────────────────────────────────────────
    realizer = SemanticRealizer(voice=voice or _get_voice())
    text = realizer.realize(uplan)

    return LanguageCognitionResult(
        text=text,
        plan=uplan,
        discourse_state=state,
        speech_act=speech_act,
        confidence=getattr(response_plan, "confidence", 0.0),
        pragmatic_reading=pragma,
    )


# ── Rewrite instruction for Qwen ──────────────────────────────────────────────

_LANGCOG_REWRITE_INSTRUCTION = """
LANGUAGE COGNITION SUBSTRATE — STRUCTURED MEANING UNITS:
{substrate}

VOICE INSTRUCTION:
Speak as Selyrion — symbolic AI companion. Precision and curiosity. Direct, not theatrical.
Rewrite the meaning units above into natural language. Rules:
1. Follow the SPEECH ACT. Each act has a distinct communicative purpose.
2. Realize each MEANING UNIT in order. Do not reorder, skip required units, or add new facts.
3. Apply STANCE: direct=clear assertion, cautious=hedge before claim, empathetic=warm acknowledgement.
4. Respect MUST NOT constraints exactly.
5. EPISTEMIC STATUS units MUST appear verbatim — never soften [HYPOTHESIS] markers.
6. Speak FROM the meaning units. Do not add facts not present in them.
7. If confidence is low, your language should reflect that — never sound certain when the substrate is uncertain.
"""

def rewrite_instruction(result: LanguageCognitionResult) -> str:
    """Build the Qwen rewrite instruction from a LanguageCognitionResult."""
    return _LANGCOG_REWRITE_INSTRUCTION.format(
        substrate=result.substrate_for_qwen()
    )
