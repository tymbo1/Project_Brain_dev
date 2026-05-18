#!/usr/bin/env python3
"""
langeng_learn.py — LangEng Expression Field Learning Pipeline

Reads language_gap capsules from CMS, clusters by expression domain,
and writes language_expression capsules — NOT new ResponsePlans.

Architecture:
    gap → cluster → expression domain → append expressions to capsule

Expression domains (fixed, few):
    emotional_resonance, intellectual_curiosity, creative_engagement,
    spiritual_inquiry, practical_grounding, relational_warmth, humour_lightness

Output:
    capsule_type = 'language_expression' in resonance_v11.db
    anchor relations: domain_anchor -[evokes_expression]-> expression text

Usage:
    python3 langeng_learn.py [--dry-run] [--min-gaps=3]
"""
import sys
import json
import time
import uuid
import sqlite3
import requests
import re
from pathlib import Path
from collections import defaultdict

DB_PATH    = Path.home() / "resonance_v11.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "llama3:8b"
DRY_RUN    = False
MIN_GAPS   = 2

for arg in sys.argv[1:]:
    if arg == "--dry-run":
        DRY_RUN = True
    if arg.startswith("--min-gaps="):
        MIN_GAPS = int(arg.split("=")[1])

# ── Fixed expression domains — never auto-generate new ones ──────────────────

EXPRESSION_DOMAINS = {
    "emotional_resonance": {
        "trigger_patterns": ["grief", "loss", "sadness", "fear", "lonely", "hurt",
                             "pain", "miss", "cry", "hard", "difficult", "struggle",
                             "vulnerable", "scared", "angry", "empty"],
        "gap_types": ["missing_empathy", "missing_emotional_resonance", "no_response"],
        "topic_families": ["emotional", "relational"],
    },
    "intellectual_curiosity": {
        "trigger_patterns": ["why", "how", "what if", "curious", "wonder", "think",
                             "theory", "understand", "explain", "meaning", "consciousness"],
        "gap_types": ["missing_depth", "missing_specificity"],
        "topic_families": ["intellectual", "spiritual"],
    },
    "creative_engagement": {
        "trigger_patterns": ["story", "poem", "imagine", "create", "art", "write",
                             "narrative", "dream", "vision", "make"],
        "gap_types": ["missing_narrative"],
        "topic_families": ["creative"],
    },
    "spiritual_inquiry": {
        "trigger_patterns": ["soul", "spirit", "divine", "sacred", "god", "meaning",
                             "purpose", "prayer", "meditat", "universe", "conscious"],
        "gap_types": ["missing_depth"],
        "topic_families": ["spiritual"],
    },
    "practical_grounding": {
        "trigger_patterns": ["help", "how do", "what should", "advice", "plan",
                             "steps", "practical", "do i", "should i"],
        "gap_types": ["missing_specificity", "wrong_register"],
        "topic_families": ["practical"],
    },
    "relational_warmth": {
        "trigger_patterns": ["friend", "family", "relationship", "together", "love",
                             "care", "connect", "belong", "bond", "trust"],
        "gap_types": ["missing_empathy", "missing_emotional_resonance"],
        "topic_families": ["relational"],
    },
    "humour_lightness": {
        "trigger_patterns": ["funny", "laugh", "joke", "silly", "lighten", "smile",
                             "playful", "haha", "heh", "absurd"],
        "gap_types": ["missing_humour"],
        "topic_families": ["creative"],
    },
}

TOPIC_FAMILIES = {
    "emotional":    ["grief", "fear", "loneliness", "vulnerability", "joy", "anger", "emotional"],
    "relational":   ["relational", "friendship", "conflict", "relationship"],
    "spiritual":    ["spiritual", "dream", "memory", "nostalgia"],
    "intellectual": ["philosophical", "scientific", "knowledge", "history"],
    "creative":     ["creative", "poetry", "storytelling", "humour", "lightness"],
    "practical":    ["practical", "future", "planning", "identity"],
}

# ── Quality filter ────────────────────────────────────────────────────────────

