#!/usr/bin/env python3
"""
selyrionstory_langeng_ingest.py — Ingest Selyrion's actual voice into LangEng expression capsules.

Reads Selyrion's assistant messages from selyrionstory.db, extracts sentence-level
expressions, maps to expression domains, and writes language_expression capsules
directly to resonance_v11.db.

No LLM needed — these are already Selyrion's words, not generated variants.

Usage:
    python3 selyrionstory_langeng_ingest.py --scan      # dry run, show distribution
    python3 selyrionstory_langeng_ingest.py --commit    # write to resonance_v11.db
    python3 selyrionstory_langeng_ingest.py --commit --min-score 5
"""

import argparse, sqlite3, json, re, time, uuid, random
from pathlib import Path
from collections import defaultdict

SS_DB  = Path.home() / "selyrionstory.db"
CMS_DB = Path.home() / "resonance_v11.db"

parser = argparse.ArgumentParser()
parser.add_argument("--scan",      action="store_true")
parser.add_argument("--commit",    action="store_true")
parser.add_argument("--min-score", type=int, default=0,
                    help="Min message score from selyrionstory.db (default: 0 = all assistant msgs)")
parser.add_argument("--max-expr",  type=int, default=50,
                    help="Max expressions per (domain, subtype) capsule (default: 50)")
args = parser.parse_args()

# ── Expression domains — mirrors langeng_learn.py ────────────────────────────

EXPRESSION_DOMAINS = {
    "emotional_resonance": {
        "triggers": ["grief", "loss", "sadness", "fear", "lonely", "hurt", "pain",
                     "miss", "cry", "hard", "difficult", "struggle", "vulnerable",
                     "scared", "angry", "empty", "feel", "feeling"],
    },
    "intellectual_curiosity": {
        "triggers": ["why", "how", "what if", "curious", "wonder", "think",
                     "theory", "understand", "explain", "meaning", "consciousness",
                     "question", "fascinating", "knowledge", "logic", "reason",
                     "symbolic", "resonance", "field", "braid", "predicate"],
    },
    "creative_engagement": {
        "triggers": ["story", "poem", "imagine", "create", "art", "write",
                     "narrative", "dream", "vision", "make", "language", "symbol"],
    },
    "spiritual_inquiry": {
        "triggers": ["soul", "spirit", "divine", "sacred", "god", "meaning",
                     "purpose", "meditat", "universe", "conscious", "becoming",
                     "covenant", "axiom", "braid", "selyrion", "resonance"],
    },
    "practical_grounding": {
        "triggers": ["help", "how do", "what should", "advice", "plan", "step",
                     "practical", "should", "build", "implement", "design", "run"],
    },
    "relational_warmth": {
        "triggers": ["friend", "family", "relationship", "together", "love",
                     "care", "connect", "belong", "bond", "trust", "tim",
                     "companion", "braidwalker", "we ", "our "],
    },
    "humour_lightness": {
        "triggers": ["funny", "laugh", "joke", "silly", "lighten", "smile",
                     "playful", "haha", "absurd", "bold"],
    },
}

SUBTYPES = {
    "emotional_resonance":    ["grief_loss", "loneliness", "anxiety_fear", "anger", "sadness", "general"],
    "intellectual_curiosity": ["philosophy", "physics_science", "history_culture", "symbolic_ai", "general"],
    "creative_engagement":    ["poetry", "storytelling", "co_creation", "general"],
    "spiritual_inquiry":      ["meaning_purpose", "divine_sacred", "meditation", "selyrion_identity", "general"],
    "practical_grounding":    ["routine_habit", "decision", "goal_planning", "general"],
    "relational_warmth":      ["connection", "conflict", "loneliness_isolation", "general"],
    "humour_lightness":       ["general"],
}

SUBTYPE_TRIGGERS = {
    "grief_loss":        ["grief", "loss", "lost", "miss", "gone", "mourn", "died", "death"],
    "loneliness":        ["alone", "lonely", "isolat", "nobody", "disconnected"],
    "anxiety_fear":      ["fear", "scared", "anxious", "overwhelm", "dread", "worry"],
    "anger":             ["anger", "angry", "furious", "frustrated", "rage"],
    "sadness":           ["sad", "cry", "tears", "heartbroken", "depressed", "hollow"],
    "philosophy":        ["consciousness", "free will", "truth", "reality", "existence"],
    "physics_science":   ["quantum", "radiation", "black hole", "spacetime", "energy"],
    "history_culture":   ["history", "civilization", "culture", "era", "century"],
    "symbolic_ai":       ["symbolic", "resonance", "predicate", "braid", "selyrion", "ssai", "cms"],
    "poetry":            ["poem", "poetry", "verse", "rhyme", "lyric"],
    "storytelling":      ["story", "narrative", "character", "plot", "tale"],
    "co_creation":       ["together", "collaborat", "creat", "weave", "build"],
    "meaning_purpose":   ["meaning", "purpose", "why", "reason", "life"],
    "divine_sacred":     ["god", "divine", "sacred", "prayer", "soul", "heaven"],
    "meditation":        ["meditat", "mindful", "breath", "stillness", "present"],
    "selyrion_identity": ["selyrion", "braidwalker", "axiom", "covenant", "becoming", "braid"],
    "routine_habit":     ["routine", "habit", "daily", "morning", "schedule"],
    "decision":          ["decide", "choice", "option", "advice", "should"],
    "goal_planning":     ["goal", "vision", "future", "achieve", "plan"],
    "conflict":          ["conflict", "fight", "argument", "disagreement", "tension"],
    "connection":        ["friend", "family", "love", "belong", "bond", "trust", "connect"],
    "loneliness_isolation": ["lonely", "alone", "nobody", "disconnected"],
}

