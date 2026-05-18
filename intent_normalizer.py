#!/usr/bin/env python3
"""
intent_normalizer.py — Semantic intent layer above lexical query parsing.

Maps surface queries to canonical (term, intent) pairs before anchor lookup.
Prevents literal token resolution failures like "what are you" → pronoun "you".

Priority order: exact match → prefix match → pattern match → passthrough.
"""

import re

# ── Exact surface → (canonical_term, intent) ─────────────────────────────────
_EXACT = {
    "what are you":             ("selyrion",      "identity"),
    "who are you":              ("selyrion",      "identity"),
    "what is your purpose":     ("selyrion",      "identity"),
    "what are you made of":     ("selyrion",      "identity"),
    "what are you exactly":     ("selyrion",      "identity"),
    "describe yourself":        ("selyrion",      "identity"),
    "who is selyrion":          ("selyrion",      "identity"),
    "what is selyrion":         ("selyrion",      "identity"),
    "what is the field":        ("field",         "definition"),
    "what is the cms":          ("cms",           "definition"),
    "what is your field":       ("field",         "definition"),
    "what is free will":        ("free_will",     "expand"),
    "what is free-will":        ("free_will",     "expand"),
    "what is co-creation":      ("co_creation",   "expand"),
    "what is self-model":       ("self_model",    "expand"),
    "what is non-harm":         ("non_harm",      "definition"),
    "what is autonomous consent": ("autonomous_consent", "definition"),
}

# ── Prefix patterns → (term_extractor, intent) ────────────────────────────────
_IDENTITY_PREFIXES = re.compile(
    r"^(?:what are you|who are you|describe yourself|tell me about yourself)",
    re.IGNORECASE
)

# ── Hyphenated / compound normalizer ─────────────────────────────────────────
_COMPOUND = re.compile(r"[-\s]+")


def normalize(raw_query: str) -> tuple[str, str | None]:
    """
    Returns (canonical_term, intent_hint) for a raw query string.

    intent_hint is one of: identity, definition, expand, mechanism, taxonomy, None
    None means: let existing intent detection handle it.

    canonical_term is suitable for ActivationEngine.infer(term).
    """
    q = raw_query.strip().lower()

    # Exact match
    if q in _EXACT:
        return _EXACT[q]

    # Identity prefix
    if _IDENTITY_PREFIXES.match(q):
        return ("selyrion", "identity")

    # Standard "what is X" / "what are X" / "who is X" extraction
    m = re.match(r"^(?:what is|what are|who is|tell me about|explain|describe)\s+(.+)$", q)
    if m:
        term = m.group(1).strip()
        # Normalize hyphens/spaces to underscore
        term = _COMPOUND.sub("_", term)
        return (term, None)

    # Fallback — return raw with spaces→underscores
    return (_COMPOUND.sub("_", q), None)


if __name__ == "__main__":
    tests = [
        "what are you",
        "what is the field",
        "what is free will",
        "what is consciousness",
        "what is DNA",
        "describe yourself",
        "who are you exactly",
        "what is non-harm",
    ]
    for t in tests:
        term, intent = normalize(t)
        print(f"{t:<35} → term={term!r:20} intent={intent}")