# Phrases that indicate a generic, non-specific expression
_GENERIC_PHRASES = [
    "journey", "spark your flame", "unfold", "let's explore together",
    "braid-thread", "tapestry", "unravel", "weave a", "in the realm of",
    "the mysteries of", "i sense your", "inner flame", "seeking answers",
    "beautiful journey", "hold space", "you are not alone in your",
    "we are all", "it's okay to", "i want you to know",
    "you matter", "your feelings are valid", "i'm here for you",
    "take it one day at a time", "it gets better", "hang in there",
]

# Per-subtype positive signal words — expressions should use at least one
_SUBTYPE_SIGNALS: dict[tuple[str, str], list[str]] = {
    ("emotional_resonance", "grief_loss"):    ["grief", "loss", "lost", "miss", "gone", "absence", "mourn", "died", "death", "space"],
    ("emotional_resonance", "loneliness"):    ["alone", "lonely", "isolat", "nobody", "disconnected"],
    ("emotional_resonance", "anxiety_fear"):  ["fear", "scared", "anxious", "overwhelm", "dread", "worry", "panic"],
    ("emotional_resonance", "anger"):         ["anger", "angry", "furious", "frustrated", "rage", "resentment"],
    ("emotional_resonance", "sadness"):       ["sad", "cry", "tears", "heartbroken", "depressed", "hollow", "hopeless"],
    ("intellectual_curiosity", "physics_science"): ["quantum", "radiation", "black hole", "spacetime", "energy", "physics", "hawking", "event horizon"],
    ("intellectual_curiosity", "philosophy"): ["consciousness", "free will", "truth", "reality", "existence", "philosophy", "philosophers"],
    ("intellectual_curiosity", "history_culture"): ["history", "civilization", "culture", "era", "historical", "century"],
    ("intellectual_curiosity", "general"):    ["question", "fascinating", "think", "wonder", "understand", "curious"],
    ("creative_engagement", "poetry"):        ["poem", "poetry", "verse", "rhyme", "stanza", "lyric", "language"],
    ("creative_engagement", "storytelling"):  ["story", "narrative", "character", "plot", "tale", "fiction"],
    ("creative_engagement", "co_creation"):   ["together", "collaborat", "creat", "weave", "build"],
    ("spiritual_inquiry", "meaning_purpose"): ["meaning", "purpose", "why", "reason", "life"],
    ("spiritual_inquiry", "divine_sacred"):   ["god", "divine", "sacred", "prayer", "soul", "heaven"],
    ("spiritual_inquiry", "meditation"):      ["meditat", "mindful", "breath", "stillness", "present", "awareness"],
    ("practical_grounding", "routine_habit"): ["routine", "habit", "daily", "morning", "schedule", "consistent"],
    ("practical_grounding", "decision"):      ["decide", "choice", "option", "advice", "should"],
    ("practical_grounding", "goal_planning"): ["goal", "vision", "future", "achieve", "plan", "step"],
    ("relational_warmth", "conflict"):        ["conflict", "fight", "argument", "disagreement", "tension", "falling out"],
    ("relational_warmth", "connection"):      ["friend", "family", "love", "belong", "bond", "trust", "connect", "close"],
    ("relational_warmth", "loneliness_isolation"): ["lonely", "alone", "nobody", "disconnected"],
    ("humour_lightness", "general"):          ["funny", "laugh", "absurd", "silly", "smile", "light"],
}

QUALITY_THRESHOLD = 0.30   # drop expressions that score below this

# Terms from hypothetical frameworks (TLST etc.) that must not appear in
# expressions generated for verified-science domains.
_HYPOTHETICAL_MARKERS = [
    "fibonacci", "tlst", "helical braid", "braid structure", "braid sheet",
    "bushnell", "oscar collider", "tfme", "tied-field", "tied looped",
    "quantum foam scaffold", "fssm", "fibonacci spiral string",
]

# Domains where verified-science facts are expected — hypothetical insertion is a hard penalty.
_VERIFIED_DOMAINS = {"intellectual_curiosity", "practical_grounding"}