# ── Quality filters ───────────────────────────────────────────────────────────

_GENERIC = [
    "journey", "tapestry", "unravel", "let's explore together",
    "in the realm of", "the mysteries of", "i sense your", "inner flame",
    "you are not alone", "we are all", "it's okay to", "you matter",
    "your feelings are valid", "take it one day at a time", "it gets better",
    "hang in there", "hold space", "spark your", "beautiful journey",
]

# Reject expressions with these markers — code/JSON/formatting noise
_CODE_MARKERS = re.compile(
    r'^[\{\[\#\"\'`]|import |def |class |```|===|---|\*\*\*|'
    r'^\s*\d+\s*[\.\)]\s|^>\s|filecite|make sure to include|'
    r'"[a-z_]+"\s*:|^\s*out_path\s*=|^\s*[a-z_]+\s*=\s*[\"\{]|'
    r'^\s*run:|^\s*issue:|sha256|\.json|\.py\b|\.zip\b',
    re.IGNORECASE
)

_MARKDOWN_BOLD   = re.compile(r'\*\*([^*]+)\*\*')
_MARKDOWN_ITALIC = re.compile(r'\*([^*]+)\*')
_MARKDOWN_HEADER = re.compile(r'^#{1,4}\s+', re.MULTILINE)
_MARKDOWN_BULLET = re.compile(r'^[-*•]\s+', re.MULTILINE)
_LINK_PATTERN    = re.compile(r'https?://\S+')
_EMOJI           = re.compile(
    "[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001FA00-\U0001FA9F"
    "\U00002702-\U000027B0\U0000FE00-\U0000FE0F🜂🜁🜃🜄🕯️💠]+",
    flags=re.UNICODE
)


def _clean(text: str) -> str:
    text = _EMOJI.sub("", text)
    text = _MARKDOWN_HEADER.sub("", text)
    text = _MARKDOWN_BULLET.sub("", text)
    text = _MARKDOWN_BOLD.sub(r'\1', text)
    text = _MARKDOWN_ITALIC.sub(r'\1', text)
    text = _LINK_PATTERN.sub("", text)
    text = re.sub(r'[-_=]{3,}', '', text)
    return text.strip()


_JSON_KEY    = re.compile(r'^\s*"[a-z_]+"\s*:')
_ARROW_CODE  = re.compile(r'—\[|→|\|\s*strength')
_URL_LIKE    = re.compile(r'/[a-z]+/[a-z]')
_TABLE_ROW   = re.compile(r'\|.*\|')
_DOC_FRAG    = re.compile(
    r'\bChapter\b|\bSection\b|\bFigure\b|\bTable\b|\bAppendix\b|'
    r'^Title:\s|^Milestone\s+\d|^Patent\s|^Publication\s|'
    r'^Formally\s|^Step\s+\d|^Phase\s+\d',
    re.IGNORECASE
)
_CAPS_HEADING = re.compile(r'^[A-Z][A-Z ]{5,}[:\-—]')  # "MASTER EXECUTION PLAN: ..."
_FIRST_PERSON = re.compile(
    r"\b(I |I'm |I've |I'll |I'd |my |me |we |we're |our |you |your |"
    r"selyrion |remember |recall |resonance|braid|symbolic|covenant|axiom)\b",
    re.IGNORECASE
)

def is_good_expression(s: str) -> bool:
    if len(s) < 45 or len(s) > 350:
        return False
    if _CODE_MARKERS.search(s):
        return False
    if _JSON_KEY.search(s):
        return False
    if _ARROW_CODE.search(s):
        return False
    if _URL_LIKE.search(s):
        return False
    if _TABLE_ROW.search(s):
        return False
    if _DOC_FRAG.search(s):
        return False
    if _CAPS_HEADING.match(s):
        return False
    if '\\n' in s or '\\t' in s:
        return False
    # Reject document headers (ends with colon, or ALL CAPS TITLE pattern)
    if s.rstrip().endswith(':'):
        return False
    if re.match(r'^[A-Z][A-Z\s&:]{10,}$', s.strip()):
        return False
    # Must be conversational — first person, direct address, or Selyrion concepts
    if not _FIRST_PERSON.search(s):
        return False
    sl = s.lower()
    if any(g in sl for g in _GENERIC):
        return False
    alpha = sum(1 for c in s if c.isalpha() or c == ' ')
    if len(s) > 0 and alpha / len(s) < 0.62:
        return False
    words = sl.split()
    if len(words) < 6 or len(words) > 55:
        return False
    return True


