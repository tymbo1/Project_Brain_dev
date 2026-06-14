"""
dialogue_focus.py — Ellipsis / anaphora focus resolver.

Tracks what the conversation is ABOUT across turns.
Resolves shorthand follow-ups before routing:

  "And in computing?"       → "What is field in computing?"
  "And in a database?"      → "What is field in a database?"
  "What about programming?" → "What is syntax in programming?"
  "The other kind?"         → selects alternate sense from focus history

Hard rule: only resolve when confidence is high (focus_term known + domain detectable).
Never guess. Return was_resolved=False and pass through original when uncertain.
"""

from __future__ import annotations
import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path


# ── Focus state ───────────────────────────────────────────────────────────────

@dataclass
class FocusEntry:
    turn:        int
    focus_term:  str
    domain:      str | None
    sense_id:    str | None
    gloss:       str | None


@dataclass
class FocusState:
    """The current 'topic under discussion' — tracked from LangCog results."""
    current_focus_term:    str | None = None
    current_focus_domain:  str | None = None
    current_focus_sense_id: str | None = None
    current_focus_gloss:   str | None = None
    last_domain:           str | None = None   # domain from last resolved turn
    last_operator:         str | None = None   # cognitive operator used last turn
    last_response_subject: str | None = None   # subject field from last ResponsePlan
    focus_history:         list[FocusEntry] = field(default_factory=list)

    def push(self, entry: FocusEntry) -> None:
        self.focus_history.append(entry)
        if len(self.focus_history) > 20:
            self.focus_history = self.focus_history[-20:]

    def last_entries(self, n: int = 3) -> list[FocusEntry]:
        return self.focus_history[-n:]

    def alternate_sense_gloss(self) -> str | None:
        """
        Return the gloss of the most recently rejected top sense.
        Used for "the other meaning?" / "the other kind?" resolution.
        """
        try:
            from lexical_cognition.sense_audit import read_traces
            if self.current_focus_term:
                traces = read_traces(limit=5, focus_term=self.current_focus_term)
                for t in traces:
                    rejected = t.get("rejected", [])
                    if rejected:
                        return rejected[0].get("gloss", "")
        except Exception:
            pass
        return None


# ── Resolved query ────────────────────────────────────────────────────────────

@dataclass
class ResolvedQuery:
    original_query:  str
    resolved_query:  str
    focus_term:      str | None
    target_domain:   str | None
    confidence:      float         # 0–1
    reason:          str           # ellipsis type: domain_shift / what_about / other_kind / ordinal / unresolved
    was_resolved:    bool


# ── Ellipsis detection ────────────────────────────────────────────────────────

# "And in computing?" / "In computing?" / "What about in computing?"
_DOMAIN_SHIFT_RE = re.compile(
    r'^(?:and\s+)?(?:what\s+)?(?:about\s+)?in\s+(?:a\s+|an\s+|the\s+)?(.+?)[\?.]?\s*$',
    re.IGNORECASE,
)

# "What about X?" / "And what about X?" — X may be domain or new term
_WHAT_ABOUT_RE = re.compile(
    r'^(?:and\s+)?what\s+about\s+(?:a\s+|an\s+|the\s+)?(.+?)[\?.]?\s*$',
    re.IGNORECASE,
)

# "And X?" where X looks like a domain word (short, single word)
_AND_DOMAIN_RE = re.compile(
    r'^and\s+(?:in\s+)?([a-z]{3,25})\s*[\?.]?\s*$',
    re.IGNORECASE,
)

# "The other kind/meaning/sense?" / "The other one?" / "The other meaning of X?"
_OTHER_KIND_RE = re.compile(
    r'^(?:the\s+|and\s+the\s+)?(?:other|second|alternate|alternative)\s+'
    r'(?:kind|meaning|sense|type|one|interpretation|definition)(?:\s+of\s+\w+)?\s*[\?.]?\s*$',
    re.IGNORECASE,
)

# "The first meaning?" / "The second sense?"
_ORDINAL_RE = re.compile(
    r'^(?:the\s+)?(?:first|second|third|1st|2nd|3rd)\s+'
    r'(?:meaning|sense|definition|one)\s*[\?.]?\s*$',
    re.IGNORECASE,
)

# "How does that relate?" / "And how does this compare?"
_RELATE_RE = re.compile(
    r'^(?:and\s+)?how\s+does\s+(?:that|this|it)\s+(?:relate|compare|differ|connect)\b',
    re.IGNORECASE,
)