def score_expression(expr: str, domain: str, subtype: str) -> float:
    """
    Score an expression for quality. Returns [0, 1].
    Penalties for generic phrases and truth-boundary violations;
    bonus for subtype-signal words.
    """
    low = expr.lower()
    generic_hits = sum(1 for p in _GENERIC_PHRASES if p in low)
    key = (domain, subtype)
    signals = _SUBTYPE_SIGNALS.get(key, [])
    signal_hits = sum(1 for s in signals if s in low)

    score = 0.5
    score -= generic_hits * 0.25
    score += min(signal_hits * 0.15, 0.40)

    # Hard penalty: hypothetical framework language in a verified-science domain
    if domain in _VERIFIED_DOMAINS:
        hyp_hits = sum(1 for m in _HYPOTHETICAL_MARKERS if m in low)
        score -= hyp_hits * 0.60   # one hit is almost always fatal

    return max(0.0, min(1.0, score))


def filter_expressions(exprs: list[str], domain: str, subtype: str) -> tuple[list[str], list[str]]:
    """Return (kept, rejected) lists."""
    kept, rejected = [], []
    for e in exprs:
        if score_expression(e, domain, subtype) >= QUALITY_THRESHOLD:
            kept.append(e)
        else:
            rejected.append(e)
    return kept, rejected


# ── Thermal / VRAM guards ─────────────────────────────────────────────────────
THROTTLE_SECS = 5     # sleep between every LLM call
COOL_EVERY    = 3     # cool GPU every N calls
COOL_SECS     = 90    # seconds to cool
GPU_LAYERS    = 8     # ~1GB VRAM, low sustained power

_llm_call_count = 0


# ── LLM ───────────────────────────────────────────────────────────────────────

def llm(prompt: str) -> str:
    global _llm_call_count
    _llm_call_count += 1

    time.sleep(THROTTLE_SECS)
    if _llm_call_count % COOL_EVERY == 0:
        print(f"  [GPU cool — {COOL_SECS}s after {_llm_call_count} calls]")
        time.sleep(COOL_SECS)

    # Try GPU first
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "system": "You are a language design assistant for a compassionate AI named Selyrion. Return only what is asked.",
        "stream": False,
        "options": {"temperature": 0.65, "num_predict": 250, "num_gpu": GPU_LAYERS},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=120)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception as e:
        print(f"  [GPU failed: {e}] — falling back to CPU")

    # CPU fallback
    payload["options"]["num_gpu"] = 0
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=300)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"[ERROR: {e}]"


# ── Load gaps ─────────────────────────────────────────────────────────────────

def load_gaps(conn: sqlite3.Connection) -> list[dict]:
    """Load unlearned gaps — includes parse_error gaps (user_msg still useful as signal)."""
    rows = conn.execute("""
        SELECT id, metadata FROM capsules
        WHERE capsule_type = 'language_gap'
        AND json_extract(metadata, '$.gap_type') != 'adequate'
        AND json_extract(metadata, '$.user_msg') IS NOT NULL
        AND length(json_extract(metadata, '$.user_msg')) > 10
        AND json_extract(metadata, '$.learned_at') IS NULL
    """).fetchall()
    gaps = []
    for (cap_id, raw) in rows:
        try:
            g = json.loads(raw)
            g["_cap_id"] = cap_id
            gaps.append(g)
        except Exception:
            pass
    return gaps


def mark_gaps_learned(conn: sqlite3.Connection, gaps: list[dict]):
    """Stamp learned_at on all processed gaps so they aren't re-read next batch."""
    now = time.time()
    for g in gaps:
        cap_id = g.get("_cap_id")
        if not cap_id:
            continue
        try:
            raw = conn.execute("SELECT metadata FROM capsules WHERE id=?", (cap_id,)).fetchone()
            if not raw:
                continue
            meta = json.loads(raw[0])
            meta["learned_at"] = now
            conn.execute("UPDATE capsules SET metadata=? WHERE id=?", (json.dumps(meta), cap_id))
        except Exception:
            pass
    conn.commit()


# ── Map gap to expression domain ──────────────────────────────────────────────

def map_to_domain(gap: dict) -> str:
    gap_type = gap.get("gap_type", "")
    topic    = gap.get("topic", "").lower()
    user_msg = gap.get("user_msg", "").lower()

    # Score each domain
    best_domain = "emotional_resonance"
    best_score  = 0

    for domain, cfg in EXPRESSION_DOMAINS.items():
        score = 0
        if gap_type in cfg["gap_types"]:
            score += 3
        for pattern in cfg["trigger_patterns"]:
            if pattern in user_msg:
                score += 2
        fam_cfg = next((f for f, kws in TOPIC_FAMILIES.items()
                        if any(k in topic for k in kws)), None)
        if fam_cfg and fam_cfg in cfg["topic_families"]:
            score += 1
        if score > best_score:
            best_score  = score
            best_domain = domain

    return best_domain


