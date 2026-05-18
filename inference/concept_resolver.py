#!/usr/bin/env python3
"""
concept_resolver.py

Converts natural language input → CMS anchor ID.

Cascade strategy:
  1. Normalize and exact match
  2. Fuzzy prefix / substring search
  3. LLaMA concept extraction → retry 1 & 2
  4. None if all fail

Used by the activation engine and any layer that needs to
bridge natural language to field anchors.
"""

import re
import sqlite3
import requests
import os

CMS_PATH     = os.path.expanduser("~/resonance_v11.db")
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3:8b"
TIMEOUT      = 15

# Stop words stripped before anchor lookup
_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "what", "who", "where", "when", "why", "how", "which",
    "tell", "me", "about", "explain", "describe", "define",
    "do", "does", "did", "can", "could", "would", "should",
    "know", "think", "believe", "understand", "mean", "means",
    "have", "has", "had", "please", "give", "show",
    "i", "you", "we", "they", "it", "my", "your", "its",
    "this", "that", "these", "those", "of", "in", "on",
    "at", "to", "for", "with", "by", "from", "and", "or",
    "not", "no", "any", "some", "all", "more", "most",
}


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_stop(text: str) -> str:
    """Remove stop words, return remaining as concept candidate."""
    tokens = [t for t in text.split() if t not in _STOP]
    return " ".join(tokens)


def _exact(db: sqlite3.Connection, term: str) -> tuple[str, int] | None:
    """Exact canonical match (visible anchors preferred)."""
    row = db.execute("""
        SELECT id, CAST(maturity AS INTEGER)
        FROM anchors
        WHERE canonical = ?
        ORDER BY visible DESC, maturity DESC
        LIMIT 1
    """, (term,)).fetchone()
    return (row[0], row[1]) if row else None


def _fuzzy(db: sqlite3.Connection, term: str, limit: int = 5) -> tuple[str, int] | None:
    """
    Prefix and substring search. Prefer:
      1. Exact prefix match
      2. Term as complete word within canonical
      3. Highest maturity among substring matches
    """
    rows = db.execute("""
        SELECT id, canonical, CAST(maturity AS INTEGER)
        FROM anchors
        WHERE canonical LIKE ?
        ORDER BY
            CASE WHEN canonical = ? THEN 0
                 WHEN canonical LIKE ? THEN 1
                 ELSE 2 END,
            visible DESC, maturity DESC
        LIMIT ?
    """, (f"%{term}%", term, f"{term}%", limit)).fetchall()

    if not rows:
        return None

    # Prefer match where our term is the whole canonical or starts it
    for row_id, canon, mat in rows:
        if canon == term or canon.startswith(term):
            return (row_id, mat)

    # Fallback: highest maturity substring match
    return (rows[0][0], rows[0][2])


def _llama_extract(query: str) -> str | None:
    """
    Ask LLaMA to extract the core concept from a natural language query.
    Returns a 1-3 word concept string or None on failure.
    """
    prompt = (
        f'Extract the single most important concept from this query: "{query}"\n'
        f"Return ONLY the concept name — 1 to 3 words, no punctuation, no explanation."
    )
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "system": "You extract concept names from natural language queries. Output only the concept — nothing else.",
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 12},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        raw = r.json().get("response", "").strip()
        # Clean up any punctuation LLaMA sneaks in
        concept = re.sub(r"[^\w\s]", "", raw).strip().lower()
        return concept if concept else None
    except Exception:
        return None


def resolve(query: str, use_llm: bool = True) -> tuple[str, int] | None:
    """
    Resolve a natural language query to a (anchor_id, relation_count) tuple.

    Args:
        query:   raw user input or extracted topic string
        use_llm: whether to fall back to LLaMA extraction (default True)

    Returns:
        (anchor_id, relation_count) or None
    """
    db = sqlite3.connect(CMS_PATH)

    def _try(term: str) -> tuple[str, int] | None:
        if not term or len(term) < 2:
            return None
        result = _exact(db, term)
        if result:
            return result
        result = _fuzzy(db, term)
        return result

    # Step 1: try raw query normalized
    norm = _normalize(query)
    result = _try(norm)
    if result:
        db.close()
        return result

    # Step 2: strip stop words and try the core concept
    stripped = _strip_stop(norm)
    if stripped and stripped != norm:
        result = _try(stripped)
        if result:
            db.close()
            return result

    # Step 3: try each word/phrase window (longest first)
    tokens = norm.split()
    for length in range(min(4, len(tokens)), 0, -1):
        for start in range(len(tokens) - length + 1):
            phrase = " ".join(tokens[start:start + length])
            if phrase in _STOP:
                continue
            result = _try(phrase)
            if result:
                db.close()
                return result

    # Step 4: LLaMA concept extraction
    if use_llm:
        concept = _llama_extract(query)
        if concept:
            result = _try(concept)
            if result:
                db.close()
                return result
            # Try stripped version of LLaMA output too
            stripped_concept = _strip_stop(_normalize(concept))
            if stripped_concept:
                result = _try(stripped_concept)
                if result:
                    db.close()
                    return result

    db.close()
    return None


def resolve_pair(query: str) -> tuple[tuple[str, int] | None, tuple[str, int] | None]:
    """
    For relational queries ('is X linked to Y'), resolve both concepts.
    Returns (anchor_a, anchor_b) — either may be None.
    """
    # Split on relational keywords
    _REL_SPLIT = re.compile(
        r"\b(linked to|related to|connected to|affect|affects|influence|"
        r"link to|compared to|versus|vs|and)\b",
        re.IGNORECASE
    )
    parts = _REL_SPLIT.split(query, maxsplit=1)
    if len(parts) >= 3:
        left  = resolve(parts[0].strip(), use_llm=True)
        right = resolve(parts[2].strip(), use_llm=True)
        return left, right
    return resolve(query), None


if __name__ == "__main__":
    tests = [
        "quantum mechnanics",        # typo
        "what do you know about dna",
        "explain photosynthesis",
        "who was charles darwin",
        "is dna related to protein",
        "tell me about the nervous system",
        "fluid dynamics",
        "Tim'aerion",
    ]
    for q in tests:
        result = resolve(q)
        if result:
            anchor_id, rc = result
            db = sqlite3.connect(CMS_PATH)
            canon = db.execute("SELECT canonical FROM anchors WHERE id=?", (anchor_id,)).fetchone()
            db.close()
            print(f"  ✓ {q!r:45} → {canon[0]!r} (maturity≈{rc})")
        else:
            print(f"  ✗ {q!r:45} → not found")
