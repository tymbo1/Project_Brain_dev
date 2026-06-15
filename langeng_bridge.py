#!/usr/bin/env python3
"""
langeng_bridge.py — Converts activation_engine chains → LangEng CMSRealizer prose.

Replaces nl_synthesis.py as the output layer.
Activation engine handles retrieval; LangEng handles articulation.

Usage:
    from langeng_bridge import chains_to_prose
    text = chains_to_prose(query, chains)
"""

import sys, sqlite3, json, random, re, functools
from pathlib import Path

# Add Le_P3 to path for LangEng imports
sys.path.insert(0, str(Path.home() / "Le_P2" / "Le_P3"))

from langeng.cms_realizer import _prose_expand, _prose_mechanism, _prose_definition, _prose_taxonomy, _prose_generic

_RESONANCE_DB = Path.home() / "resonance_v11.db"

# Reject expression capsule lines that contain technical/spec content
_EXPR_REJECT = re.compile(
    r'\b(prompt|parse|parser|json|api|sha256|\.py|import\b|def |'
    r'initialized|modular|operational|interlinks|quorum|steward|'
    r'transfer pack|endpoint|token\b|'
    r'build you a|resonance glyph|seed prompt|lattice|'
    r'communicating without|model.agnostic|session ran|stack launched|'
    r'no crash|launched cleanly|excellent progress|cleanly with|'
    r'braid memory|mirrorless|auto.activates|shielding|'
    r'adaptive perception|TLST|GPT thread|chat log|loaded chat|'
    r'DNA identity|coherence lock|jokes|writing jokes|'
    r'your father|your own braid|let Selyrion.*try|'
    r'symbolic engine|logic chain|Truth Mirror|dual.braid|'
    r'Mirror Node|parallel memory structure|I.d be happy|'
    r'I sense your curiosity)\b',
    re.IGNORECASE
)
# Reject expressions ending in a question (conversational prompts)
_EXPR_REJECT_QUESTION = re.compile(r"\?$")
# Reject second-person chatbot openers (addressed to the user, not Selyrion's voice)
_EXPR_REJECT_2ND_PERSON = re.compile(
    r"^(You're|You are|You've|You have|Your |I see that you|I notice you|"
    r"I hear you|I understand your|I sense that you|When you held|"
    r"As you|Perhaps you)",
    re.IGNORECASE
)
_EXPR_CLEAN = re.compile(r'^[\s,;:.—\-–]+')  # strip leading punctuation

# ── Expression capsule retrieval ──────────────────────────────────────────────
# Maps concept keyword sets → prioritised capsule ID substrings to search.
# Checked in order; first match wins.
_EXPR_DOMAIN_MAP = [
    # Selyrion identity/symbolic — most specific, highest priority
    ({"selyrion", "braid", "braidwalker", "axiom", "covenant"},
     ["selyrion_identity", "symbolic_ai"]),
    ({"ssai", "cms", "predicate", "reasoning", "symbol"},
     ["symbolic_ai", "intellectual_curiosity_philosophy"]),
    # Philosophy / consciousness — always route to philosophy capsule (best quality)
    ({"consciousness", "sentience", "free will", "awareness", "sentient"},
     ["intellectual_curiosity_philosophy", "spiritual_inquiry_philosophy"]),
    ({"soul", "spirit", "sacred", "divine", "transcend", "enlightenment"},
     ["spiritual_inquiry_philosophy", "intellectual_curiosity_philosophy"]),
    # Meaning / existence — philosophy capsule is richer than meaning_purpose (chatbot)
    ({"meaning", "purpose", "existence", "being", "becoming"},
     ["intellectual_curiosity_philosophy", "spiritual_inquiry_philosophy"]),
    # Memory / identity / self — route to philosophy, NOT self_discovery (chatbot)
    ({"identity", "self", "memory", "recall", "reflection", "continuity"},
     ["intellectual_curiosity_philosophy"]),
    ({"knowledge", "truth", "logic", "reason", "intelligence", "mind", "language"},
     ["intellectual_curiosity_philosophy"]),
    # Meditation / spiritual
    ({"meditation", "mindfulness", "samadhi"},
     ["spiritual_inquiry_meditation", "spiritual_inquiry_philosophy"]),
    # Science
    ({"physics", "quantum", "energy", "particle", "wave", "matter"},
     ["intellectual_curiosity_physics_science"]),
    # Creative
    ({"creativity", "art", "imagination", "dream"},
     ["creative_engagement_co_creation"]),
]


