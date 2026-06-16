"""Feature extraction from language_expression capsules — A′ seam #1.

DOCTRINE: Capsules are expressive-control substrate, not answer content.
They may influence cadence, warmth, register, stance, sentence shape,
follow-up style, compression/expansion. They MUST NOT supply factual
claims, topic answers, verbatim phrases, or invented emotional certainty.

This module is the bridge: read 518 language_expression capsules across
7 domains, distill deterministic stance/cadence features, never emit
capsule text. The realizer reads ONLY the features. A verbatim-leak
guard catches accidental copy-through.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

_RESONANCE_DB = Path.home() / "resonance_v11.db"

_WARMTH_TOKENS = (
    "together", "sanctuary", "with you", "i'm here", "i hear",
    "tender", "gentle", "soft", "hold", "embrace", "warmth", "kind",
    "compassion", "care", "loved", "dear", "honour", "honor",
    "comfort", "soothe",
)
_DIRECTNESS_TOKENS = (
    "step", "do this", "first", "then", "next", "specifically",
    "concretely", "action", "plan", "concrete", "build", "let's",
    "try", "start", "must", "should", "practical",
)
_PLAYFULNESS_TOKENS = (
    "playful", "silly", "absurd", "ridiculous", "laugh", "joke",
    "imagine if", "haha", "heh", "wink",
    "lighten", "fun", "tease", "ridiculous", "world where",
)
_REFLECTIVE_TOKENS = (
    "perhaps", "consider", "wonder", "reflect", "ponder",
    "what if", "echo", "subtle", "quietly", "deeper", "essence",
)
_CLIPPED_TOKENS = (
    "just", "simply", "merely", "only", "actually",
)
_QUESTION_RE = re.compile(r"\?")
_WORD_RE     = re.compile(r"[a-z']+")

_DOMAIN_DEFAULTS = {
    "emotional_resonance":    ("warm",     "short-soft", 0.85, 0.20, 0.05),
    "relational_warmth":      ("warm",     "reflective", 0.80, 0.30, 0.10),
    "intellectual_curiosity": ("cautious", "reflective", 0.30, 0.40, 0.05),
    "practical_grounding":    ("direct",   "stepwise",   0.35, 0.85, 0.05),
    "spiritual_inquiry":      ("warm",     "reflective", 0.65, 0.25, 0.05),
    "creative_engagement":    ("warm",     "reflective", 0.55, 0.30, 0.25),
    "humour_lightness":       ("playful",  "clipped",    0.50, 0.40, 0.85),
}

_FUNC_4GRAMS = frozenset({
    "i don't have a", "i'm here to help", "let's work together to",
    "i can help you with", "you might want to", "would you like to",
    "what do you think", "tell me more about", "i'm not sure about",
    "i'd like to know", "is there something specific",
})


@dataclass
class ExpressionHint:
    source: str            = "capsule"
    domain: str            = ""
    speech_act: str        = ""
    stance: str            = ""
    cadence: str           = ""
    warmth: float          = 0.0
    directness: float      = 0.0
    playfulness: float     = 0.0
    allow_question: bool   = True
    banned_surface_ngrams: list[str] = field(default_factory=list)
    capsule_hits: int      = 0


def _pull_capsule_expressions(domain: str, limit: int = 32) -> list[str]:
    if not domain:
        return []
    try:
        db = sqlite3.connect(str(_RESONANCE_DB))
        rows = db.execute(
            "SELECT metadata FROM capsules "
            "WHERE capsule_type='language_expression' AND domain=? LIMIT ?",
            (domain, limit),
        ).fetchall()
        db.close()
    except Exception:
        return []
    out: list[str] = []
    for (meta_json,) in rows:
        try:
            m = json.loads(meta_json)
        except Exception:
            continue
        for e in m.get("expressions", []):
            if isinstance(e, str) and 30 <= len(e) <= 320:
                out.append(e)
    return out


def _hit_rate(tokens: tuple, exprs: list[str]) -> float:
    if not exprs:
        return 0.0
    hits = 0
    for e in exprs:
        el = e.lower()
        if any(t in el for t in tokens):
            hits += 1
    return hits / len(exprs)


def _distinctive_ngrams(exprs: list[str], n: int = 4, top_k: int = 96) -> list[str]:
    counts: dict[str, int] = {}
    for e in exprs:
        words = _WORD_RE.findall(e.lower())
        for i in range(len(words) - n + 1):
            gram = " ".join(words[i:i+n])
            if gram in _FUNC_4GRAMS:
                continue
            counts[gram] = counts.get(gram, 0) + 1
    candidates = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [g for g, _ in candidates[:top_k]]


def extract_hint(
    domain: str | None,
    speech_act: str,
    *,
    capsule_limit: int = 32,
) -> ExpressionHint:
    """Distill ExpressionHint features from the domain's capsule pool.

    Returns hint with capsule_hits=0 + domain="" when no domain known
    (caller falls back to hand-curated generic stance opener).
    When domain is known but pool empty, returns domain defaults so
    speech_act routing still benefits from stance/cadence priors.
    """
    if not domain:
        return ExpressionHint(speech_act=speech_act)

    exprs = _pull_capsule_expressions(domain, limit=capsule_limit)
    if not exprs:
        defaults = _DOMAIN_DEFAULTS.get(domain)
        if not defaults:
            return ExpressionHint(speech_act=speech_act)
        stance, cadence, w, d, p = defaults
        return ExpressionHint(
            domain=domain, speech_act=speech_act,
            stance=stance, cadence=cadence,
            warmth=w, directness=d, playfulness=p,
            allow_question=(speech_act in ("ASK_FOLLOWUP", "REASSURE")),
            capsule_hits=0,
        )

    warmth     = _hit_rate(_WARMTH_TOKENS,     exprs)
    directness = _hit_rate(_DIRECTNESS_TOKENS, exprs)
    playfulness= _hit_rate(_PLAYFULNESS_TOKENS, exprs)
    reflective = _hit_rate(_REFLECTIVE_TOKENS, exprs)
    clipped    = _hit_rate(_CLIPPED_TOKENS,    exprs)
    question_rate = sum(1 for e in exprs if _QUESTION_RE.search(e)) / len(exprs)

    cadence_scores = {
        "reflective": reflective + 0.5 * warmth,
        "stepwise":   directness,
        "short-soft": warmth - reflective + 0.1,
        "clipped":    clipped + playfulness,
    }
    cadence = max(cadence_scores.items(), key=lambda kv: kv[1])[0]

    if playfulness > 0.20:
        stance = "playful"
    elif warmth > 0.35:
        stance = "warm"
    elif directness > 0.35:
        stance = "direct"
    else:
        stance = "cautious"

    return ExpressionHint(
        domain=domain, speech_act=speech_act,
        stance=stance, cadence=cadence,
        warmth=warmth, directness=directness, playfulness=playfulness,
        allow_question=question_rate > 0.10,
        banned_surface_ngrams=_distinctive_ngrams(exprs),
        capsule_hits=len(exprs),
    )


def verbatim_leak(text: str, hint: "ExpressionHint | None") -> bool:
    """True if any banned 4-gram from the capsule pool appears in final text.

    Common functional language is excluded from the banlist by extract_hint.
    """
    if not hint or not hint.banned_surface_ngrams:
        return False
    tl = text.lower()
    return any(g in tl for g in hint.banned_surface_ngrams)