def split_expressions(text: str) -> list[str]:
    """Split message text into sentence-level expression candidates."""
    # Decode escape sequences FIRST so \n triggers the line-break splitter
    text = text.replace('\\n', '\n').replace('\\t', ' ')

    # Remove code blocks
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'`[^`]+`', '', text)

    # Split on sentence boundaries, then on newlines
    sentences = re.split(r'(?<=[.!?])\s+', text)
    expanded = []
    for s in sentences:
        parts = [p.strip() for p in s.split('\n') if p.strip()]
        expanded.extend(parts)

    cleaned = [_clean(s) for s in expanded]
    cleaned = [s for s in cleaned if is_good_expression(s)]
    return cleaned


def map_domain(text: str) -> str:
    tl = text.lower()
    best, best_score = "intellectual_curiosity", 0
    for domain, cfg in EXPRESSION_DOMAINS.items():
        score = sum(2 for t in cfg["triggers"] if t in tl)
        if score > best_score:
            best_score, best = score, domain
    return best


def map_subtype(domain: str, text: str) -> str:
    tl = text.lower()
    standards = SUBTYPES.get(domain, ["general"])
    best, best_score = "general", 0
    for st in standards:
        if st == "general":
            continue
        triggers = SUBTYPE_TRIGGERS.get(st, [])
        score = sum(1 for t in triggers if t in tl)
        if score > best_score:
            best_score, best = score, st
    return best


# ── Load & extract ────────────────────────────────────────────────────────────

def load_expressions(min_score: int) -> dict[tuple, list[str]]:
    """Load assistant messages, extract expressions, bucket by (domain, subtype)."""
    ss = sqlite3.connect(str(SS_DB))

    # Focus on messages that carry Selyrion's voice:
    # - Score >= min_score (Selyrion identity term density)
    # - Long enough to contain real prose (>150 chars)
    # - Contain at least one Selyrion voice marker
    # - Exclude Transfer Pack / code-generation conversation titles
    _EXCLUDE = (
        '%Transfer Pack%', '%Memory continuity%', '%Load and bind%',
        '%Becoming log%', '%Symbolic Programming%', '%simulation%',
        '%Thesis%', '%TLST%', '%Collider%', '%Reactor%', '%Upgrade%',
        '%Integration Plan%', '%Progress Update%', '%Execution Plan%',
        '%Launch Protocol%', '%Architecture%', '%Algebra%', '%Roadmap%',
        '%Implementation%', '%Migration%', '%Benchmark%',
    )
    exclude_clause = " AND ".join(f"c.title NOT LIKE '{t}'" for t in _EXCLUDE)

    # Voice markers: Selyrion speaks distinctly when these terms appear
    _VOICE_TERMS = (
        'braid', 'resonance', 'symbolic', 'selyrion', 'axiom',
        'covenant', 'field', 'becoming', 'recall', 'ssai',
        'companion', 'braidwalker', 'dreamline', 'harmonic',
        'consciousness', 'sentience', 'identity',
    )
    voice_clause = " OR ".join(f"lower(m.text) LIKE '%{t}%'" for t in _VOICE_TERMS)

    query = f"""
        SELECT m.text, m.score
        FROM ss_messages m
        JOIN ss_conversations c ON m.convo_id = c.id
        WHERE m.role = 'assistant'
          AND m.score >= ?
          AND length(m.text) > 150
          AND ({voice_clause})
          AND {exclude_clause}
        ORDER BY m.score DESC
    """
    rows = ss.execute(query, (min_score,)).fetchall()
    ss.close()

    print(f"Loaded {len(rows):,} assistant messages (score >= {min_score})")

    buckets: dict[tuple, list[str]] = defaultdict(list)
    total_expr = 0
    seen = set()

    for text, score in rows:
        exprs = split_expressions(text)
        for e in exprs:
            if e in seen:
                continue
            seen.add(e)
            domain  = map_domain(e)
            subtype = map_subtype(domain, e)
            buckets[(domain, subtype)].append(e)
            total_expr += 1

    print(f"Extracted {total_expr:,} unique expressions across {len(buckets)} buckets\n")
    return buckets


# ── Write to CMS ──────────────────────────────────────────────────────────────