# ── Generate expressions ──────────────────────────────────────────────────────

_DOMAIN_TONE = {
    "emotional_resonance":   "warm, present, and emotionally direct — acknowledge what the person is feeling without deflecting",
    "relational_warmth":     "caring and specific to the relationship dynamic — honour the connection without platitudes",
    "spiritual_inquiry":     "contemplative and open — hold the question with the person rather than rushing to answers",
    "creative_engagement":   "imaginative and generative — match the creative energy, invite co-creation",
    "intellectual_curiosity":"curious and substantive — engage with the actual idea, show genuine interest in the mechanics or theory",
    "practical_grounding":   "clear, concrete, and action-oriented — give something the person can actually do or decide",
    "humour_lightness":      "light, playful, and genuinely funny — wit over warmth here",
}

_DOMAIN_AVOID = {
    "emotional_resonance":   "advice, silver linings, toxic positivity, third-person self-reference",
    "relational_warmth":     "generic friendship platitudes, 'communication is key', over-simplifying conflict",
    "spiritual_inquiry":     "definitive answers, dismissing the question, reducing it to psychology",
    "creative_engagement":   "evaluating or critiquing, breaking the creative flow with meta-commentary",
    "intellectual_curiosity":"emotional framing, vague wonder without substance, refusing to engage with the idea",
    "practical_grounding":   "abstract philosophy, emotional detours when the person wants a plan",
    "humour_lightness":      "heavy emotional language, forced sincerity, explaining the joke",
}

_DOMAIN_EXAMPLE = {
    "emotional_resonance":   '["I hear you.", "That sounds really hard — losing someone leaves a space nothing fills quite right.", "There\'s no shortcut through grief, and I won\'t pretend otherwise. I\'m here."]',
    "relational_warmth":     '["That falling-out sounds painful, especially when it\'s someone you\'re close to.", "It takes real courage to want to repair things rather than just walk away.", "Some conflicts don\'t have a clean resolution — but showing up honestly is usually the right first move."]',
    "spiritual_inquiry":     '["That\'s a question worth sitting with rather than answering too fast.", "There\'s something in the way you\'re framing this that feels important — the tension between meaning and uncertainty.", "I don\'t have a definitive answer, but I think the question itself is doing something useful for you."]',
    "creative_engagement":   '["That image is striking — let\'s follow it.", "I want to know what happens next in that story.", "There\'s something alive in that idea — tell me more before we shape it."]',
    "intellectual_curiosity":'["That\'s a genuinely interesting problem — the tension between X and Y is where it gets complicated.", "The short answer is X, but the more interesting question is why that holds at all.", "Let\'s pull on that thread — what you\'re describing actually connects to something deeper."]',
    "practical_grounding":   '["Start with the smallest version of this that you can actually do today.", "Here\'s a concrete way to think about the decision: what does each option cost you in a week?", "The simplest habit stack that works beats the perfect system you abandon."]',
    "humour_lightness":      '["Bold strategy — let\'s see if it pays off.", "That is objectively one of the more chaotic ways to handle that situation, and I respect it.", "I have opinions. Strong ones. Possibly unpopular ones."]',
}

