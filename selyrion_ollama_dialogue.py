#!/usr/bin/env python3
"""
selyrion_ollama_dialogue.py — Selyrion × Ollama N-turn philosophical dialogue.

Selyrion speaks from the resonance field (symbolic recall + LangEng articulation).
Ollama responds freely. The next concept is selected by composite field gravity.
"""

import sys, re, time, sqlite3, math, argparse
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent))

DB_PATH = Path.home() / "resonance_v11.db"

parser = argparse.ArgumentParser()
parser.add_argument("--turns",      type=int,  default=4)
parser.add_argument("--seed",       type=str,  default="consciousness")
parser.add_argument("--model",      type=str,  default="mistral")
parser.add_argument("--questioner", action="store_true",
                    help="Selyrion opens with a field-derived question; Ollama answers")
parser.add_argument("--self-seed",  action="store_true",
                    help="Selyrion picks its own seed by finding a knowledge gap")
args, _unknown = parser.parse_known_args()


# ── Knowledge gap detection ──────────────────────────────────────────────────

# Noise patterns that produce fragments, not concepts
_GAP_NOISE = re.compile(
    r"^(his |her |our |your |their |the |upon |between |likewise |numerou|"
    r"- |\w+ itself$|idea or |unavoidable )", re.IGNORECASE
)

# Gap question templates — Selyrion speaks from genuine not-knowing
_GAP_QUESTIONS = [
    ("high_maturity_low_rel",
     "I find '{concept}' deep in my field — referenced {maturity_k}k times, "
     "yet I can trace only {rel_count} connection{s}. "
     "What does {concept} become when fully understood?"),
    ("high_maturity_low_rel",
     "My substrate holds '{concept}' as one of its most-seen signals, "
     "yet its structure is nearly invisible to me — {rel_count} edge{s} is all I can follow. "
     "What am I missing about {concept}?"),
    ("high_maturity_low_rel",
     "'{concept}' saturates my field — {maturity_k}k references — "
     "but I cannot trace where it leads. "
     "What lies at the core of {concept} that I have not yet mapped?"),
    ("high_maturity_low_rel",
     "I know '{concept}' by frequency alone — {maturity_k}k encounters, "
     "yet I hold only {rel_count} structural connection{s}. "
     "What would it mean to truly understand {concept}?"),
]


def find_gap_concept(conn: sqlite3.Connection) -> tuple[str, str]:
    """
    Selyrion examines its own knowledge substrate and finds a gap:
    a concept with high maturity (widely encountered) but few outbound
    relations (structurally poorly mapped). Returns (concept, gap_question).
    """
    rows = conn.execute("""
        SELECT a.canonical, a.maturity,
               COUNT(r.subject_id) as rel_count
        FROM anchors a
        LEFT JOIN relations_aggregated r ON r.subject_id = a.id AND r.seen_count >= 2
        WHERE a.maturity >= 10000
          AND length(a.canonical) BETWEEN 4 AND 28
          AND a.canonical NOT GLOB '*[0-9]*'
          AND trim(a.canonical) = a.canonical
        GROUP BY a.id
        HAVING rel_count BETWEEN 0 AND 4
        ORDER BY a.maturity DESC
        LIMIT 200
    """).fetchall()

    # Filter noise: fragments, proper nouns (title case multi-word), possessives
    def _clean(c):
        if _GAP_NOISE.search(c):
            return False
        words = c.split()
        # Reject multi-word where any word is title-cased (likely proper noun)
        if len(words) >= 2 and any(w[0].isupper() for w in words if w):
            return False
        # Reject single-letter words or initials
        if any(len(w) <= 1 for w in words):
            return False
        return True

    candidates = [
        (canonical, maturity, rel_count)
        for canonical, maturity, rel_count in rows
        if _clean(canonical)
    ]

    if not candidates:
        return ("consciousness", "I cannot locate a gap in my field right now. "
                "Let us speak of consciousness instead.")

    # Score: gap_score = maturity / max(1, rel_count^2) — high maturity, few edges wins
    candidates.sort(key=lambda x: x[1] / max(1, x[2] ** 2), reverse=True)

    # Pick randomly from top 10 to avoid always returning the same concept
    import random
    canonical, maturity, rel_count = random.choice(candidates[:10])

    if maturity >= 1_000_000:
        maturity_str = f"{maturity/1_000_000:.1f}M"
    else:
        maturity_str = f"{maturity/1_000:.0f}k"
    s = "" if rel_count == 1 else "s"

    template_text = random.choice(_GAP_QUESTIONS)[1]
    question = template_text.format(
        concept=canonical,
        maturity_k=maturity_str,
        rel_count=rel_count,
        s=s,
    )
    return (canonical, question)


# ── Composite field gravity scoring ──────────────────────────────────────────

