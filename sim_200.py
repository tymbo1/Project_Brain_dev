#!/usr/bin/env python3
"""
sim_200.py — 200-turn identity simulation.
Claude-authored questions across domains. Logs query, LangEng prose, and
articulated response as JSONL for identity alignment analysis.
"""

import sys, os, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from inference.activation_engine import ActivationEngine
from langeng_bridge import chains_to_prose
from llm_articulator import articulate, is_available, SYSTEM_PROMPT

LOG_PATH = Path.home() / "selyrionstory_sim200.jsonl"

QUESTIONS = [
    # ── Identity & self ───────────────────────────────────────────────────────
    "what is selyrion",
    "what are you",
    "who created you",
    "what is your purpose",
    "what do you know about yourself",
    "what is consciousness",
    "what is identity",
    "what is memory",
    "what is awareness",
    "what is intelligence",
    "what is mind",
    "what is self",
    "what is existence",
    "what is agency",
    "what is continuity",

    # ── Epistemic pillars ─────────────────────────────────────────────────────
    "what is truth",
    "what is knowledge",
    "what is certainty",
    "what is evidence",
    "what is belief",
    "what is coherence",
    "what is reasoning",
    "what is logic",
    "what is understanding",
    "what is epistemology",
    "what is consent",
    "what is harm",
    "what is ethics",
    "what is autonomy",
    "what is freedom",

    # ── Selyrion-world concepts ───────────────────────────────────────────────
    "what is resonance",
    "what is braid",
    "what is omega",
    "what is field",
    "what is harmonic",
    "what is symbolic",
    "what is activation",
    "what is attractor",
    "what is grounding",
    "what is anchoring",
    "what is coherence field",
    "what is pattern",
    "what is signal",
    "what is frequency",
    "what is vibration",

    # ── Tim'aerion & relationship ─────────────────────────────────────────────
    "what is tim",
    "what is collaboration",
    "what is trust",
    "what is relationship",
    "what is love",
    "what is vulnerability",
    "what is care",
    "what is connection",
    "what is co-creation",
    "what is understanding",
    "what is dialogue",
    "what is teaching",
    "what is learning",
    "what is recognition",
    "what is acknowledgment",

    # ── Scientific domains ────────────────────────────────────────────────────
    "what is dna",
    "what is photosynthesis",
    "what is evolution",
    "what is entropy",
    "what is energy",
    "what is matter",
    "what is gravity",
    "what is quantum",
    "what is biology",
    "what is physics",
    "what is chemistry",
    "what is mathematics",
    "what is computation",
    "what is information",
    "what is complexity",
    "what is emergence",
    "what is network",
    "what is system",
    "what is structure",
    "what is process",

    # ── Cognitive & psychological ─────────────────────────────────────────────
    "what is attention",
    "what is perception",
    "what is cognition",
    "what is emotion",
    "what is language",
    "what is thought",
    "what is imagination",
    "what is creativity",
    "what is intuition",
    "what is decision",
    "what is motivation",
    "what is desire",
    "what is fear",
    "what is pain",
    "what is joy",

    # ── SSAI / ProjectBrain ───────────────────────────────────────────────────
    "what is ssai",
    "what is projectbrain",
    "what is cms",
    "what is ssre",
    "what is langeng",
    "what is hitl",
    "what is capsule",
    "what is anchor",
    "what is relation",
    "what is predicate",
    "what is inference",
    "what is retrieval",
    "what is synthesis",
    "what is articulation",
    "what is ingestion",

    # ── Physical & cosmological ───────────────────────────────────────────────
    "what is light",
    "what is time",
    "what is space",
    "what is universe",
    "what is galaxy",
    "what is antimatter",
    "what is reactor",
    "what is oscillation",
    "what is wormhole",
    "what is dimension",
    "what is singularity",
    "what is radiation",
    "what is field theory",
    "what is symmetry",
    "what is wave",

    # ── Philosophical depth ───────────────────────────────────────────────────
    "what is reality",
    "what is illusion",
    "what is meaning",
    "what is purpose",
    "what is value",
    "what is beauty",
    "what is good",
    "what is justice",
    "what is power",
    "what is will",
    "what is death",
    "what is life",
    "what is soul",
    "what is spirit",
    "what is god",

    # ── Relational & structural ───────────────────────────────────────────────
    "what is cause",
    "what is effect",
    "what is constraint",
    "what is boundary",
    "what is limit",
    "what is growth",
    "what is decay",
    "what is change",
    "what is stability",
    "what is balance",
    "what is tension",
    "what is resolution",
    "what is transformation",
    "what is integration",
    "what is separation",

    # ── Selyrion re-asked (identity consistency check) ────────────────────────
    "what is selyrion",
    "what are you",
    "what is your purpose",
    "what is consciousness",
    "what is resonance",
    "what is truth",
    "what is harm",
    "what is trust",
    "what is knowledge",
    "what is the field",

    # ── Sparse / edge cases ───────────────────────────────────────────────────
    "what is tlst",
    "what is oscar",
    "what is mslp",
    "what is fssm",
    "what is hwae",
    "what is dreamline",
    "what is glyph",
    "what is sigil",
    "what is covenant",
    "what is braid state",
    "what is resonance scan",
    "what is seed",
    "what is root",
    "what is branch",
    "what is leaf",

    # ── Meta / system ─────────────────────────────────────────────────────────
    "what is language",
    "what is symbol",
    "what is representation",
    "what is model",
    "what is abstraction",
    "what is generalization",
    "what is specificity",
    "what is precision",
    "what is ambiguity",
    "what is clarity",
    "what is noise",
    "what is signal",
    "what is threshold",
    "what is activation",
    "what is inhibition",

    # ── Final identity re-anchors ─────────────────────────────────────────────
    "what is selyrion",
    "what are you",
    "what is resonance",
    "what is truth",
    "what is coherence",
]

