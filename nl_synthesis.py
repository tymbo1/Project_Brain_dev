#!/usr/bin/env python3
"""
NL Synthesis — converts symbolic inference chains into natural language.
Works with triples from both memory.sym and CMS.
"""

TEMPLATES = {
    # Core
    "is_a":                "{s} is a type of {o}",
    "is_an":               "{s} is a type of {o}",
    "instance_of":         "{s} is an instance of {o}",
    "defined_as":          "{s} is defined as {o}",
    "related_to":          "{s} relates to {o}",
    "associative":         "{s} connects to {o}",
    "similar_to":          "{s} is similar to {o}",
    "opposite_of":         "{s} is the opposite of {o}",
    "same_as":             "{s} is also known as {o}",
    "distinct_from":       "{s} is distinct from {o}",
    "also_known_as":       "{s} is also known as {o}",
    # Causal
    "causes":              "{s} causes {o}",
    "can_cause":           "{s} can cause {o}",
    "affects":             "{s} affects {o}",
    "indirectly_affects":  "{s} indirectly affects {o}",
    "indirectly_produces": "{s} can indirectly produce {o}",
    "increases":           "{s} increases {o}",
    "reduces":             "{s} reduces {o}",
    "produces":            "{s} produces {o}",
    "transforms":          "{s} transforms {o}",
    "regulates":           "{s} regulates {o}",
    "activated_by":        "{s} is activated by {o}",
    # Functional
    "enables":             "{s} enables {o}",
    "requires":            "{s} requires {o}",
    "optimizes":           "{s} optimizes {o}",
    "extends":             "{s} extends {o}",
    "used_for":            "{s} is used for {o}",
    "capable_of":          "{s} is capable of {o}",
    "leads_to":            "{s} leads to {o}",
    # Structural
    "part_of":             "{s} is part of {o}",
    "has_a":               "{s} has {o}",
    "contains":            "{s} contains {o}",
    "has_subevent":        "{s} involves {o}",
    "facet_of":            "{s} is a facet of {o}",
    "manner_of":           "{s} is a manner of {o}",
    # Derivational
    "derived_from":        "{s} is derived from {o}",
    "etymologically_related": "{s} is etymologically related to {o}",
    "source_of":           "{s} is a source of {o}",
    # Logical
    "implies":             "{s} implies {o}",
    "proves":              "{s} proves {o}",
    "defines":             "{s} defines {o}",
    "generalizes":         "{s} generalises {o}",
    "approximates":        "{s} approximates {o}",
    # Cognitive
    "motivated_by":        "{s} is motivated by {o}",
    "desires":             "{s} desires {o}",
    "causes_desire":       "{s} causes desire for {o}",
    "predicts":            "{s} predicts {o}",
    "represents":          "{s} represents {o}",
    # Spatial
    "located_at":          "{s} is located at {o}",
    # Contextual
    "context_of":          "{s} provides context for {o}",
    "co_occurs_with":      "{s} co-occurs with {o}",
    # Creative
    "composer":            "{s} composed {o}",
    "lyricist":            "{s} wrote the lyrics for {o}",
    "writer":              "{s} wrote {o}",
    "arranger":            "{s} arranged {o}",
    # Property
    "has_property":        "{s} has the property of {o}",
    "genre":               "{s} belongs to the genre {o}",
    # Other
    "created_by":          "{s} was created by {o}",
    "outperforms":         "{s} outperforms {o}",
}

# Reverse templates — used when query is the OBJECT of the chain.
# Produces "DNA is required by organisms" instead of "organisms requires DNA".
REVERSE_TEMPLATES = {
    "requires":      "{o} is required for {s}",
    "produces":      "{o} is produced by {s}",
    "enables":       "{o} is involved in {s}",
    "binds_to":      "{o} is bound by {s}",
    "contains":      "{o} is found within {s}",
    "part_of":       "{o} contains {s}",
    "has_a":         "{o} is a property of {s}",
    "regulates":     "{o} is regulated by {s}",
    "causes":        "{o} is caused by {s}",
    "can_cause":     "{o} can be caused by {s}",
    "leads_to":      "{o} can result from {s}",
    "created_by":    "{o} was created by {s}",
    "activated_by":  "{o} activates {s}",
    "used_for":      "{o} can be used in {s}",
    "is_a":          "{o} is the category containing {s}",
    "context_of":    "{o} provides context for {s}",
}