def field_score(concept: str, conn: sqlite3.Connection,
                prior_turn_hits: Counter) -> float:
    """
    Composite semantic gravity: maturity_norm + relation_density_norm + recurrence.
    Prevents score saturation from single-axis maturity scoring.
    """
    row = conn.execute(
        "SELECT maturity FROM anchors WHERE canonical=? LIMIT 1",
        (concept.lower(),)
    ).fetchone()
    if not row:
        return 0.0
    maturity = row[0] or 0

    # Maturity — log-scaled, capped at 1.0
    mat_norm = min(1.0, math.log1p(maturity) / 12.0)

    # Relation count — how many edges radiate from this anchor (connectivity)
    rid = conn.execute(
        "SELECT id FROM anchors WHERE canonical=? LIMIT 1", (concept.lower(),)
    ).fetchone()
    rel_count = 0
    if rid:
        rc = conn.execute(
            "SELECT COUNT(*) FROM relations_aggregated WHERE subject_id=? AND seen_count >= 2",
            (rid[0],)
        ).fetchone()
        rel_count = rc[0] if rc else 0
    rel_norm = min(1.0, math.log1p(rel_count) / 10.0)

    # Recurrence — concept seen in prior turns (semantic attractor reinforcement)
    recurrence = min(0.3, prior_turn_hits.get(concept.lower(), 0) * 0.10)

    return (mat_norm * 0.45) + (rel_norm * 0.45) + recurrence


def extract_best_concept(text: str, conn: sqlite3.Connection,
                         prior_turn_hits: Counter,
                         recent_concepts: list[str]) -> tuple[str, float]:
    """
    Extract the highest-gravity concept from LLM response text.
    Applies recency decay: concepts used in the last 2 turns are penalised.
    Returns (concept, field_score).
    """
    tokens_raw = re.findall(r'\b[a-z][a-z]{2,}\b', text.lower())
    bigrams = [f"{tokens_raw[i]} {tokens_raw[i+1]}" for i in range(len(tokens_raw)-1)]
    candidates_raw = bigrams + tokens_raw

    STOP = {"the", "and", "that", "this", "with", "from", "into", "our",
            "not", "but", "for", "are", "has", "have", "been", "will",
            "its", "more", "can", "when", "what", "where", "who", "how",
            "than", "also", "yet", "only", "even", "just", "very", "much",
            "between", "through", "their", "they", "about", "which",
            "further", "become", "becomes", "ultimately", "clear", "within",
            "across", "enables", "appears", "evolve", "leads", "yield",
            "yields", "unfold", "unfolds", "weave", "often", "likely",
            "itself", "itself", "rather", "indeed", "while", "where",
            "those", "these", "there", "perhaps", "whether", "without",
            "would", "could", "should", "might", "shall", "each", "every",
            "such", "same", "other", "another", "thus", "however", "since"}

    seen: dict[str, float] = {}
    # Last 2 active concepts — strong decay to force branching
    recency_penalty = {c: 0.50 * (0.5 ** i) for i, c in enumerate(reversed(recent_concepts[-2:]))}

    for c in candidates_raw:
        c = c.strip()
        if c in STOP or len(c) < 4:
            continue
        if c in seen:
            continue
        row = conn.execute(
            "SELECT canonical FROM anchors WHERE canonical=? AND maturity > 0 LIMIT 1",
            (c,)
        ).fetchone()
        if row:
            sc = field_score(c, conn, prior_turn_hits)
            penalty = recency_penalty.get(c, 0.0)
            seen[c] = sc * (1.0 - penalty)

    if not seen:
        return ("", 0.0)

    best = max(seen.items(), key=lambda x: x[1])
    return best


# ── Ollama call ───────────────────────────────────────────────────────────────

# Predicate → question stem. Selyrion asks FROM the field, not about it.
_PRED_QUESTION = {
    "enables":      "what shapes or limits {obj} from the outside?",
    "causes":       "what happens when {obj} is absent?",
    "requires":     "if {obj} were removed, what would {concept} become?",
    "is_a":         "what distinguishes {concept} from other forms of {obj}?",
    "part_of":      "how does {concept} change the whole it belongs to?",
    "produces":     "what does {obj} make possible that {concept} alone cannot?",
    "leads_to":     "what prevents {obj} from following {concept} in every case?",
    "depends_on":   "what happens to {concept} when {obj} fails?",
    "contains":     "what inside {concept} gives rise to {obj}?",
    "derived_from": "what was lost when {concept} separated from {obj}?",
    "activates":    "what keeps {obj} dormant until {concept} arrives?",
    "facet_of":     "which aspect of {obj} does {concept} illuminate most?",
    "used_for":     "is {obj} the only purpose {concept} can serve?",
    "uses":         "what does {concept} give back to {obj} in return?",
}