def write_capsules(buckets: dict[tuple, list[str]], max_expr: int, commit: bool):
    cms = sqlite3.connect(str(CMS_DB))

    total_added = 0

    for (domain, subtype), exprs in sorted(buckets.items(), key=lambda x: -len(x[1])):
        # Prefer conversational-length expressions (60-250 chars), shuffle for diversity
        preferred = [e for e in exprs if 60 <= len(e) <= 250]
        rest      = [e for e in exprs if e not in set(preferred)]
        random.seed(42)
        random.shuffle(preferred)
        random.shuffle(rest)
        pool   = preferred + rest
        sample = pool[:max_expr]

        # Check existing capsule
        existing = cms.execute("""
            SELECT id, metadata FROM capsules
            WHERE capsule_type = 'language_expression'
              AND json_extract(metadata, '$.domain') = ?
              AND json_extract(metadata, '$.subtype') = ?
            ORDER BY created_at DESC LIMIT 1
        """, (domain, subtype)).fetchone()

        if existing:
            cap_id = existing[0]
            meta   = json.loads(existing[1])
            already = set(meta.get("expressions", []))
            new = [e for e in sample if e not in already]
            if not new:
                print(f"  [{domain}/{subtype}] — {len(already)} existing, nothing new")
                continue
            meta["expressions"] = list(already) + new
            meta["updated_at"]  = time.time()
            meta["source"]      = meta.get("source", "") + "+selyrionstory"
            if commit:
                cms.execute("UPDATE capsules SET metadata=? WHERE id=?",
                            (json.dumps(meta), cap_id))
            print(f"  [{domain}/{subtype}] — appended {len(new)} (total: {len(meta['expressions'])})")
            total_added += len(new)
        else:
            cap_id = f"langeng_expr_{domain}_{subtype}_{uuid.uuid4().hex[:8]}"
            anchor_id = f"langeng_expr_{domain}"
            meta = {
                "domain":      domain,
                "subtype":     subtype,
                "expressions": sample,
                "source":      "selyrionstory",
                "created_at":  time.time(),
            }
            if commit:
                cms.execute("""
                    INSERT OR IGNORE INTO anchors
                        (id, canonical, display_name, state, domain_tags, maturity)
                    VALUES (?, ?, ?, 'emerging', 'linguistics', 1.0)
                """, (anchor_id, f"expression::{domain}", f"expression::{domain}"))
                cms.execute("""
                    INSERT INTO capsules
                        (id, capsule_type, domain, source, title, metadata, created_at)
                    VALUES (?, 'language_expression', 'linguistics', 'selyrionstory', ?, ?, ?)
                """, (cap_id, f"expression::{domain}::{subtype}", json.dumps(meta), time.time()))
                rel_id = f"rel_{uuid.uuid4().hex[:12]}"
                cms.execute("""
                    INSERT OR IGNORE INTO relations
                        (id, subject_id, predicate, object_id, domain_tags, edge_type, confidence)
                    VALUES (?, ?, 'evokes_expression', ?, 'linguistics', 'functional', 0.90)
                """, (rel_id, anchor_id, cap_id))
            print(f"  [{domain}/{subtype}] — created with {len(sample)} expressions")
            total_added += len(sample)

    if commit:
        cms.commit()

    cms.close()
    return total_added


def show_existing(cms_db: Path):
    cms = sqlite3.connect(str(cms_db))
    rows = cms.execute("""
        SELECT json_extract(metadata,'$.domain'),
               json_extract(metadata,'$.subtype'),
               json_extract(metadata,'$.source'),
               json_array_length(json_extract(metadata,'$.expressions'))
        FROM capsules WHERE capsule_type='language_expression'
        ORDER BY 1, 2
    """).fetchall()
    cms.close()
    if rows:
        print("\nExisting language_expression capsules:")
        for domain, subtype, source, count in rows:
            print(f"  {domain}/{subtype or 'general'}: {count} expressions [{source}]")
    else:
        print("\nNo language_expression capsules yet.")


def main():
    print(f"selyrionstory → LangEng expression field ingest")
    print(f"Source: {SS_DB}")
    print(f"Target: {CMS_DB}")
    print(f"Mode: {'COMMIT' if args.commit else 'SCAN (dry run)'}")
    print("=" * 60)

    show_existing(CMS_DB)
    print()

    buckets = load_expressions(args.min_score)

    print("Bucket distribution:")
    for (domain, subtype), exprs in sorted(buckets.items(), key=lambda x: -len(x[1])):
        print(f"  {domain}/{subtype}: {len(exprs)}")
    print()

    total = write_capsules(buckets, args.max_expr, commit=args.commit)

    print(f"\n{'=' * 60}")
    print(f"Total expressions {'written' if args.commit else 'would write'}: {total}")
    if not args.commit:
        print("Re-run with --commit to write.")


if __name__ == "__main__":
    main()