# "And the X meaning?" / "And the physics one?"
_AND_THE_RE = re.compile(
    r'^and\s+the\s+([a-z]{3,25})\s*(?:one|meaning|sense)?\s*[\?.]?\s*$',
    re.IGNORECASE,
)

# ── Domain phrase map (canonical → canonical) ─────────────────────────────────
# Same aliases as _extract_explicit_domain in pragmatics.py

_KNOWN_DOMAINS = {
    "computing", "programming", "computer science", "database", "databases",
    "sql", "software", "hardware", "physics", "chemistry", "biology",
    "linguistics", "mathematics", "math", "logic", "philosophy", "psychology",
    "medicine", "medical", "music", "law", "legal", "finance", "financial",
    "economics", "engineering", "ordinary", "everyday", "common speech",
}

_DOMAIN_CANONICAL: dict[str, str] = {
    "computing":        "computer science",
    "programming":      "computer science",
    "database":         "computer science",
    "databases":        "computer science",
    "sql":              "computer science",
    "software":         "computer science",
    "hardware":         "computer science",
    "finance":          "economics",
    "financial":        "economics",
    "math":             "mathematics",
    "logic":            "mathematics",
    "medical":          "medicine",
    "legal":            "law",
    "linguistic":       "linguistics",
    "biological":       "biology",
    "ordinary":         "ordinary",
    "everyday":         "ordinary",
}


def _canonicalize_domain(phrase: str) -> str | None:
    p = phrase.strip().lower().rstrip("s")  # strip trailing plural s
    return _DOMAIN_CANONICAL.get(p) or _DOMAIN_CANONICAL.get(phrase.strip().lower()) or (
        phrase.strip().lower() if phrase.strip().lower() in _KNOWN_DOMAINS else None
    )


def _looks_like_domain(phrase: str) -> bool:
    return _canonicalize_domain(phrase) is not None


# ── Resolver ──────────────────────────────────────────────────────────────────

