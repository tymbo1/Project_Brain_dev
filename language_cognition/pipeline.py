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

from .discourse_state  import DiscourseState, infer_discourse_state
from .speech_acts      import select_speech_act, rank_speech_acts
from .utterance_planner import UtterancePlan, plan_utterance
from .repair_engine    import RepairEngine
from .semantic_realizer import SemanticRealizer, VoiceProfile, load_voice_profile

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

    def as_dict(self) -> dict:
        return {
            "speech_act":     self.speech_act,
            "discourse_state": self.discourse_state.as_dict(),
            "confidence":     round(self.confidence, 3),
            "text_length":    len(self.text),
            "meaning_units":  len(self.plan.meaning_units),
            "stance":         self.plan.stance,
            "next_turn":      self.plan.next_turn_affordance,
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
    )

    # ── 2. Speech Act Selection ───────────────────────────────────────────────
    speech_act = select_speech_act(query, response_plan, state)

    # ── 3. Utterance Planning ─────────────────────────────────────────────────
    uplan = plan_utterance(speech_act, response_plan, state)

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