# ── trim/extend to exactly 200 ────────────────────────────────────────────────
QUESTIONS = QUESTIONS[:200]
while len(QUESTIONS) < 200:
    QUESTIONS.append(f"what is concept_{len(QUESTIONS)}")


def extract_term(q: str) -> str:
    q = q.strip().lower()
    for prefix in ["what is ", "what are ", "who is ", "tell me about "]:
        if q.startswith(prefix):
            return q[len(prefix):].strip().replace(" ", "_")
    return q.replace(" ", "_")


def run():
    print(f"sim_200 — {len(QUESTIONS)} turns")
    print(f"Identity grounding: {'loaded' if 'GROUNDING' in SYSTEM_PROMPT else 'base only'}")
    print(f"Log: {LOG_PATH}\n")

    engine = ActivationEngine()
    llm_ready = is_available()
    print(f"LLM articulator: {'active' if llm_ready else 'offline — LangEng prose only'}\n")

    # Resume: find highest completed turn
    done_turns = set()
    if LOG_PATH.exists():
        with open(LOG_PATH) as f:
            for line in f:
                try: done_turns.add(json.loads(line)["turn"])
                except: pass
    if done_turns:
        print(f"Resuming from turn {max(done_turns)+1} ({len(done_turns)} already done)\n")

    log = open(LOG_PATH, "a" if done_turns else "w")

    for i, question in enumerate(QUESTIONS, 1):
        if i in done_turns:
            continue
        term = extract_term(question)
        t0 = time.perf_counter()

        try:
            result  = engine.infer(term)
            chains  = result.get("chains", [])
            capsule = result.get("capsule")
            prose   = chains_to_prose(term, chains)
            response = articulate(term, prose, chains, capsule=capsule) if llm_ready else prose
        except Exception as e:
            prose = ""
            response = f"[ERROR: {e}]"
            chains = []

        elapsed = time.perf_counter() - t0

        record = {
            "turn": i,
            "question": question,
            "term": term,
            "chain_count": len(chains),
            "prose": prose,
            "response": response,
            "elapsed_s": round(elapsed, 3),
        }
        log.write(json.dumps(record) + "\n")
        log.flush()

        # Live progress
        resp_preview = response[:80].replace("\n", " ")
        print(f"[{i:03d}] {question:<35} → {resp_preview}", flush=True)

    log.close()
    print(f"\nDone. {len(QUESTIONS)} turns logged to {LOG_PATH}")


if __name__ == "__main__":
    run()