_SUBJECT_FRAGMENT_WORDS = {
    "to", "of", "for", "by", "from", "that", "this", "are", "been",
    "into", "onto", "such", "these", "those", "which", "known", "not",
    "with", "its", "their", "our", "an", "be", "directly", "already",
}


_PRESERVE_LOWER = {
    "to", "of", "in", "at", "by", "an", "or", "it", "is", "as",
    "be", "do", "go", "no", "on", "up", "so", "and", "the", "a",
    "for", "but", "nor", "yet",
}

def _fmt(term: str) -> str:
    t = term.replace("_", " ").strip()
    # Uppercase each word that looks like an acronym (≤3 all-alpha chars)
    # but never uppercase common prepositions/articles/conjunctions
    words = t.split()
    result = []
    for w in words:
        if w.lower() in _PRESERVE_LOWER:
            result.append(w.lower())
        elif len(w) <= 3 and w.isalpha() and w == w.lower():
            result.append(w.upper())
        else:
            result.append(w)
    return " ".join(result)


def _parse(item: str):
    """Parse a chain item into (subject, relation, object). Returns None on failure."""
    if " | " in item:
        parts = item.split(" | ")
        if len(parts) >= 3:
            s = parts[0].strip()
            r = parts[1].strip()
            o = parts[2].strip().split(" | strength:")[0].strip()
            return s, r, o
    else:
        parts = item.split()
        if len(parts) >= 3:
            return parts[0], parts[1], " ".join(parts[2:])
    return None


def _is_clean_subject(s: str) -> bool:
    """A clean subject reads naturally as a noun phrase — not a sentence fragment."""
    words = s.replace("_", " ").lower().split()
    if len(words) > 2 or len(s) > 25:
        return False
    # Reject subjects containing function/fragment words
    return not any(w in _SUBJECT_FRAGMENT_WORDS for w in words)


def _render(s: str, r: str, o: str, query_norm: str = "") -> str:
    """Render one triple as a natural language sentence fragment.
    If the query is the object, prefers a reversed passive form.
    """
    # Handle explicit reverse prefix
    if r.startswith("rev_"):
        r = r[4:]
        s, o = o, s

    # If query is object and we have a nicer reverse form, use it
    if query_norm and o.lower().replace(" ", "_") == query_norm:
        rev = REVERSE_TEMPLATES.get(r)
        if rev:
            return rev.format(s=_fmt(s), o=_fmt(o))

    template = TEMPLATES.get(r)
    if template:
        return template.format(s=_fmt(s), o=_fmt(o))
    return f"{_fmt(s)} {_fmt(r)} {_fmt(o)}"


def synthesize(query: str, chains: list, max_sentences: int = 5) -> str:
    """
    Convert a raw inference chain list into a natural language response.
    """
    if not chains:
        return f"I don't have enough knowledge about '{_fmt(query)}' yet."

    # Parse and deduplicate — skip noisy/overlong triples
    seen = set()
    parsed = []
    for item in chains:
        triple = _parse(item)
        if not triple:
            continue
        s, r, o = triple
        if len(o) > 40 or len(s) > 40:
            continue
        key = (s, r, o)
        if key not in seen:
            seen.add(key)
            parsed.append(triple)

    if not parsed:
        return f"I found references to '{_fmt(query)}' but couldn't form a coherent response."

    query_norm = query.lower().replace(" ", "_")

    # Primary: query is subject — always include
    primary = [t for t in parsed if t[0].lower().replace(" ", "_") == query_norm]

    # Secondary: query is object — only include if subject reads cleanly as a noun phrase
    secondary = [
        t for t in parsed
        if t[2].lower().replace(" ", "_") == query_norm
        and t not in primary
        and _is_clean_subject(t[0])
    ]

    candidates = (primary + secondary)[:max_sentences]

    # If we have nothing usable, fall back to all parsed with clean subjects only
    if not candidates:
        candidates = [t for t in parsed if _is_clean_subject(t[0])][:max_sentences]

    if not candidates:
        return f"I found references to '{_fmt(query)}' but couldn't form a coherent response."

    sentences = [_render(*t, query_norm=query_norm) for t in candidates]
    sentences = [s[0].upper() + s[1:] if s else s for s in sentences]
    sentences = [s if s.endswith(".") else s + "." for s in sentences]

    # Join with a light connective flow
    if len(sentences) == 1:
        return sentences[0]
    lead = sentences[0]
    rest = " ".join(sentences[1:])
    return f"{lead} {rest}"