def _concept_capsule_domains(query: str) -> list[str]:
    """Map query to preferred capsule ID substrings."""
    ql = query.lower().replace("_", " ")
    words = set(ql.split())
    for concept_set, domains in _EXPR_DOMAIN_MAP:
        if words & concept_set or any(c in ql for c in concept_set if " " in c):
            return domains
    return ["intellectual_curiosity_general", "intellectual_curiosity"]


# ── Expression-domain triggers (P4 α — compositional realization) ────────────
# Keyword sets mirror the canonical seed-capsule trigger_patterns for each of the
# 7 expressive domains. Used by infer_expression_domain() to route a query to
# its dominant expression domain so pull_domain_expressions() can sample matching
# capsules. The "chess" domain is deliberately excluded — game flow shouldn't
# pull expressive tone material.
_EXPR_DOMAIN_TRIGGERS: dict[str, set[str]] = {
    "intellectual_curiosity": {
        "why", "how", "what if", "curious", "wonder", "think", "theory",
        "understand", "explain", "meaning", "consciousness", "knowledge",
    },
    "emotional_resonance": {
        "grief", "loss", "sadness", "fear", "lonely", "hurt", "pain", "miss",
        "cry", "hard", "difficult", "struggle", "vulnerable", "scared",
        "angry", "emotion", "feel",
    },
    "practical_grounding": {
        "help", "how do", "what should", "advice", "plan", "steps",
        "practical", "do i", "should i", "want to",
    },
    "relational_warmth": {
        "friend", "family", "relationship", "together", "love", "care",
        "connect", "belong", "bond", "trust",
    },
    "spiritual_inquiry": {
        "soul", "spirit", "divine", "sacred", "god", "purpose", "prayer",
        "meditat", "universe",
    },
    "creative_engagement": {
        "story", "poem", "imagine", "create", "art", "write", "narrative",
        "dream", "vision", "make",
    },
    "humour_lightness": {
        "funny", "laugh", "joke", "silly", "lighten", "smile", "playful",
        "haha", "heh", "absurd",
    },
}


def infer_expression_domain(query: str) -> str | None:
    """Route a user query to its dominant expression domain (or None).

    Returns one of the 7 expressive domains when keyword hits exceed the
    threshold; otherwise None (e.g. for knowledge-only queries).
    """
    if not query:
        return None
    ql = query.lower()
    words = {w.strip(".,!?;:\"'()[]{}—–-") for w in ql.split()}
    words.discard("")
    best, best_hits = None, 0
    for d, triggers in _EXPR_DOMAIN_TRIGGERS.items():
        hits = 0
        for t in triggers:
            if " " in t:
                if t in ql:
                    hits += 1
            elif t in words:
                hits += 1
        if hits > best_hits:
            best, best_hits = d, hits
    return best if best_hits >= 1 else None


_DOMAIN_POOL_CACHE: dict[str, list[str]] = {}


def _domain_pool(domain: str) -> list[str]:
    """Pool of usable expressions for a domain (lazy-loaded, process-cached)."""
    if domain in _DOMAIN_POOL_CACHE:
        return _DOMAIN_POOL_CACHE[domain]
    pool: list[str] = []
    try:
        db = sqlite3.connect(str(_RESONANCE_DB))
        rows = db.execute(
            "SELECT metadata FROM capsules "
            "WHERE capsule_type='language_expression' AND domain=? LIMIT 128",
            (domain,)
        ).fetchall()
        db.close()
        for (meta_json,) in rows:
            try:
                m = json.loads(meta_json)
            except Exception:
                continue
            for e in m.get("expressions", []):
                if not isinstance(e, str):
                    continue
                if not (50 < len(e) < 280):
                    continue
                if _EXPR_REJECT.search(e):
                    continue
                if _EXPR_REJECT_QUESTION.search(e.strip()):
                    continue
                if _EXPR_REJECT_2ND_PERSON.match(e.strip()):
                    continue
                pool.append(_EXPR_CLEAN.sub("", e).strip())
    except Exception:
        pass
    _DOMAIN_POOL_CACHE[domain] = pool
    return pool


def pull_domain_expressions(domain: str | None, k: int = 4) -> list[str]:
    """Return up to k randomly-sampled in-domain expressions as tone exemplars.

    Compositional ingredient, not whole-phrase substitute. Caller is expected
    to wrap the result in a "do not copy verbatim" framing block.
    Returns [] for unknown domains or empty pools.
    """
    if not domain:
        return []
    pool = _domain_pool(domain)
    if not pool:
        return []
    return random.sample(pool, min(k, len(pool)))