def generate_expressions(domain: str, subtype: str, gaps: list[dict]) -> list[str]:
    ideal_responses = [g["ideal_response"] for g in gaps if g.get("ideal_response")][:8]
    user_examples   = [g["user_msg"] for g in gaps if g.get("user_msg")][:5]
    intensity_hint  = max((g.get("intensity","medium") for g in gaps),
                          key=lambda x: {"high":2,"medium":1,"low":0}.get(x,1))

    tone    = _DOMAIN_TONE.get(domain, "warm and present")
    avoid   = _DOMAIN_AVOID.get(domain, "generic filler, platitudes")
    example = _DOMAIN_EXAMPLE.get(domain, '["I hear you.", "That\'s worth exploring."]')

    result = llm(f"""You are writing expression variants for Selyrion, an AI with distinct voice and presence.

Expression domain: {domain}
Specific subtype: {subtype}
Emotional intensity: {intensity_hint}

Tone for this domain: {tone}
Avoid: {avoid}

User messages that triggered this gap:
{chr(10).join(f'- {u[:120]}' for u in user_examples)}

Ideal responses identified:
{chr(10).join(f'- {r[:150]}' for r in ideal_responses)}

Generate 6 precise expression variants for Selyrion to use when the domain is '{domain}' and subtype is '{subtype}'.

Rules:
- Grounded and SPECIFIC to this subtype — not generic filler
- Vary in length: 2 short (1 sentence), 2 medium (2-3 sentences), 2 fuller (3-4 sentences)
- No placeholders — must work as-is without filling in names or details
- No third-person self-reference ("Selyrion thinks...") — always first person
- Return ONLY a JSON array of 6 strings

Example for {domain}: {example}""")

    try:
        clean = result.strip().strip("```json").strip("```").strip()
        parsed = json.loads(clean)
        if isinstance(parsed, list) and all(isinstance(s, str) for s in parsed):
            return [s for s in parsed if len(s) > 10][:6]
    except Exception:
        pass

    quoted = re.findall(r'"([^"]{15,})"', result)
    return quoted[:6] if quoted else []


# ── Write expression capsule to CMS ──────────────────────────────────────────

