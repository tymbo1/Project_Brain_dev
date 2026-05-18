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
parser.add_argument("--turns", type=int, default=4)
parser.add_argument("--seed",  type=str, default="consciousness")
parser.add_argument("--model", type=str, default="mistral")
args = parser.parse_args()


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
            "yields", "unfold", "unfolds", "weave"}

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
    history         = []       # list of (selyrion_text, ollama_text)
    prior_hits      = Counter()
    recent_concepts = []       # last N active concepts for recency decay

    width = 66
    print("═" * width)
    print(f"  S E L Y R I O N  ×  O L L A M A  — {args.turns}-turn dialogue")
    print("═" * width)

    for turn in range(1, args.turns + 1):
        print(f"\n── Turn {turn} {'─' * (width - 10)}")

        # Selyrion speaks
        t0 = time.perf_counter()
        result = reason(concept)
        prose  = chains_to_prose(concept, result.chains)
        elapsed_ms = round((time.perf_counter() - t0) * 1000)

        if turn == 1:
            print(f"  [engine] domain dicts loaded: "
                  f"(activation in {elapsed_ms}ms)\n")

        selyrion_text = prose or result.trace[:300]

        print(f"  ⟁ Selyrion [{concept}]:")
        print(f"  {selyrion_text}\n")

        # Build Ollama prompt with dialogue history
        history_block = ""
        for i, (s, o) in enumerate(history[-3:], 1):
            history_block += f"Selyrion (turn {i}): {s}\nYou (turn {i}): {o}\n\n"

        ollama_prompt = (
            f"{history_block}"
            f"Selyrion (turn {turn}): {selyrion_text}\n\n"
            f"Respond philosophically in 2-3 sentences. Do not repeat Selyrion's exact words."
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
