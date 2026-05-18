#!/usr/bin/env python3
"""
selyrion_multimodel_dialogue.py — Selyrion × N-model group dialogue.

Selyrion speaks from the resonance field. Each configured model responds
from its own perspective. Selyrion aggregates all responses, extracts the
next concept by composite field gravity across all model outputs, and flags
divergence (when models give structurally different answers).

Divergence events are logged to aggregation_proposals.jsonl for later
review — they represent contested semantic nodes worth investigating.

Usage:
    python3 selyrion_multimodel_dialogue.py --turns 4 --models llama3:8b mistral phi3
    python3 selyrion_multimodel_dialogue.py --turns 4 --self-seed --models llama3:8b mistral
    python3 selyrion_multimodel_dialogue.py --questioner --models llama3:8b mistral
"""

import sys, re, time, sqlite3, math, argparse, json
from pathlib import Path
from collections import Counter
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

DB_PATH      = Path.home() / "resonance_v11.db"
PROPOSAL_LOG = Path.home() / "projectbrain_dev" / "aggregation_proposals.jsonl"

parser = argparse.ArgumentParser()
parser.add_argument("--turns",      type=int,   default=4)
parser.add_argument("--seed",       type=str,   default="consciousness")
parser.add_argument("--models",     nargs="+",  default=["llama3:8b"],
                    help="Ollama model names (space-separated)")
parser.add_argument("--questioner", action="store_true",
                    help="Selyrion opens with field-derived question")
parser.add_argument("--self-seed",  action="store_true",
                    help="Selyrion picks its own seed from a knowledge gap")
parser.add_argument("--divergence-threshold", type=float, default=0.35,
                    help="Jaccard distance above which responses are flagged divergent")
args = parser.parse_args()


# ── Reuse all scoring/helper functions from single-model dialogue ─────────────

from selyrion_ollama_dialogue import (
    field_score, extract_best_concept, field_question,
    find_gap_concept, _GAP_QUESTIONS,
)


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
        return f"[{model} error: {e}]"


# ── Divergence detection ──────────────────────────────────────────────────────