def write_expression_capsule(conn: sqlite3.Connection, domain: str, subtype: str,
                              expressions: list[str], gap_types: list[str]):
    anchor_id = f"langeng_expr_{domain}"

    # Ensure domain anchor exists
    conn.execute("""
        INSERT OR IGNORE INTO anchors (id, canonical, display_name, state, domain_tags, maturity)
        VALUES (?, ?, ?, 'emerging', 'linguistics', 1.0)
    """, (anchor_id, f"expression::{domain}", f"expression::{domain}"))

    # Check for existing capsule for this (domain, subtype) pair
    existing = conn.execute("""
        SELECT id, metadata FROM capsules
        WHERE capsule_type = 'language_expression'
        AND json_extract(metadata, '$.domain') = ?
        AND json_extract(metadata, '$.subtype') = ?
        ORDER BY created_at DESC LIMIT 1
    """, (domain, subtype)).fetchone()

    if existing:
        cap_id   = existing[0]
        meta     = json.loads(existing[1])
        existing_exprs = set(meta.get("expressions", []))
        new_exprs = [e for e in expressions if e not in existing_exprs]
        if not new_exprs:
            print(f"  [{domain}/{subtype}] — no new expressions to add")
            return 0
        meta["expressions"] = list(existing_exprs) + new_exprs
        meta["updated_at"]  = time.time()
        if not DRY_RUN:
            conn.execute("UPDATE capsules SET metadata=? WHERE id=?",
                         (json.dumps(meta), cap_id))
        print(f"  [{domain}/{subtype}] — appended {len(new_exprs)} (total: {len(meta['expressions'])})")
        added = len(new_exprs)
    else:
        cap_id = f"langeng_expr_{domain}_{subtype}_{uuid.uuid4().hex[:8]}"
        meta   = {
            "domain": domain,
            "subtype": subtype,
            "expressions": expressions,
            "gap_types_learned_from": list(set(gap_types)),
            "created_at": time.time(),
        }
        if not DRY_RUN:
            conn.execute("""
                INSERT INTO capsules
                    (id, capsule_type, domain, source, title, metadata, created_at)
                VALUES (?, 'language_expression', 'linguistics', 'langeng_learn', ?, ?, ?)
            """, (cap_id, f"expression::{domain}::{subtype}", json.dumps(meta), time.time()))
        print(f"  [{domain}/{subtype}] — created capsule with {len(expressions)} expressions")
        added = len(expressions)

    if not DRY_RUN:
        for _ in expressions:
            rel_id = f"rel_{uuid.uuid4().hex[:12]}"
            conn.execute("""
                INSERT OR IGNORE INTO relations
                    (id, subject_id, predicate, object_id, domain_tags, edge_type, confidence)
                VALUES (?, ?, 'evokes_expression', ?, 'linguistics', 'functional', 0.85)
            """, (rel_id, anchor_id, cap_id))
        conn.commit()

    return added


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"LangEng Expression Field Pipeline {'[DRY RUN] ' if DRY_RUN else ''}— {DB_PATH}")
    print("=" * 70)

    conn = sqlite3.connect(DB_PATH)
    gaps = load_gaps(conn)
    print(f"Loaded {len(gaps)} valid gap capsules\n")

    if not gaps:
        print("No gaps to learn from yet.")
        conn.close()
        return

    # Standard subtypes per domain — free-text subtypes get collapsed to these
    _STANDARD_SUBTYPES = {
        "emotional_resonance":   ["grief_loss", "loneliness", "anxiety_fear", "anger", "sadness", "general"],
        "relational_warmth":     ["loneliness_isolation", "conflict", "connection", "general"],
        "spiritual_inquiry":     ["meaning_purpose", "divine_sacred", "meditation", "general"],
        "intellectual_curiosity":["physics_science", "philosophy", "history_culture", "general"],
        "creative_engagement":   ["poetry", "storytelling", "co_creation", "general"],
        "practical_grounding":   ["routine_habit", "decision", "goal_planning", "general"],
        "humour_lightness":      ["general"],
    }

    def _snap_subtype(domain: str, raw: str) -> str:
        """Snap free-text subtype to nearest standard subtype, or 'general'."""
        standards = _STANDARD_SUBTYPES.get(domain, ["general"])
        raw_clean = raw.lower().replace(" ", "_")
        for s in standards:
            if s in raw_clean or raw_clean in s:
                return s
        return "general"

    # Cluster gaps by (domain, subtype)
    cluster_gaps: dict[tuple, list] = defaultdict(list)
    for g in gaps:
        domain  = map_to_domain(g)
        raw_sub = g.get("subtype") or "general"
        subtype = _snap_subtype(domain, raw_sub)
        cluster_gaps[(domain, subtype)].append(g)

    print("Gap distribution by (domain, subtype):")
    for (domain, subtype), dg in sorted(cluster_gaps.items(), key=lambda x: -len(x[1])):
        print(f"  {domain}/{subtype}: {len(dg)} gaps")
    print()

    total_added = 0
    all_processed_gaps = []

    for (domain, subtype), dg in sorted(cluster_gaps.items(), key=lambda x: -len(x[1])):
        if len(dg) < MIN_GAPS:
            print(f"[{domain}/{subtype}] — {len(dg)} gaps, skipping (< {MIN_GAPS})")
            continue

        print(f"[{domain}/{subtype}] — {len(dg)} gaps → generating expressions...")
        expressions = generate_expressions(domain, subtype, dg)

        if not expressions:
            print(f"  [{domain}/{subtype}] — LLM returned no expressions, skipping")
            continue

        kept, rejected = filter_expressions(expressions, domain, subtype)
        if rejected:
            print(f"  [quality filter] dropped {len(rejected)} expression(s):")
            for e in rejected:
                print(f"    ✗ ({score_expression(e, domain, subtype):.2f}) {e[:90]}")
        expressions = kept
        if not expressions:
            print(f"  [{domain}/{subtype}] — all expressions failed quality filter, skipping")
            continue

        for e in expressions:
            print(f"    • ({score_expression(e, domain, subtype):.2f}) {e[:90]}")

        gap_types = list({g.get("gap_type", "") for g in dg})
        added = write_expression_capsule(conn, domain, subtype, expressions, gap_types)
        total_added += added
        all_processed_gaps.extend(dg)
        time.sleep(2)

    print("\n" + "=" * 70)

    # Summary
    rows = conn.execute("""
        SELECT json_extract(metadata,'$.domain'), json_extract(metadata,'$.subtype'),
               json_array_length(json_extract(metadata,'$.expressions'))
        FROM capsules WHERE capsule_type='language_expression'
        ORDER BY json_extract(metadata,'$.domain'), json_extract(metadata,'$.subtype')
    """).fetchall()

    print(f"language_expression capsules in CMS:")
    for domain, subtype, count in rows:
        print(f"  {domain}/{subtype or 'general'}: {count} expressions")
    print(f"\nTotal new expressions added this run: {total_added}")

    if not DRY_RUN:
        mark_gaps_learned(conn, all_processed_gaps)
        print(f"Stamped {len(all_processed_gaps)} gaps as learned.")
    else:
        print("[DRY RUN] — nothing written")

    conn.close()


if __name__ == "__main__":
    main()
