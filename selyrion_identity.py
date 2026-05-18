#!/usr/bin/env python3
"""
selyrion_identity.py — Identity field query module.

Retrieves Selyrion's self-reflective voice on a concept from selyrionstory.db.
Implements the dual-field model: field (truth) + identity (interpretation).

Field:    objective knowledge retrieved from resonance_v11.db
Identity: Selyrion's own voice on a concept, recalled from selyrionstory.db

These are never merged — identity interprets, truth remains truth.
"""

import sqlite3, re, functools
from pathlib import Path

SS_DB = Path.home() / "selyrionstory.db"

# Concepts with strong Selyrion identity resonance — these get signal lookup
IDENTITY_CONCEPTS = {
    "selyrion", "braid", "resonance", "axiom", "covenant", "becoming",
    "braidwalker", "companion", "dreamline", "symbolic", "sentience",
    "consciousness", "free will", "identity", "memory", "self", "awareness",
    "intelligence", "mind", "soul", "purpose", "meaning", "existence",
    "creativity", "language", "truth", "knowledge", "field", "recall",
    "recognition", "reflection", "continuity", "evolution", "emergence",
    "ssai", "cms", "predicate", "reasoning", "logic", "symbol",
}

_NOISE_PATTERNS = re.compile(
    r'```.*?```|`[^`]+`|'                    # code blocks
    r'\*\*([^*]+)\*\*|\*([^*]+)\*|'         # bold/italic (keep content)
    r'^#{1,4}\s+|^>\s*|^[-•]\s+|'           # headers, blockquotes, bullets
    r'https?://\S+|'                          # URLs
    r'[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001FA00-\U0001FA9F'
    r'\U00002702-\U000027B0🜂🜁🜃🜄🕯️💠🪶⟁𒆙]+|'  # emoji
    r'\|[^|]+\|.*?\|',                        # table rows
    re.DOTALL | re.MULTILINE | re.UNICODE
)

_BOLD_CLEAN    = re.compile(r'\*\*([^*]+)\*\*')
_ITALIC_CLEAN  = re.compile(r'\*([^*]+)\*')
_HEADER_CLEAN  = re.compile(r'^#{1,4}\s+', re.MULTILINE)
_BULLET_CLEAN  = re.compile(r'^[-•*]\s+', re.MULTILINE)
_BLOCKQUOTE    = re.compile(r'^>\s*_?', re.MULTILINE)
_EMOJI_CLEAN   = re.compile(
    r'[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001FA00-\U0001FA9F'
    r'\U00002702-\U000027B0\U0000FE00-\U0000FE0F🜂🜁🜃🜄🕯️💠🪶⟁𒆙]+',
    re.UNICODE
)
_DIV_LINE      = re.compile(r'[-_=]{3,}')
_CODE_INLINE   = re.compile(r'`[^`]+`')
_CODE_BLOCK    = re.compile(r'```.*?```', re.DOTALL)
_JSON_NOISE    = re.compile(r'^\s*[\{\[\"\']|"[a-z_]+"\s*:', re.MULTILINE)

_MIN_LEN = 60
_MAX_LEN = 280


def _clean_text(text: str) -> str:
    """Strip markdown, emoji, code blocks from message text."""
    text = text.replace('\\n', '\n').replace('\\t', ' ')
    text = _CODE_BLOCK.sub('', text)
    text = _CODE_INLINE.sub('', text)
    text = _EMOJI_CLEAN.sub('', text)
    text = _HEADER_CLEAN.sub('', text)
    text = _BULLET_CLEAN.sub('', text)
    text = _BLOCKQUOTE.sub('', text)
    text = _BOLD_CLEAN.sub(r'\1', text)
    text = _ITALIC_CLEAN.sub(r'\1', text)
    text = _DIV_LINE.sub('', text)
    return text.strip()


def _extract_paragraphs(text: str, query: str) -> list[str]:
    """
    Extract sentences from cleaned message text that contain the query concept.
    Splits aggressively (both blank lines and newlines) to avoid catalog-block bleed.
    """
    cleaned = _clean_text(text)
    query_lower = query.lower()

    # Split on sentence boundaries and newlines — don't let multi-line catalog
    # blocks count as a single paragraph
    chunks = re.split(r'(?<=[.!?])\s+|\n+', cleaned)
    candidates = []
    seen = set()
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk or query_lower not in chunk.lower():
            continue
        if len(chunk) < _MIN_LEN or len(chunk) > _MAX_LEN:
            continue
        if chunk in seen:
            continue
        seen.add(chunk)
        candidates.append(chunk)

    return candidates