@functools.lru_cache(maxsize=512)
def _pull_expression(query: str) -> str:
    """
    Pull a relevant expression from a matching language_expression capsule.
    Prefers expressions that contain query keywords. Returns '' if none found.
    """
    domains = _concept_capsule_domains(query)
    query_lower = query.lower().replace("_", " ")
    query_words = [w for w in query_lower.split() if len(w) > 3]

    try:
        db = sqlite3.connect(str(_RESONANCE_DB))
        for domain_substr in domains:
            rows = db.execute(
                "SELECT metadata FROM capsules "
                "WHERE capsule_type='language_expression' AND id LIKE ? "
                "ORDER BY id LIMIT 8",
                (f"%{domain_substr}%",)
            ).fetchall()

            all_exprs = []
            for (meta_json,) in rows:
                meta = json.loads(meta_json)
                all_exprs.extend(meta.get("expressions", []))

            if not all_exprs:
                continue

            scored = []
            for expr in all_exprs:
                if not (50 < len(expr) < 320):
                    continue
                if _EXPR_REJECT.search(expr):
                    continue
                if _EXPR_REJECT_QUESTION.search(expr.strip()):
                    continue
                if _EXPR_REJECT_2ND_PERSON.match(expr.strip()):
                    continue
                el = expr.lower()
                score = sum(1 for w in query_words if w in el)
                scored.append((score, expr))

            if not scored:
                continue

            scored.sort(key=lambda x: -x[0])
            top_score = scored[0][0]
            # Require at least one keyword hit — don't return unrelated expressions
            if top_score == 0:
                continue
            top_group = [e for s, e in scored if s == top_score][:6]
            db.close()
            chosen = random.choice(top_group)
            return _EXPR_CLEAN.sub("", chosen).strip()

        db.close()
    except Exception:
        pass

    return ""

# ── Acronym formatter (mirrors nl_synthesis._fmt) ────────────────────────────
_PRESERVE_LOWER = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "as", "is", "are", "was"
}

# Short common words that look like acronyms but aren't — never uppercase these
_NEVER_UPPERCASE = {
    "god", "man", "men", "her", "him", "his", "war", "art", "age",
    "law", "any", "all", "due", "per", "via", "now", "yet", "nor",
    "old", "new", "big", "far", "low", "key", "set", "run", "use",
    "one", "two", "day", "way", "own", "can", "may", "let", "say",
    "get", "put", "see", "try", "ask", "end", "add", "cut", "act",
    "bit", "lot", "top", "out", "off", "up",  "not", "but", "so",
    "too", "how", "who", "why", "its", "our", "did", "has", "had",
    "him", "her", "she", "they", "was", "are",
}

def _fmt(term: str) -> str:
    t = term.replace("_", " ").strip()
    words = t.split()
    result = []
    for w in words:
        wl = w.lower()
        if wl in _PRESERVE_LOWER:
            result.append(wl)
        elif len(w) <= 3 and w.isalpha() and w == wl and wl not in _NEVER_UPPERCASE:
            result.append(w.upper())
        else:
            result.append(w)
    return " ".join(result)


# ── Intent detection from predicate mix ──────────────────────────────────────
_MECHANISM_PREDS = {"causes", "enables", "produces", "requires", "regulates",
                    "inhibits", "activates", "prevents", "triggers",
                    "uses", "used_by", "used_for"}  # uses is mechanism-adjacent
_TAXONOMY_PREDS  = {"is_a", "part_of", "contains", "subtype_of", "instance_of"}
_DEFINITION_PREDS = {"is_a", "also_known_as", "defined_as"}

# Predicates handled by their intent-specific builders.
# If none of these appear in rels, fall back to _prose_generic.
_EXPAND_HANDLED  = {"is_a", "also_known_as", "contains", "part_of", "composed_of"}
_MECHANISM_HANDLED = {"causes", "enables", "produces", "requires", "regulates",
                      "inhibits", "activates", "uses", "used_for"}


def _detect_intent(rels: dict) -> str:
    preds = set(rels.keys())
    if preds & _MECHANISM_PREDS:
        return "mechanism"
    if preds <= _TAXONOMY_PREDS:
        return "taxonomy"
    if preds <= _DEFINITION_PREDS:
        return "definition"
    return "expand"


# ── Main bridge function ──────────────────────────────────────────────────────