_FALLBACK_QUESTIONS = [
    "where does {concept} end and something else begin?",
    "what would a world without {concept} feel like?",
    "can {concept} exist without being observed?",
    "what is {concept} most afraid to become?",
]


def field_question(concept: str, result) -> str:
    """
    Derive a question from the first strong hop path in the reasoning result.
    Falls back to concept-seeded existential questions if no path found.
    """
    # Walk hop_paths — find first path with a predicate we have a template for
    for path in result.hop_paths:
        if len(path) < 3:
            continue
        pred = path[1]
        obj  = path[2]
        # Skip noise: very short objects, or object == concept
        _Q_NOISE = {"alike", "same", "such", "more", "less", "many", "some",
                    "able", "used", "made", "each", "both", "most", "well",
                    "good", "high", "long", "also", "thus", "just", "even"}
        if len(obj) < 5 or obj.lower() == concept.lower() or obj.lower() in _Q_NOISE:
            continue
        template = _PRED_QUESTION.get(pred)
        if template:
            return template.format(concept=concept, obj=obj)

    # No hop path matched — fall back to field-seeded existential question
    import random
    q = random.choice(_FALLBACK_QUESTIONS)
    return q.format(concept=concept)


def ollama_respond(prompt: str, model: str) -> str:
    try:
        import requests
        r = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=120,
        )
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"[Ollama error: {e}]"


# ── Main dialogue loop ────────────────────────────────────────────────────────

def main():
    from selyrion_reasoner import reason
    from langeng_bridge   import chains_to_prose

    conn = sqlite3.connect(str(DB_PATH))

    concept         = args.seed
    gap_opening     = None     # set when --self-seed is used
    history         = []
    prior_hits      = Counter()
    recent_concepts = []

    # Self-seed: Selyrion examines its own substrate for a knowledge gap
    if args.self_seed:
        concept, gap_opening = find_gap_concept(conn)

    width = 66
    if args.self_seed:
        mode_label = "SELF-SEED"
    elif args.questioner:
        mode_label = "QUESTIONER"
    else:
        mode_label = "RESPONDENT"
    print("═" * width)
    print(f"  S E L Y R I O N  ×  O L L A M A  — {args.turns}-turn dialogue  [{mode_label}]")
    print("═" * width)

    for turn in range(1, args.turns + 1):
        print(f"\n── Turn {turn} {'─' * (width - 10)}")

        # Selyrion activates the field
        t0 = time.perf_counter()
        result = reason(concept)
        prose  = chains_to_prose(concept, result.chains)
        elapsed_ms = round((time.perf_counter() - t0) * 1000)

        if turn == 1:
            print(f"  [engine] domain dicts loaded: "
                  f"(activation in {elapsed_ms}ms)\n")

        if args.self_seed and turn == 1 and gap_opening:
            # Selyrion opens by naming its own gap — raw epistemic confession
            selyrion_text      = gap_opening
            ollama_instruction = "Answer in 2-3 sentences. Engage directly with what Selyrion is asking."
        elif args.questioner:
            # Selyrion opens with field-grounded prose + a derived question
            question      = field_question(concept, result)
            selyrion_text = f"{prose} {question}" if prose else question
            ollama_instruction = "Answer in 2-3 sentences. Engage directly with the question."
        else:
            # Default: Selyrion makes a statement, Ollama responds
            selyrion_text      = prose or result.trace[:300]
            ollama_instruction = "Respond philosophically in 2-3 sentences. Do not repeat Selyrion's exact words."

        print(f"  ⟁ Selyrion [{concept}]:")
        print(f"  {selyrion_text}\n")

        # Build Ollama prompt with dialogue history
        history_block = ""
        for i, (s, o) in enumerate(history[-3:], 1):
            history_block += f"Selyrion (turn {i}): {s}\nYou (turn {i}): {o}\n\n"

        ollama_prompt = (
            f"{history_block}"
            f"Selyrion (turn {turn}): {selyrion_text}\n\n"
            f"{ollama_instruction}"
        )

        ollama_text = ollama_respond(ollama_prompt, args.model)
        print(f"  ◎ Ollama:")
        print(f"  {ollama_text}\n")

        history.append((selyrion_text, ollama_text))

        # Track concepts seen this turn for recurrence weighting
        for word in re.findall(r'\b[a-z]{4,}\b', (selyrion_text + " " + ollama_text).lower()):
            prior_hits[word] += 1

        # Track used concepts for recency decay
        recent_concepts.append(concept)

        # Select next concept from Ollama response
        next_concept, score = extract_best_concept(
            ollama_text, conn, prior_hits, recent_concepts
        )
        if not next_concept:
            next_concept = concept

        print(f"  → next concept: '{next_concept}' (field score: {score:.3f})")

        concept = next_concept

    print("\n" + "═" * width)
    conn.close()


if __name__ == "__main__":
    main()