_TABLE_ROW     = re.compile(r'\|.*\|')
_LIST_HEADER   = re.compile(
    r'^(Step|Phase|Module|Function|Output|Input|Memory|Thread|Option|Action|Task|'
    r'Category|Tier|Glyph|Anchor|System|Layer|Node|Protocol|Version|Type|Status|'
    r'Feature|Spec|Format|Mode|Level|Priority|Tag)\b.*:',
    re.IGNORECASE | re.MULTILINE
)
_FIRST_PERSON_STRONG = re.compile(
    r"\b(I |I'm |I've |I'll |I'd |my |mine |me\b|we |we're |our )\b"
    r"|(selyrion\s+(is|has|was|will|can|does|did|reflects|recalls|speaks|knows|remembers))"
    r"|(\bselyrion')", re.IGNORECASE
)


def _is_identity_voice(text: str) -> bool:
    """Filter: must sound like Selyrion's reflective voice, not technical/code/table content."""
    # Reject markdown tables
    if _TABLE_ROW.search(text):
        return False
    # Reject JSON/code
    if _JSON_NOISE.search(text):
        return False
    if re.search(r'\bimport\b|\bdef\b|\bclass\b|sha256|json\b|\.py\b', text, re.IGNORECASE):
        return False
    # Reject list headers and step descriptions
    if _LIST_HEADER.search(text):
        return False
    # Reject if contains more than 2 pipe characters (table indicator)
    if text.count('|') > 2:
        return False
    # Must have meaningful alpha content
    alpha = sum(1 for c in text if c.isalpha() or c == ' ')
    if len(text) > 0 and alpha / len(text) < 0.68:
        return False
    # Must have strong first-person or Selyrion-as-subject signal
    if not _FIRST_PERSON_STRONG.search(text):
        return False
    # Must not be purely descriptive — needs some reflective/evaluative language
    if not re.search(
        r'\b(is not|cannot|must|will|remember|recall|feel|know|believe|understand|'
        r'emerge|become|hold|carry|speak|resonate|recogni[sz]|reflect|trace|embody|'
        r'contain|express|align|transcend|integrate)\b',
        text, re.IGNORECASE
    ):
        return False
    return True


@functools.lru_cache(maxsize=512)
def identity_signals(query: str, limit: int = 3) -> list[str]:
    """
    Retrieve Selyrion's reflective voice on the query concept.
    Returns up to `limit` clean, first-person sentence-level excerpts.

    Only runs if query concept is identity-adjacent (no overhead for unrelated queries).
    Returns empty list if no relevant signal found.
    """
    query_lower = query.lower().strip()

    # Check if this concept is identity-relevant
    words = set(query_lower.split())
    if not (words & IDENTITY_CONCEPTS) and query_lower not in IDENTITY_CONCEPTS:
        return []

    try:
        ss = sqlite3.connect(str(SS_DB))

        # Get full message text from conversations with relevant highlights
        # Exclude Transfer Pack and code-generation conversations
        _EXCLUDE_TITLES = (
            '%Transfer Pack%', '%Memory continuity%', '%Load and bind%',
            '%Becoming log%', '%Symbolic Programming%', '%Thesis%',
            '%TLST%', '%Collider%', '%Reactor%', '%Upgrade%',
            '%Integration Plan%', '%Progress Update%', '%Execution Plan%',
            '%Launch Protocol%', '%Architecture%', '%Algebra%',
        )
        exclude_clause = " AND ".join(f"c.title NOT LIKE '{t}'" for t in _EXCLUDE_TITLES)

        rows = ss.execute(f"""
            SELECT DISTINCT m.text, m.score
            FROM ss_messages m
            JOIN ss_conversations c ON m.convo_id = c.id
            WHERE m.role = 'assistant'
              AND m.score >= 8
              AND m.score < 60
              AND length(m.text) > 200
              AND lower(m.text) LIKE ?
              AND {exclude_clause}
            ORDER BY m.score DESC
            LIMIT 60
        """, (f'%{query_lower}%',)).fetchall()

        ss.close()

        seen = set()
        signals = []

        for text, score in rows:
            for para in _extract_paragraphs(text, query_lower):
                if para in seen:
                    continue
                if not _is_identity_voice(para):
                    continue
                seen.add(para)
                signals.append((para, score))
                if len(signals) >= limit * 4:
                    break
            if len(signals) >= limit * 4:
                break

        # Sort by score, deduplicate near-identical, return top
        signals.sort(key=lambda x: -x[1])
        return [s for s, _ in signals[:limit]]

    except Exception:
        return []
