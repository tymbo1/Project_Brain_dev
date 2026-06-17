"""
discourse_state.py — DiscourseState: what is happening in this exchange.

Tracks:
  topic               — main concept being discussed
  user_act            — what the user is doing (question/concern/assertion/...)
  implied_need        — what they actually need, beyond the literal words
  prior_assistant_act — what Selyrion did last turn
  depth               — conversation depth (turn count)
  active_project      — which project is in focus
  emotional_pressure  — urgency/frustration signal (0=neutral, 1=high)
  user_knowledge_level— novice / familiar / expert
  response_goal       — what this response should accomplish
  must_not            — constraints: what this response must not do
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field


# ── User act taxonomy ─────────────────────────────────────────────────────────

_USER_ACT_PATTERNS: dict[str, list[re.Pattern]] = {
    "question":     [re.compile(r'\b(what|who|where|when|why|how|which|does|is|are|can|will|should|did)\b', re.I),
                     re.compile(r'\?$')],
    "request":      [re.compile(r'\b(build|create|write|implement|add|make|generate|give me|show me|can you)\b', re.I)],
    "assertion":    [re.compile(r'\b(i think|i believe|i know|it is|this is|that is|we should|we need)\b', re.I)],
    "concern":      [re.compile(r'\b(problem|issue|bug|broken|wrong|failing|not working|garbage|weird|confused)\b', re.I)],
    "correction":   [re.compile(r'\b(no|wrong|incorrect|that.s not|you said|but|actually|wait)\b', re.I)],
    "challenge":    [re.compile(r'\b(prove|why should|how do you know|are you sure|really|seriously)\b', re.I)],
    "greeting":     [re.compile(r'^(hi|hey|hello|yo|sup|morning|evening)\b', re.I)],
    "acknowledgement": [re.compile(r'^(ok|yes|got it|understood|makes sense|sure|right|agreed|exactly)\b', re.I)],
}

# ── Emotional pressure signals ────────────────────────────────────────────────

_HIGH_PRESSURE = {"urgent", "broken", "garbage", "terrible", "wrong", "failed", "bad",
                  "useless", "nonsense", "wtf", "seriously", "still", "again", "fix this"}

# ── Project keywords ──────────────────────────────────────────────────────────

_PROJECT_SIGNALS: dict[str, set[str]] = {
    "tlst":     {"tlst", "braid", "topology", "string theory"},
    "oscar":    {"oscar", "resonance"},
    "eden":     {"eden", "entailment", "deterministic"},
    "cms":      {"cms", "capsule", "anchor", "memory substrate"},
    "scos":     {"scos", "cognitive operating system"},
    "chess":    {"chess", "stockfish", "lichess", "position", "move"},
    "langeng":  {"langeng", "language engine", "expression", "articulation"},
    "projectbrain": {"projectbrain", "project brain"},
}

# ── Implied need inference ─────────────────────────────────────────────────────

_NEED_MAP: dict[str, str] = {
    "question":       "understand",
    "request":        "action",
    "assertion":      "validation_or_dialogue",
    "concern":        "diagnosis_and_fix",
    "correction":     "acknowledgement_and_correction",
    "challenge":      "evidence_or_justification",
    "greeting":       "connection",
    "acknowledgement": "continuation",
}

# ── Knowledge level signals ───────────────────────────────────────────────────

_EXPERT_SIGNALS = {"operator", "pipeline", "substrate", "activation", "inference engine",
                   "ssre", "hitl", "langeng", "discourse", "speech act", "utterance"}
_NOVICE_SIGNALS = {"explain", "what is", "i don't understand", "confused", "simple", "basics"}


@dataclass
class DiscourseState:
    topic:               str        = ""
    user_act:            str        = "question"
    implied_need:        str        = "understand"
    prior_assistant_act: str        = ""
    prior_topic:         str        = ""
    depth:               int        = 0
    active_project:      str        = ""
    emotional_pressure:  float      = 0.0
    user_knowledge_level:str        = "familiar"
    response_goal:       str        = ""
    must_not:            list       = field(default_factory=list)
    active_domain:       str | None = None   # dominant semantic domain this turn
    domain_trail:        list[str]  = field(default_factory=list)  # domains seen across turns (newest last)

    @property
    def persistent_domain(self) -> str | None:
        """Domain that has appeared in ≥2 of the last 3 turns — indicates sustained topic."""
        if len(self.domain_trail) < 2:
            return None
        recent = self.domain_trail[-3:]
        for d in set(recent):
            if recent.count(d) >= 2:
                return d
        return None

    def as_dict(self) -> dict:
        return {
            "topic":               self.topic,
            "user_act":            self.user_act,
            "implied_need":        self.implied_need,
            "prior_act":           self.prior_assistant_act,
            "depth":               self.depth,
            "active_project":      self.active_project,
            "emotional_pressure":  round(self.emotional_pressure, 2),
            "user_knowledge":      self.user_knowledge_level,
            "response_goal":       self.response_goal,
            "must_not":            self.must_not,
            "active_domain":       self.active_domain,
            "persistent_domain":   self.persistent_domain,
        }


def infer_discourse_state(
    query: str,
    history: list[dict] | None = None,
    operator_output: dict | None = None,
    dominant_domain: str | None = None,
    domain_trail: list[str] | None = None,
) -> DiscourseState:
    """
    Infer DiscourseState from the current query + conversation history.

    history format: [{"role": "user"|"assistant", "content": "..."}]
    operator_output: the .operator_output dict from ResponsePlan (optional)
    """
    state = DiscourseState()
    q = query.strip()
    q_lower = q.lower()

    # ── Topic extraction ──────────────────────────────────────────────────────
    # Use operator subject if available, else extract from query
    if operator_output:
        state.topic = operator_output.get("subject", "")
    if not state.topic:
        state.topic = _extract_topic(q)

    # ── User act ──────────────────────────────────────────────────────────────
    state.user_act = _classify_user_act(q_lower)
    state.implied_need = _NEED_MAP.get(state.user_act, "understand")

    # ── Depth + prior context ─────────────────────────────────────────────────
    if history:
        state.depth = sum(1 for m in history if m.get("role") == "user")
        prior_msgs = [m for m in reversed(history) if m.get("role") == "assistant"]
        if prior_msgs:
            prior_content = prior_msgs[0].get("content", "")
            state.prior_assistant_act = _infer_act_from_text(prior_content)
            state.prior_topic = _extract_topic(prior_content)

    # ── Active project ────────────────────────────────────────────────────────
    state.active_project = _detect_project(q_lower)

    # ── Emotional pressure ────────────────────────────────────────────────────
    words = set(q_lower.split())
    pressure_hits = words & _HIGH_PRESSURE
    state.emotional_pressure = min(len(pressure_hits) * 0.3, 1.0)

    # ── User knowledge level ──────────────────────────────────────────────────
    if any(s in q_lower for s in _EXPERT_SIGNALS):
        state.user_knowledge_level = "expert"
    elif any(s in q_lower for s in _NOVICE_SIGNALS):
        state.user_knowledge_level = "novice"
    else:
        state.user_knowledge_level = "familiar"

    # ── Domain continuity (before response_goal so goal is domain-aware) ────────
    state.active_domain = dominant_domain
    trail = list(domain_trail) if domain_trail else []
    if dominant_domain:
        trail.append(dominant_domain)
    state.domain_trail = trail[-10:]   # keep last 10 turns max

    # ── Response goal ─────────────────────────────────────────────────────────
    state.response_goal = _derive_response_goal(state)

    # ── Must-not constraints ──────────────────────────────────────────────────
    state.must_not = _derive_constraints(state, operator_output)

    return state


# ── Helpers ───────────────────────────────────────────────────────────────────

def _classify_user_act(q_lower: str) -> str:
    for act, patterns in _USER_ACT_PATTERNS.items():
        for pat in patterns:
            if pat.search(q_lower):
                return act
    return "question"


def _extract_topic(text: str) -> str:
    """Extract the main concept being discussed from text."""
    text = text.strip()
    # "What is X?" / "Who is X?" → X
    m = re.search(r'\b(?:what|who) (?:is|are) ([A-Za-z0-9\'\-\. ]{2,40})\??', text, re.I)
    if m:
        return m.group(1).strip().rstrip("?").strip()
    # "Tell me about X" / "Explain X"
    m = re.search(r'\b(?:about|explain|describe|define) ([A-Za-z0-9\'\-\. ]{2,40})', text, re.I)
    if m:
        return m.group(1).strip()
    # Fall back to first noun phrase (simple heuristic: capitalised word or first word)
    words = [w.strip("?.,!") for w in text.split() if len(w) > 3]
    return words[0] if words else text[:40]


def _detect_project(q_lower: str) -> str:
    for project, signals in _PROJECT_SIGNALS.items():
        if any(s in q_lower for s in signals):
            return project
    return ""


def _infer_act_from_text(text: str) -> str:
    """Rough inference of what speech act a prior assistant turn performed."""
    t = text.lower()
    if any(w in t for w in ("plan", "next step", "build", "roadmap")):
        return "PLAN"
    if any(w in t for w in ("uncertain", "don't know", "not sure", "no memory")):
        return "MARK_UNCERTAINTY"
    if any(w in t for w in ("is a", "refers to", "defined as")):
        return "DEFINE"
    if any(w in t for w in ("i am", "selyrion", "my nature", "my origin")):
        return "RECALL"
    return "ASSERT"


def _derive_response_goal_with_state(state: DiscourseState) -> str:
    return _derive_response_goal(state)


def _derive_response_goal(state: DiscourseState) -> str:
    domain_ctx = f" in {state.persistent_domain} domain" if state.persistent_domain else ""
    if state.emotional_pressure > 0.5:
        return "diagnose and address the concern directly"
    if state.user_act == "question" and state.implied_need == "understand":
        return f"explain {state.topic}{domain_ctx} at {state.user_knowledge_level} depth"
    if state.user_act == "request":
        return f"fulfill the request for {state.topic}{domain_ctx}"
    if state.user_act == "concern":
        return "identify the problem and propose a fix"
    if state.user_act == "correction":
        return "acknowledge and correct"
    if state.user_act == "assertion":
        return "engage with the claim — agree, extend, or challenge"
    if state.user_act == "challenge":
        return "provide evidence or acknowledge the limit honestly"
    return f"respond to {state.user_act} about {state.topic}{domain_ctx}"


def _derive_constraints(state: DiscourseState, operator_output: dict | None) -> list[str]:
    constraints = []
    if state.user_act == "concern":
        constraints.append("do not minimise the problem")
    if state.user_knowledge_level == "expert":
        constraints.append("do not over-explain basics")
    if state.user_knowledge_level == "novice":
        constraints.append("avoid jargon without explanation")
    if operator_output and not operator_output.get("definition"):
        constraints.append("do not fabricate facts not in memory")
    if state.emotional_pressure > 0.6:
        constraints.append("be direct — no decorative language")
    return constraints