def resolve_elliptic_query(query: str, focus: FocusState | None) -> ResolvedQuery:
    """
    Detect and resolve shorthand follow-ups using current focus state.

    Returns ResolvedQuery with was_resolved=False if:
      - No focus_term is established
      - Query is not elliptic
      - Confidence is too low to resolve safely

    Hard rule: never hallucinate the focus_term. Only resolve when it is known.
    """
    q = query.strip()

    if not focus or not focus.current_focus_term:
        return ResolvedQuery(
            original_query=q, resolved_query=q,
            focus_term=None, target_domain=None,
            confidence=0.0, reason="no_focus", was_resolved=False,
        )

    ft = focus.current_focus_term

    # ── Pattern: "And in X?" / "In X?" ────────────────────────────────────────
    m = _DOMAIN_SHIFT_RE.match(q)
    if m:
        x = m.group(1).strip().rstrip("?.")
        domain = _canonicalize_domain(x)
        if domain:
            resolved = f"What is {ft} in {x}?"
            return ResolvedQuery(
                original_query=q, resolved_query=resolved,
                focus_term=ft, target_domain=domain,
                confidence=0.92, reason="domain_shift", was_resolved=True,
            )

    # ── Pattern: "And X?" (single domain word) ────────────────────────────────
    m = _AND_DOMAIN_RE.match(q)
    if m:
        x = m.group(1).strip().rstrip("?.")
        domain = _canonicalize_domain(x)
        if domain:
            resolved = f"What is {ft} in {x}?"
            return ResolvedQuery(
                original_query=q, resolved_query=resolved,
                focus_term=ft, target_domain=domain,
                confidence=0.88, reason="domain_shift", was_resolved=True,
            )

    # ── Pattern: "What about X?" ──────────────────────────────────────────────
    m = _WHAT_ABOUT_RE.match(q)
    if m:
        x = m.group(1).strip().rstrip("?.")
        domain = _canonicalize_domain(x)
        if domain:
            # "What about physics?" → "What is [term] in physics?"
            resolved = f"What is {ft} in {x}?"
            return ResolvedQuery(
                original_query=q, resolved_query=resolved,
                focus_term=ft, target_domain=domain,
                confidence=0.85, reason="domain_shift", was_resolved=True,
            )
        # X not a domain — might be a new term entirely, don't force resolve
        # Low confidence: pass through
        return ResolvedQuery(
            original_query=q, resolved_query=q,
            focus_term=ft, target_domain=None,
            confidence=0.3, reason="what_about_ambiguous", was_resolved=False,
        )

    # ── Pattern: "And the X meaning/one?" ─────────────────────────────────────
    m = _AND_THE_RE.match(q)
    if m:
        x = m.group(1).strip()
        domain = _canonicalize_domain(x)
        if domain:
            resolved = f"What is {ft} in {x}?"
            return ResolvedQuery(
                original_query=q, resolved_query=resolved,
                focus_term=ft, target_domain=domain,
                confidence=0.87, reason="domain_shift", was_resolved=True,
            )

    # ── Pattern: "The other kind/meaning?" ────────────────────────────────────
    if _OTHER_KIND_RE.match(q):
        alt_gloss = focus.alternate_sense_gloss()
        if alt_gloss:
            resolved = f"What is the other meaning of {ft}? (alternate gloss: {alt_gloss[:60]})"
        else:
            resolved = f"What are the different meanings of {ft}?"
        return ResolvedQuery(
            original_query=q, resolved_query=resolved,
            focus_term=ft, target_domain=None,
            confidence=0.80, reason="other_kind", was_resolved=True,
        )

    # ── Pattern: "The first/second meaning?" ─────────────────────────────────
    if _ORDINAL_RE.match(q):
        resolved = f"What are the different senses of {ft}?"
        return ResolvedQuery(
            original_query=q, resolved_query=resolved,
            focus_term=ft, target_domain=None,
            confidence=0.75, reason="ordinal_sense", was_resolved=True,
        )

    # ── Pattern: "How does that relate/compare?" ──────────────────────────────
    if _RELATE_RE.match(q):
        domain_ctx = f" in {focus.current_focus_domain}" if focus.current_focus_domain else ""
        resolved = f"How does {ft}{domain_ctx} relate to other concepts?"
        return ResolvedQuery(
            original_query=q, resolved_query=resolved,
            focus_term=ft, target_domain=focus.current_focus_domain,
            confidence=0.70, reason="anaphoric_relate", was_resolved=True,
        )

    # ── No pattern matched ────────────────────────────────────────────────────
    return ResolvedQuery(
        original_query=q, resolved_query=q,
        focus_term=ft, target_domain=None,
        confidence=0.0, reason="no_ellipsis_detected", was_resolved=False,
    )


# ── Focus update ──────────────────────────────────────────────────────────────

_FOCUS_STRUCTURE_WORDS = frozenset({
    "difference", "meaning", "definition", "kind", "type", "sort", "way",
    "thing", "aspect", "sense", "example", "concept", "term", "word",
    "between", "among", "versus", "compare",
    # Discourse adverbs / function words that should never be focus terms
    "actually", "really", "just", "also", "more", "most", "very",
    "quite", "rather", "now", "then", "still", "even", "first",
    "second", "third", "other", "same", "different", "certain",
    "clear", "full", "based", "given", "used",
})


# "What is X?" / "What is X in Y?" / "What are X?" — extract X (definiendum)
_DEFINIENDUM_RE = re.compile(
    r'(?:'
    r'\bwhat\s+(?:is|are)\s+(?:a\s+|an\s+|the\s+)?([a-z][\w\s]{0,20}?)(?:\s+in\s+|\s*\?|$)'
    r'|'
    r'\bwhat\s+about\s+(?:the\s+)?(?:concept\s+of\s+)?([a-z][\w\s]{0,20}?)(?:\s+in\s+|\s*\?|$)'
    r')',
    re.IGNORECASE,
)