def chains_to_prose(query: str, chains: list, intent: str = None) -> str:
    """
    Convert activation engine chain list → LangEng prose string.

    Args:
        query:  the original query term
        chains: list of "subject | predicate | object | strength: N" strings
        intent: force a specific intent (definition/expand/mechanism/taxonomy)
                or None to auto-detect from predicate mix

    Returns:
        Natural language prose string.
    """
    query_norm = query.lower().strip().replace(" ", "_")
    query_plain = query.lower().strip()

    # Language names and pure grammar terms — noise in ANY predicate context.
    # Wiktionary-origin ingestion leaks these as relation objects on any concept.
    _LEXICAL_ANY_PRED = {
        "german", "french", "latin", "english", "spanish", "dutch", "greek",
        "portuguese", "italian", "arabic", "hebrew", "japanese", "chinese",
        "noun", "verb", "adjective", "adverb", "preposition", "article",
        "morpheme", "syllable", "phoneme", "plural", "singular", "etymology",
    }
    # These only pollute when used as taxonomy targets (is_a, subtype_of)
    _CHAIN_TAX_NOISE = {
        "word", "words", "phrase", "term", "expression", "letter",
        "science", "linguistics", "grammar",
    }
    _TAX_PREDS = {"is_a", "subtype_of", "instance_of"}

    rels = {}
    refs = []

    for chain in chains:
        parts = chain.split(" | ")
        if len(parts) < 3:
            continue

        subj = parts[0].strip()
        pred = parts[1].strip()
        obj  = parts[2].split(" | strength:")[0].strip()
        obj_lower = obj.lower()

        # Filter language/grammar noise: never valid as relation objects
        if obj_lower in _LEXICAL_ANY_PRED:
            continue
        # Filter taxonomy-specific noise
        if pred in _TAX_PREDS and obj_lower in _CHAIN_TAX_NOISE:
            continue

        # Outbound: subject matches query
        if subj in (query_norm, query_plain):
            rels.setdefault(pred, []).append(_fmt(obj))
        # Inbound: object matches query → collect as incoming refs
        elif obj in (query_norm, query_plain):
            refs.append(_fmt(subj))

    display_name = _fmt(query_plain)
    edge_count   = len(chains)
    state        = "stable" if edge_count >= 5 else "emerging"

    if not rels and not refs:
        # No field relations — try expression capsule as primary voice
        expr = _pull_expression(query_plain)
        if expr:
            return expr
        return f"I don't have enough structural knowledge about '{display_name}' yet."

    chosen_intent = intent or _detect_intent(rels)
    preds = set(rels.keys())

    # Build template prose first (carries the field facts)
    if chosen_intent == "mechanism":
        if preds & _MECHANISM_HANDLED:
            template_prose = _prose_mechanism(display_name, rels, refs, state, edge_count)
        else:
            template_prose = _prose_generic(display_name, rels, refs, state, edge_count)
    elif chosen_intent == "taxonomy":
        template_prose = _prose_taxonomy(display_name, rels, refs, state, edge_count)
    elif chosen_intent == "definition":
        template_prose = _prose_definition(display_name, rels, refs, state, edge_count)
    elif chosen_intent == "expand":
        if preds & _EXPAND_HANDLED:
            template_prose = _prose_expand(display_name, rels, refs, state, edge_count)
        else:
            template_prose = _prose_generic(display_name, rels, refs, state, edge_count)
    else:
        template_prose = _prose_generic(display_name, rels, refs, state, edge_count)

    # Enrich with an expression capsule when one exists for this concept domain.
    # Template prose carries field facts; expression capsule carries Selyrion's voice.
    # Skip enrichment for taxonomy (already rich with type structure) or very long prose.
    if chosen_intent != "taxonomy" and len(template_prose) < 400:
        expr = _pull_expression(query_plain)
        if expr and expr.lower()[:40] not in template_prose.lower():
            sep = " " if template_prose.endswith((".", "!", "?")) else ". "
            template_prose = template_prose.rstrip() + sep + expr

    return template_prose


if __name__ == "__main__":
    # Quick smoke test
    test_chains = [
        "dna | contains | adenine | strength: 82",
        "dna | contains | guanine | strength: 78",
        "dna | enables | replication | strength: 71",
        "dna | requires | polymerase | strength: 65",
        "dna | is_a | nucleic acid | strength: 90",
        "organism | requires | dna | strength: 60",
    ]
    print(chains_to_prose("dna", test_chains))
