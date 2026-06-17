"""
dialogue_memory.py — Short-term conversational memory.

Tracks the live conversation state turn-by-turn:
  - What the user last claimed
  - What corrections the user has issued
  - Active invariants (things that must remain true this conversation)
  - Open questions (things asked but not answered)
  - Repair history (what has been repaired and how)

This is NOT long-term memory (that lives in CMS / selyrionstory.db).
This is the working scratchpad of the current exchange.

Invariants once established persist for the whole conversation.
Corrections override prior model — the new model becomes the invariant.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

try:
    from language_cognition.dialogue_focus import FocusState, update_focus_from_lc
except Exception:
    FocusState = None  # type: ignore
    update_focus_from_lc = None  # type: ignore


@dataclass
class DialogueTurn:
    turn_number:   int
    role:          str          # "user" or "assistant"
    text:          str
    speech_act:    str = ""     # what act was used (ASSERT, RECALL, etc.)
    pragmatic_act: str = ""     # what pragmatic function was inferred
    inferred_intent: str = ""
    repair_needed: bool = False
    emotional_signal: str = "neutral"
    domain:        str | None = None  # dominant semantic domain this turn


@dataclass
class ActiveInvariant:
    """
    A fact established in this conversation that MUST remain true.
    Typically set by user corrections or explicit statements.
    """
    body:         str
    established_at: int    # turn number
    source:       str = "user_correction"   # user_correction / assistant_assertion


@dataclass
class OpenQuestion:
    text:         str
    asked_at:     int
    asked_by:     str = "assistant"
    answered:     bool = False
    answer_turn:  Optional[int] = None


class DialogueMemory:
    """
    Maintains the cognitive state of the current conversation.

    Key properties:
      turns              — ordered list of all turns
      active_invariants  — facts established this conversation
      user_corrections   — corrections the user has issued
      open_questions     — questions left unanswered
      repair_history     — what was repaired and how
      depth              — how many exchanges have occurred
    """

    def __init__(self):
        self.turns:             list[DialogueTurn]    = []
        self.active_invariants: list[ActiveInvariant] = []
        self.user_corrections:  list[str]             = []
        self.open_questions:    list[OpenQuestion]    = []
        self.repair_history:    list[dict]            = []
        self._last_user_claim:  str                   = ""
        self._last_assistant_text: str                = ""
        self.focus_state = FocusState() if FocusState is not None else None

    def update_focus(self, lc_result, response_plan, turn_number: int = 0, query: str = "") -> None:
        """Update focus state after a LangCog result."""
        if self.focus_state is not None and update_focus_from_lc is not None:
            try:
                update_focus_from_lc(self.focus_state, lc_result, response_plan, turn_number, query=query)
            except Exception:
                pass

    # ── Turn management ───────────────────────────────────────────────────────

    def add_turn(self, turn: DialogueTurn) -> None:
        self.turns.append(turn)
        if turn.role == "user":
            self._last_user_claim = turn.text
            if turn.repair_needed:
                self._flag_repair_needed(turn)
        elif turn.role == "assistant":
            self._last_assistant_text = turn.text

    def record_user_turn(
        self,
        text: str,
        speech_act: str = "",
        pragmatic_act: str = "",
        inferred_intent: str = "",
        repair_needed: bool = False,
        emotional_signal: str = "neutral",
        domain: str | None = None,
    ) -> DialogueTurn:
        t = DialogueTurn(
            turn_number=len(self.turns),
            role="user",
            text=text,
            speech_act=speech_act,
            pragmatic_act=pragmatic_act,
            inferred_intent=inferred_intent,
            repair_needed=repair_needed,
            emotional_signal=emotional_signal,
            domain=domain,
        )
        self.add_turn(t)
        return t

    def record_assistant_turn(
        self,
        text: str,
        speech_act: str = "",
    ) -> DialogueTurn:
        t = DialogueTurn(
            turn_number=len(self.turns),
            role="assistant",
            text=text,
            speech_act=speech_act,
        )
        self.add_turn(t)
        return t

    # ── Invariant management ──────────────────────────────────────────────────

    def add_invariant(self, body: str, source: str = "user_correction") -> None:
        # Don't duplicate
        existing = {inv.body for inv in self.active_invariants}
        if body not in existing:
            self.active_invariants.append(ActiveInvariant(
                body=body,
                established_at=len(self.turns),
                source=source,
            ))

    def add_correction(self, correction: str) -> None:
        """User has corrected the model. Add as invariant and track separately."""
        if correction not in self.user_corrections:
            self.user_corrections.append(correction)
        self.add_invariant(correction, source="user_correction")

    def get_invariants_text(self) -> str:
        if not self.active_invariants:
            return ""
        lines = [f"- {inv.body}" for inv in self.active_invariants]
        return "Active conversation invariants:\n" + "\n".join(lines)

    # ── Open questions ────────────────────────────────────────────────────────

    def add_open_question(self, text: str, asked_by: str = "assistant") -> None:
        self.open_questions.append(OpenQuestion(
            text=text,
            asked_at=len(self.turns),
            asked_by=asked_by,
        ))

    def mark_question_answered(self, idx: int, at_turn: int) -> None:
        if 0 <= idx < len(self.open_questions):
            self.open_questions[idx].answered = True
            self.open_questions[idx].answer_turn = at_turn

    def unanswered_questions(self) -> list[OpenQuestion]:
        return [q for q in self.open_questions if not q.answered]

    # ── Repair tracking ───────────────────────────────────────────────────────

    def record_repair(self, turn: int, what: str, how: str) -> None:
        self.repair_history.append({
            "turn": turn,
            "what": what,
            "how":  how,
        })

    def _flag_repair_needed(self, turn: DialogueTurn) -> None:
        # Extract the correction text from the user's message as an invariant
        text = turn.text.strip()
        if len(text) > 10:
            self.add_correction(text)

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def depth(self) -> int:
        return len([t for t in self.turns if t.role == "user"])

    @property
    def last_user_claim(self) -> str:
        return self._last_user_claim

    @property
    def last_assistant_text(self) -> str:
        return self._last_assistant_text

    @property
    def has_active_repairs(self) -> bool:
        return bool(self.user_corrections)

    @property
    def domain_trail(self) -> list[str]:
        """Ordered list of non-null domains from user turns, capped at last 10."""
        trail = [t.domain for t in self.turns if t.role == "user" and t.domain]
        return trail[-10:]

    def as_history(self) -> list[dict]:
        """Return turn list as standard history format for LangCog pipeline."""
        return [{"role": t.role, "content": t.text} for t in self.turns]

    def summary(self) -> dict:
        return {
            "depth":        self.depth,
            "invariants":   len(self.active_invariants),
            "corrections":  len(self.user_corrections),
            "open_questions": len(self.unanswered_questions()),
            "repairs":      len(self.repair_history),
        }