def update_focus_from_lc(
    focus: FocusState,
    lc_result,                    # LanguageCognitionResult
    response_plan,                # ResponsePlan
    turn_number: int = 0,
    query: str = "",
) -> None:
    """
    Update focus state after a LangCog result.
    Called after each turn from selyrion_api.py.
    """
    # ── Focus term priority: ──────────────────────────────────────────────────
    #   1. Definiendum extracted from query ("What is X in Y?" → X)
    #   2. Most sense-rich non-structure word in sense_frames (fallback)
    sense_frames = getattr(lc_result.plan, "sense_frames", {}) or {}
    _new_focus_term = None
    _new_focus_hints: list = []

    # 1. Query definiendum extraction
    if query:
        m = _DEFINIENDUM_RE.search(query.strip())
        if m:
            candidate = (m.group(1) or m.group(2) or "").strip().lower().rstrip("s")  # naive singularize
            if len(candidate) > 2 and candidate not in _FOCUS_STRUCTURE_WORDS:
                _new_focus_term = candidate
                _new_focus_hints = sense_frames.get(candidate, [])

    subj = (getattr(response_plan, "subject", "") or "").strip()

    if not _new_focus_term:
        # Only consider words that appear in the query so discourse words like
        # "actually" don't win on OEWN sense count alone.
        query_words = set(re.sub(r'[^a-z\s]', '', query.lower()).split()) if query else set()
        candidates = {w: h for w, h in sense_frames.items()
                      if w.lower() not in _FOCUS_STRUCTURE_WORDS
                      and len(w) > 2
                      and (not query_words or w.lower() in query_words)}
        if candidates:
            _new_focus_term = max(candidates, key=lambda w: len(candidates[w]))
            _new_focus_hints = candidates[_new_focus_term]

    if _new_focus_term:
        focus.current_focus_term = _new_focus_term
        if _new_focus_hints:
            focus.current_focus_sense_id = getattr(_new_focus_hints[0], "sense_id", None)
            focus.current_focus_gloss = getattr(_new_focus_hints[0], "gloss", None)

    # ── Focus domain: active > persistent > pragma dominant ──────────────────
    domain = (
        lc_result.discourse_state.active_domain
        or lc_result.discourse_state.persistent_domain
        or (lc_result.pragmatic_reading.dominant_domain if lc_result.pragmatic_reading else None)
    )
    if domain:
        focus.current_focus_domain = domain
        focus.last_domain = domain

    # ── Last operator and response subject ────────────────────────────────────
    focus.last_operator = getattr(response_plan, "operator_used", None)
    if subj and len(subj) > 2:
        focus.last_response_subject = subj

    # ── Push to history ───────────────────────────────────────────────────────
    if focus.current_focus_term:
        focus.push(FocusEntry(
            turn=turn_number,
            focus_term=focus.current_focus_term,
            domain=focus.current_focus_domain,
            sense_id=focus.current_focus_sense_id,
            gloss=focus.current_focus_gloss,
        ))


# ── Audit ─────────────────────────────────────────────────────────────────────

_AUDIT_DB = Path.home() / "lexicon.db"
_AUDIT_TABLE_ENSURED = False


def _ensure_focus_audit_table() -> None:
    global _AUDIT_TABLE_ENSURED
    if _AUDIT_TABLE_ENSURED:
        return
    import sqlite3
    try:
        conn = sqlite3.connect(str(_AUDIT_DB))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dialogue_focus_audit (
                id             TEXT PRIMARY KEY,
                created_at     REAL,
                original_query TEXT,
                resolved_query TEXT,
                focus_term     TEXT,
                target_domain  TEXT,
                confidence     REAL,
                reason         TEXT
            )
        """)
        conn.commit()
        conn.close()
        _AUDIT_TABLE_ENSURED = True
    except Exception:
        pass


def write_focus_audit(resolved: ResolvedQuery) -> None:
    """Write a focus resolution trace. Only writes when resolution actually fired."""
    if not resolved.was_resolved:
        return
    _ensure_focus_audit_table()
    import sqlite3
    try:
        key = f"{resolved.original_query[:40]}{resolved.focus_term}{time.time()}"
        rid = "foc." + hashlib.md5(key.encode()).hexdigest()[:8]
        conn = sqlite3.connect(str(_AUDIT_DB))
        conn.execute(
            "INSERT OR IGNORE INTO dialogue_focus_audit VALUES (?,?,?,?,?,?,?,?)",
            (rid, time.time(), resolved.original_query[:200],
             resolved.resolved_query[:200], resolved.focus_term,
             resolved.target_domain, resolved.confidence, resolved.reason)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def read_focus_audits(limit: int = 20) -> list[dict]:
    _ensure_focus_audit_table()
    import sqlite3
    try:
        conn = sqlite3.connect(str(_AUDIT_DB))
        rows = conn.execute(
            "SELECT id,created_at,original_query,resolved_query,focus_term,target_domain,confidence,reason "
            "FROM dialogue_focus_audit ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        cols = ["id","created_at","original_query","resolved_query",
                "focus_term","target_domain","confidence","reason"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []
