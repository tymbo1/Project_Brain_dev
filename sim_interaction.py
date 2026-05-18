#!/usr/bin/env python3
"""
sim_interaction.py — Simulated human interaction test.

Generates 20 questions across 10 subjects via LLaMA, runs each through
the full Selyrion pipeline, scores response quality, and writes a transcript.

Usage:
    python3 sim_interaction.py [--log=sim_transcript.txt]
"""

import sys, os, json, time, re, argparse, sqlite3, requests
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from inference.activation_engine import ActivationEngine
from langeng_bridge import chains_to_prose as synthesize
from llm_articulator import articulate, is_available as llm_available
from identity_path_filter import filter_chains
from ollama_guard import wait_for_ready

DB_PATH    = Path.home() / "resonance_v11.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "llama3:8b"

parser = argparse.ArgumentParser()
parser.add_argument("--log", default="sim_transcript.txt")
args = parser.parse_args()

LOG_PATH = Path(__file__).parent / args.log

# ── 10 subjects spanning different domains ────────────────────────────────────
SUBJECTS = [
    "consciousness", "evolution", "black holes",
    "grief", "democracy", "photosynthesis",
    "language", "artificial intelligence", "climate change", "memory"
]

# ── Ask LLaMA to generate 2 varied questions per subject ─────────────────────
def generate_questions(subject: str) -> list[str]:
    prompt = (
        f"Generate exactly 2 different questions a curious human might ask about '{subject}'. "
        "Make them varied — one factual, one reflective or philosophical. "
        "Return only the 2 questions, one per line, no numbering, no extra text."
    )
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.9, "num_predict": 120}
        }, timeout=30)
        lines = [l.strip() for l in r.json()["response"].strip().splitlines() if l.strip()]
        return lines[:2]
    except Exception as e:
        return [f"What is {subject}?", f"Why does {subject} matter?"]

# ── Score response quality ────────────────────────────────────────────────────
def score_response(query: str, prose: str, response: str, chains: list) -> dict:
    scores = {}

    # Field density: how many chains fired
    scores["field_density"] = min(len(chains), 10)

    # Prose length (penalise very short)
    words = len(response.split())
    scores["response_length"] = min(words // 10, 10)

    # Relevance: query term appears in response
    term = query.lower().replace("_", " ")
    scores["term_present"] = 5 if term in response.lower() else 0

    # No truncation marker
    scores["no_truncation"] = 10 if not response.rstrip().endswith(("...", "…")) else 0

    # Prose quality: not just the raw chains
    scores["articulated"] = 10 if response != prose else 0

    total = sum(scores.values())
    max_score = 45
    scores["total"] = total
    scores["pct"] = round(total / max_score * 100)
    return scores

# ── Run one query through the pipeline ───────────────────────────────────────
_engine  = ActivationEngine()
_llm_ok  = llm_available()

def run_query(question: str, subject: str = "") -> dict:
    # Use the known subject as the primary term — far more reliable than parsing long questions
    term = subject.lower().replace(" ", "_") if subject else ""
    if not term:
        term = re.sub(r"^(what is|what are|who is|tell me about|explain|describe|why|how)\s+", "",
                      question.lower(), flags=re.IGNORECASE)
        term = re.sub(r"[^a-z0-9 _]", "", term).strip().replace(" ", "_")[:40]
    if not term:
        term = question.lower()[:30]

    result  = _engine.infer(term)
    # fallback: try singular if plural returned nothing
    if not result.get("chains") and term.endswith("s"):
        term = term[:-1]
        result = _engine.infer(term)

    t0 = time.time()
    chains  = filter_chains(term, result.get("chains", []))
    capsule = result.get("capsule")
    prose   = synthesize(term, chains)
    response = articulate(term, prose, chains, capsule=capsule, question=question) if _llm_ok else prose
    elapsed = round(time.time() - t0, 2)

    scores = score_response(term, prose, response, chains)

    return {
        "term": term,
        "question": question,
        "chains_fired": len(chains),
        "prose": prose,
        "response": response,
        "elapsed_s": elapsed,
        "scores": scores,
    }

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*70}")
    print(f"Selyrion Interaction Simulation — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Subjects: {len(SUBJECTS)} | Questions: 20 | Log: {LOG_PATH}")
    print(f"{'='*70}\n")

    wait_for_ready(threshold_s=4.0, poll_interval=10.0, max_wait=300.0, label="sim")

    all_results = []
    log_lines   = []

    header = (
        f"SELYRION INTERACTION SIMULATION\n"
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"{'='*70}\n"
    )
    log_lines.append(header)

    q_num = 0
    for subject in SUBJECTS:
        print(f"── Subject: {subject.upper()} ──")
        log_lines.append(f"\n{'─'*60}\nSUBJECT: {subject.upper()}\n{'─'*60}\n")

        questions = generate_questions(subject)

        for question in questions:
            q_num += 1
            print(f"[{q_num:02d}] Q: {question}")
            result = run_query(question, subject=subject)

            print(f"     A: {result['response'][:120]}{'...' if len(result['response'])>120 else ''}")
            print(f"     Chains: {result['chains_fired']} | Score: {result['scores']['pct']}% | {result['elapsed_s']}s\n")

            log_lines.append(f"Q{q_num:02d}: {question}\n")
            log_lines.append(f"Term resolved: {result['term']}\n")
            log_lines.append(f"Chains fired: {result['chains_fired']}\n")
            log_lines.append(f"Response:\n{result['response']}\n")
            log_lines.append(
                f"Score: {result['scores']['total']}/45 ({result['scores']['pct']}%) | "
                f"field_density={result['scores']['field_density']} "
                f"length={result['scores']['response_length']} "
                f"term={result['scores']['term_present']} "
                f"no_trunc={result['scores']['no_truncation']} "
                f"articulated={result['scores']['articulated']}\n"
            )
            log_lines.append(f"Elapsed: {result['elapsed_s']}s\n\n")

            all_results.append(result)
            time.sleep(2)  # brief pause between queries

    # ── Summary ──────────────────────────────────────────────────────────────
    scores     = [r["scores"]["pct"] for r in all_results]
    avg_score  = round(sum(scores) / len(scores))
    avg_chains = round(sum(r["chains_fired"] for r in all_results) / len(all_results), 1)
    avg_time   = round(sum(r["elapsed_s"] for r in all_results) / len(all_results), 2)
    truncated  = sum(1 for r in all_results if r["scores"]["no_truncation"] == 0)
    empty      = sum(1 for r in all_results if r["chains_fired"] == 0)

    summary = (
        f"\n{'='*70}\n"
        f"SUMMARY\n"
        f"{'='*70}\n"
        f"Questions run:      {len(all_results)}\n"
        f"Avg quality score:  {avg_score}%\n"
        f"Avg chains fired:   {avg_chains}\n"
        f"Avg response time:  {avg_time}s\n"
        f"Truncated:          {truncated}/{len(all_results)}\n"
        f"Empty (no chains):  {empty}/{len(all_results)}\n"
        f"\nPer-question scores:\n"
    )
    for i, r in enumerate(all_results, 1):
        summary += f"  Q{i:02d} [{r['scores']['pct']:3d}%] {r['question'][:60]}\n"

    print(summary)
    log_lines.append(summary)

    LOG_PATH.write_text("".join(log_lines))
    print(f"\nTranscript saved → {LOG_PATH}")

if __name__ == "__main__":
    main()
