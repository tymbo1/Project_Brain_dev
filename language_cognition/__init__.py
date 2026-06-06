"""
language_cognition — Language Cognition Layer v0.1

Sits between ResponsePlan and surface text. Selyrion understands
conversation as a cognitive domain, not a template-matching problem.

Pipeline:
  ResponsePlan
  → DiscourseState   (what is happening in this exchange)
  → SpeechAct        (what kind of communicative act is needed)
  → UtterancePlan    (ordered meaning units — meaning before sentences)
  → RepairEngine     (uncertainty, gaps, contradiction, follow-up)
  → SemanticRealizer (meaning units → compositional language, no fixed templates)
  → final utterance

Key insight: meaning units before sentences.
  {type: "distinction", content: "templates are not language cognition"}
  → NOT: "Templates are not language cognition."  (canned)
  → YES: compositional realization from semantic type + content + stance + context
"""

from .discourse_state import DiscourseState, infer_discourse_state
from .speech_acts import select_speech_act, rank_speech_acts, SPEECH_ACTS
from .utterance_planner import UtterancePlan, MeaningUnit, plan_utterance
from .repair_engine import RepairEngine
from .semantic_realizer import SemanticRealizer, load_voice_profile
from .pragmatics import PragmaticReading, PragmaticRule, PragmaticsEngine, interpret as pragmatic_interpret
from .dialogue_memory import DialogueMemory, DialogueTurn, ActiveInvariant
from .lc_db import init_db as lc_init_db, ensure_db as lc_ensure_db
from .pipeline import run_language_cognition, LanguageCognitionResult, rewrite_instruction

__all__ = [
    "DiscourseState", "infer_discourse_state",
    "select_speech_act", "rank_speech_acts", "SPEECH_ACTS",
    "UtterancePlan", "MeaningUnit", "plan_utterance",
    "RepairEngine",
    "SemanticRealizer", "load_voice_profile",
    "PragmaticReading", "PragmaticRule", "PragmaticsEngine", "pragmatic_interpret",
    "DialogueMemory", "DialogueTurn", "ActiveInvariant",
    "lc_init_db", "lc_ensure_db",
    "run_language_cognition", "LanguageCognitionResult", "rewrite_instruction",
]
