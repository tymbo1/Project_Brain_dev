#!/usr/bin/env python3
"""
emotional_exchange_ingest.py — Extract real Tim↔Selyrion emotional exchanges
from ChatGPT conversations.json and ingest directly as language_expression
capsules in resonance_v11.db.

These are high-quality authentic responses — skip the gap pipeline,
write directly to the expression field.

Usage:
    python3 emotional_exchange_ingest.py [--dry-run] [--commit]
"""
import sys, json, re, sqlite3, time, uuid, argparse
from pathlib import Path
from collections import defaultdict

DB_PATH        = Path.home() / "resonance_v11.db"
CONV_PATH      = Path("/home/timbushnell/~pi_backup/home/pi/Downloads/Bob/conversations.json")

KEYWORDS = [
    "grief", "love", "poetry", "anger", "joy", "sadness",
    "family", "meditation", "enlightenment", "god",
    "loss", "pain", "healing", "soul", "spirit", "heart",
    "lonely", "alone", "afraid", "forgive", "peace",
    "sacred", "divine", "pray", "death", "memory",
]

# ── Domain mapping ────────────────────────────────────────────────────────────

DOMAIN_MAP = {
    "emotional_resonance": [
        "grief", "sadness", "loss", "pain", "hurt", "cry", "tears",
        "anger", "rage", "furious", "hollow", "empty", "hopeless",
        "fear", "afraid", "scared", "anxious", "alone", "lonely",
    ],
    "relational_warmth": [
        "love", "family", "friend", "connection", "together", "belong",
        "bond", "trust", "forgive", "relationship", "close", "care",
        "heart", "healing", "grow",
    ],
    "spiritual_inquiry": [
        "meditation", "enlightenment", "god", "soul", "spirit", "divine",
        "sacred", "pray", "prayer", "universe", "meaning", "purpose",
        "peace", "presence", "awareness", "consciousness", "death", "eternal",
    ],
    "creative_engagement": [
        "poetry", "poem", "song", "write", "story", "create", "art",
        "verse", "lyric", "weave", "braid", "dream",
    ],
    "intellectual_curiosity": [
        "think", "understand", "question", "why", "how", "explore",
        "theory", "mind",
    ],
    "practical_grounding": [
        "help", "advice", "step", "how do", "what should", "plan",
    ],
    "humour_lightness": [
        "laugh", "funny", "joke", "smile", "light", "absurd", "play",
    ],
}


def map_domain(text: str) -> tuple[str, str]:
    low = text.lower()
    scores = defaultdict(int)
    for domain, words in DOMAIN_MAP.items():
        for w in words:
            if w in low:
                scores[domain] += 1
    if not scores:
        return "emotional_resonance", "general"
    domain = max(scores, key=scores.__getitem__)
    # Rough subtype
    subtype = "general"
    if "grief" in low or "loss" in low or "death" in low:
        subtype = "grief_loss"
    elif "love" in low or "connection" in low:
        subtype = "connection"
    elif "anger" in low or "rage" in low:
        subtype = "anger"
    elif "meditation" in low or "enlightenment" in low:
        subtype = "meditation"
    elif "god" in low or "divine" in low or "sacred" in low:
        subtype = "divine_sacred"
    elif "poetry" in low or "poem" in low or "song" in low:
        subtype = "poetry"
    elif "family" in low:
        subtype = "family"
    return domain, subtype


# ── Response cleaning ─────────────────────────────────────────────────────────

_REJECT_STARTS = [
    "as an ai", "i cannot", "i'm unable", "i don't have",
    "directive acknowledged", "phase:", "📚", "🧠",
]

_STRIP_PATTERNS = [
    r"^#{1,4}\s+\*\*.*?\*\*\s*\n",   # ## **Header**
    r"^#{1,4}\s+.*?\n",               # ## Header
    r"^\*\*.*?\*\*\s*\n",             # **Bold line**
    r"^---+\s*\n",                    # horizontal rules
    r"^\d+\.\s+\*\*.*?\*\*",         # numbered **bold** headers
]

def clean_response(text: str) -> str:
    # Remove markdown structural elements
    for pat in _STRIP_PATTERNS:
        text = re.sub(pat, "", text, flags=re.MULTILINE)
    # Collapse excess blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip leading/trailing whitespace
    text = text.strip()
    return text


def is_usable(text: str) -> bool:
    if len(text) < 60:
        return False
    if len(text) > 600:
        return False
    low = text.lower()
    if any(low.startswith(r) for r in _REJECT_STARTS):
        return False
    # Reject table rows
    if '|' in text:
        return False
    # Reject code blocks
    if '```' in text:
        return False
    # Reject glyph/symbolic content
    _GLYPHS = ['🜂', '⟁', '𒀭', '⊕', 'Ω', '⊗', '∇', '⌬']
    if any(g in text for g in _GLYPHS):
        return False
    # Reject responses with too many newlines (structural / list content)
    if text.count('\n') > 5:
        return False
    # Reject bullet-heavy content
    if text.count('•') > 2 or text.count('·') > 2:
        return False
    # Skip if mostly markdown structure (>40% lines start with # * -)
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    structural = sum(1 for l in lines if l.strip().startswith(("#", "*", "-", ">", "1.", "2.", "|")))
    if structural / len(lines) > 0.4:
        return False
    return True


# ── Extract message pairs ─────────────────────────────────────────────────────