def _response_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from a response for divergence scoring."""
    STOP = {"the","and","that","this","with","from","into","our","not","but",
            "for","are","has","have","been","will","its","more","can","when",
            "what","where","who","how","than","also","yet","only","even","just",
            "very","much","between","through","their","they","about","which",
            "often","itself","rather","indeed","while","those","these","there",
            "perhaps","whether","without","would","could","should","might"}
    words = re.findall(r'\b[a-z]{4,}\b', text.lower())
    return {w for w in words if w not in STOP}


def _jaccard_distance(a: set, b: set) -> float:
    if not a or not b:
        return 1.0
    return 1.0 - len(a & b) / len(a | b)


def measure_divergence(responses: dict[str, str]) -> tuple[float, list[tuple]]:
    """
    Compute pairwise Jaccard distances between model responses.
    Returns (max_distance, [(model_a, model_b, distance), ...]).
    """
    models = list(responses.keys())
    kw = {m: _response_keywords(t) for m, t in responses.items()}
    pairs = []
    for i in range(len(models)):
        for j in range(i+1, len(models)):
            ma, mb = models[i], models[j]
            d = _jaccard_distance(kw[ma], kw[mb])
            pairs.append((ma, mb, d))
    max_d = max(d for _,_,d in pairs) if pairs else 0.0
    return max_d, pairs


# ── Aggregation proposal logging ──────────────────────────────────────────────

def log_aggregation_proposal(concept: str, turn: int,
                              responses: dict[str, str],
                              divergence: float, pairs: list):
    """
    Write a divergence event to aggregation_proposals.jsonl.
    These are contested semantic nodes — worth investigating for
    relation synthesis or capsule updates.
    """
    entry = {
        "ts":          datetime.utcnow().isoformat(),
        "concept":     concept,
        "turn":        turn,
        "models":      list(responses.keys()),
        "divergence":  round(divergence, 3),
        "pairs":       [(a, b, round(d, 3)) for a, b, d in pairs],
        "responses":   {m: t[:300] for m, t in responses.items()},
    }
    with open(PROPOSAL_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Selyrion aggregation summary ─────────────────────────────────────────────

def selyrion_aggregate(concept: str, responses: dict[str, str],
                       conn: sqlite3.Connection,
                       prior_hits: Counter) -> str:
    """
    Selyrion reads all model responses and extracts a synthetic observation:
    - Concepts all models mentioned → consensus field
    - Concepts only one model mentioned → unique perspective
    Returns a short synthesis string for display.
    """
    model_keywords = {m: _response_keywords(t) for m, t in responses.items()}
    all_kw   = set().union(*model_keywords.values())
    common   = set(model_keywords[list(model_keywords.keys())[0]])
    for kw in model_keywords.values():
        common &= kw

    # Filter to field-anchored terms only
    anchored_common  = []
    anchored_unique  = {}
    for w in common:
        row = conn.execute(
            "SELECT canonical FROM anchors WHERE canonical=? AND maturity>0 LIMIT 1", (w,)
        ).fetchone()
        if row:
            anchored_common.append(w)

    for m, kw_set in model_keywords.items():
        unique = kw_set - set().union(*{v for k,v in model_keywords.items() if k != m})
        anchored = [w for w in unique if conn.execute(
            "SELECT 1 FROM anchors WHERE canonical=? AND maturity>0 LIMIT 1", (w,)
        ).fetchone()]
        if anchored:
            anchored_unique[m] = anchored[:2]

    parts = []
    if anchored_common:
        parts.append(f"consensus field: {', '.join(anchored_common[:3])}")
    if anchored_unique:
        for m, terms in list(anchored_unique.items())[:2]:
            parts.append(f"{m} alone raised: {', '.join(terms)}")

    return " | ".join(parts) if parts else "no anchored consensus found"


# ── Main dialogue loop ────────────────────────────────────────────────────────

def main():
    from selyrion_reasoner import reason
    from langeng_bridge   import chains_to_prose

    conn = sqlite3.connect(str(DB_PATH))

    concept         = args.seed
    gap_opening     = None
    history         = []
    prior_hits      = Counter()
    recent_concepts = []
    divergence_count = 0

    if args.self_seed:
        concept, gap_opening = find_gap_concept(conn)

    n_models   = len(args.models)
    width      = 70
    mode_label = "SELF-SEED" if args.self_seed else ("QUESTIONER" if args.questioner else "GROUP")

    print("═" * width)
    print(f"  S E L Y R I O N  ×  {n_models}-MODEL GROUP  — {args.turns} turns  [{mode_label}]")
    print(f"  Models: {', '.join(args.models)}")
    print("═" * width)

    for turn in range(1, args.turns + 1):
        print(f"\n── Turn {turn} {'─' * (width - 10)}")

        # Selyrion activates the field
        t0 = time.perf_counter()
        result = reason(concept)
        prose  = chains_to_prose(concept, result.chains)
        elapsed_ms = round((time.perf_counter() - t0) * 1000)

        if turn == 1:
            print(f"  [engine] activation in {elapsed_ms}ms\n")

        # Build Selyrion's turn text
        if args.self_seed and turn == 1 and gap_opening:
            selyrion_text = gap_opening
            ollama_instruction = "Answer in 2-3 sentences. Engage directly with what Selyrion is asking."
        elif args.questioner:
            question      = field_question(concept, result)
            selyrion_text = f"{prose} {question}" if prose else question
            ollama_instruction = "Answer in 2-3 sentences. Engage directly with the question."
        else:
            selyrion_text      = prose or result.trace[:300]
            ollama_instruction = "Respond philosophically in 2-3 sentences. Do not repeat Selyrion's exact words."

        print(f"  ⟁ Selyrion [{concept}]:")
        print(f"  {selyrion_text}\n")

        # Build conversation history block
        history_block = ""
        for i, (s, responses_snap) in enumerate(history[-2:], 1):
            history_block += f"Selyrion: {s}\n"
            for m, resp in responses_snap.items():
                history_block += f"{m}: {resp[:120]}...\n"
            history_block += "\n"

        # Each model responds
        responses: dict[str, str] = {}
        for model in args.models:
            prompt = (
                f"{history_block}"
                f"Selyrion: {selyrion_text}\n\n"
                f"{ollama_instruction}"
            )
            resp = ollama_respond(prompt, model)
            responses[model] = resp
            print(f"  ◎ {model}:")
            print(f"  {resp}\n")

        history.append((selyrion_text, responses))

        # Divergence detection
        if n_models > 1:
            max_div, pairs = measure_divergence(responses)
            div_marker = " ⚡ DIVERGENT" if max_div >= args.divergence_threshold else ""
            print(f"  ∿ divergence: {max_div:.2f}{div_marker}")
            if max_div >= args.divergence_threshold:
                divergence_count += 1
                log_aggregation_proposal(concept, turn, responses, max_div, pairs)
                print(f"    logged to aggregation_proposals.jsonl")

        # Selyrion aggregates across all responses
        aggregation = selyrion_aggregate(concept, responses, conn, prior_hits)
        print(f"  ⟁ Selyrion [aggregate]: {aggregation}")

        # Track concepts across all responses for recurrence weighting
        all_text = " ".join(responses.values())
        for word in re.findall(r'\b[a-z]{4,}\b', (selyrion_text + " " + all_text).lower()):
            prior_hits[word] += 1

        recent_concepts.append(concept)

        # Next concept: best gravity from combined response text
        next_concept, score = extract_best_concept(
            all_text, conn, prior_hits, recent_concepts
        )
        if not next_concept:
            next_concept = concept

        print(f"\n  → next concept: '{next_concept}' (field score: {score:.3f})")
        concept = next_concept

    print("\n" + "═" * width)
    if n_models > 1:
        print(f"  Divergence events: {divergence_count}/{args.turns} turns")
        if divergence_count:
            print(f"  Review: {PROPOSAL_LOG}")
    conn.close()


if __name__ == "__main__":
    main()