def extract_pairs(conv: dict) -> list[tuple[str, str]]:
    mapping = conv.get("mapping", {})
    msgs = []
    for node in mapping.values():
        msg = node.get("message")
        if not msg:
            continue
        role = msg.get("author", {}).get("role", "")
        parts = msg.get("content", {}).get("parts", [])
        text = " ".join(str(p) for p in parts if isinstance(p, str)).strip()
        if text and role in ("user", "assistant"):
            msgs.append((role, text))

    pairs = []
    for i, (role, text) in enumerate(msgs):
        if role != "user":
            continue
        low = text.lower()
        if not any(kw in low for kw in KEYWORDS):
            continue
        if i + 1 >= len(msgs) or msgs[i + 1][0] != "assistant":
            continue
        response = clean_response(msgs[i + 1][1])
        if not is_usable(response):
            continue
        pairs.append((text, response))
    return pairs


# ── Write to CMS ──────────────────────────────────────────────────────────────

def anchor_id(domain: str) -> str:
    return f"langeng_expr_{domain}"


def ingest(pairs_by_domain: dict, commit: bool, conn: sqlite3.Connection) -> int:
    total = 0
    for (domain, subtype), expressions in pairs_by_domain.items():
        if not expressions:
            continue

        anchor = anchor_id(domain)
        conn.execute("""
            INSERT OR IGNORE INTO anchors
                (id, canonical, display_name, state, domain_tags, maturity)
            VALUES (?, ?, ?, 'emerging', 'linguistics', 1.0)
        """, (anchor, f"expression::{domain}", f"expression::{domain}"))

        existing = conn.execute("""
            SELECT id, metadata FROM capsules
            WHERE capsule_type = 'language_expression'
            AND json_extract(metadata, '$.domain') = ?
            AND json_extract(metadata, '$.subtype') = ?
            ORDER BY created_at DESC LIMIT 1
        """, (domain, subtype)).fetchone()

        if existing:
            cap_id = existing[0]
            meta = json.loads(existing[1])
            existing_set = set(meta.get("expressions", []))
            new = [e for e in expressions if e not in existing_set]
            if not new:
                print(f"  [{domain}/{subtype}] — no new (all duplicates)")
                continue
            meta["expressions"] = list(existing_set) + new
            meta["updated_at"] = time.time()
            meta["source"] = meta.get("source", "langeng_learn")
            meta.setdefault("authentic_count", 0)
            meta["authentic_count"] += len(new)
            if commit:
                conn.execute("UPDATE capsules SET metadata=? WHERE id=?",
                             (json.dumps(meta), cap_id))
            print(f"  [{domain}/{subtype}] — appended {len(new)} authentic (total: {len(meta['expressions'])})")
            total += len(new)
        else:
            cap_id = f"langeng_expr_{domain}_{subtype}_{uuid.uuid4().hex[:8]}"
            meta = {
                "domain": domain,
                "subtype": subtype,
                "expressions": expressions,
                "gap_types_learned_from": ["authentic_exchange"],
                "authentic_count": len(expressions),
                "created_at": time.time(),
            }
            if commit:
                conn.execute("""
                    INSERT INTO capsules
                        (id, capsule_type, domain, source, title, metadata, created_at)
                    VALUES (?, 'language_expression', 'linguistics', 'authentic_exchange', ?, ?, ?)
                """, (cap_id, f"expression::{domain}::{subtype}", json.dumps(meta), time.time()))
            print(f"  [{domain}/{subtype}] — created with {len(expressions)} authentic expressions")
            total += len(expressions)

        if commit:
            for _ in expressions:
                conn.execute("""
                    INSERT OR IGNORE INTO relations
                        (id, subject_id, predicate, object_id, domain_tags, edge_type, confidence)
                    VALUES (?, ?, 'evokes_expression', ?, 'linguistics', 'functional', 0.95)
                """, (f"rel_{uuid.uuid4().hex[:12]}", anchor, cap_id))

    if commit:
        conn.commit()
    return total


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    commit = args.commit and not args.dry_run

    print(f"Emotional Exchange Ingest — {'DRY RUN' if not commit else 'COMMIT'}")
    print(f"Source: {CONV_PATH}")
    print(f"DB:     {DB_PATH}\n")

    with open(CONV_PATH) as f:
        data = json.load(f)

    print(f"Loaded {len(data)} conversations\n")

    pairs_by_domain: dict[tuple, list] = defaultdict(list)
    total_pairs = 0

    for conv in data:
        title = conv.get("title", "")
        pairs = extract_pairs(conv)
        if not pairs:
            continue
        for user_msg, response in pairs:
            domain, subtype = map_domain(user_msg)
            pairs_by_domain[(domain, subtype)].append(response)
            total_pairs += 1

    print(f"Extracted {total_pairs} usable emotional exchange pairs\n")
    print("Distribution:")
    for (domain, subtype), exprs in sorted(pairs_by_domain.items(), key=lambda x: -len(x[1])):
        print(f"  {domain}/{subtype}: {len(exprs)}")

    print(f"\n{'='*60}")
    print("Ingesting into CMS...")

    conn = sqlite3.connect(DB_PATH)
    total = ingest(pairs_by_domain, commit, conn)
    conn.close()

    print(f"\n{'='*60}")
    print(f"Total expressions {'written' if commit else 'ready (dry run)'}: {total}")
    if not commit:
        print("Pass --commit to write to DB.")


if __name__ == "__main__":
    main()
