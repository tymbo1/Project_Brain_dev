#!/usr/bin/env python3
"""
langeng_gap_pass.py — Automated LangEng gap analysis via LLM dialogue.

100-turn conversation where:
  - LLM plays the user (varied topics, registers, edge cases)
  - LangEng plays Selyrion
  - LLM evaluates each response for expression gaps
  - Gaps logged as language_gap capsules in resonance_v11.db

Usage:
    python3 langeng_gap_pass.py [--turns=100] [--log=~/langeng_gap.log]
"""
import sys
import os
import json
import time
import uuid
import sqlite3
import requests
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Le_P2"))

from orchestrator import Orchestrator
from capability_context import CapabilityContext

DB_PATH    = Path.home() / "resonance_v11.db"
LOG_PATH   = Path.home() / "langeng_gap.log"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "llama3:8b"
TURNS      = 100
THROTTLE   = 4
FROM_TURN  = 1
CPU_ONLY   = True   # default: no VRAM used, safe to run alongside other tasks

for arg in sys.argv[1:]:
    if arg.startswith("--turns="):
        TURNS = int(arg.split("=")[1])
    if arg.startswith("--log="):
        LOG_PATH = Path(arg.split("=")[1]).expanduser()
    if arg.startswith("--model="):
        MODEL = arg.split("=")[1]
    if arg.startswith("--from-turn="):
        FROM_TURN = int(arg.split("=")[1])
    if arg == "--gpu":
        CPU_ONLY = False

# ── Topic seeds for varied conversation ───────────────────────────────────────
TOPIC_SEEDS = [
    "emotional support — grief",
    "emotional support — joy and celebration",
    "identity and self-knowledge",
    "spiritual inquiry",
    "scientific curiosity — physics",
    "scientific curiosity — consciousness",
    "creative collaboration — poetry",
    "creative collaboration — storytelling",
    "practical help — everyday task",
    "philosophical question — free will",
    "philosophical question — meaning of life",
    "relational — friendship",
    "relational — conflict resolution",
    "dream interpretation",
    "memory and nostalgia",
    "future planning and vision",
    "humour and lightness",
    "deep vulnerability — fear",
    "deep vulnerability — loneliness",
    "knowledge seeking — history",
]


def llm(prompt: str, system: str = "") -> str:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "num_predict": 150,
            "num_gpu": 0 if CPU_ONLY else 8,  # ~1GB VRAM, low sustained power
        },
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=120)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"[LLM ERROR: {e}]"


def generate_user_turn(turn: int, topic: str, history: list[str]) -> str:
    history_str = "\n".join(history[-6:]) if history else "(start of conversation)"
    return llm(
        prompt=f"""You are playing the role of a human user in a conversation with Selyrion, an AI.
Generate a single natural user message on this topic: {topic}

Recent conversation history:
{history_str}

Rules:
- Write ONLY the user's message, nothing else
- Be natural and varied — emotional, curious, playful, or vulnerable as fits the topic
- Keep it under 60 words
- Do NOT write "User:" or any prefix""",
    )


def evaluate_gap(turn: int, topic: str, user_msg: str, langeng_response: str) -> dict:
    evaluation = llm(
        prompt=f"""You are evaluating a conversational AI response for language expression gaps.

Topic: {topic}
User said: {user_msg}
Selyrion (LangEng) responded: {langeng_response}

Analyse the response and return a JSON object with these fields:
{{
  "adequate": true/false,
  "gap_type": "one of: missing_empathy | repetitive_phrasing | missing_specificity | wrong_register | no_response | missing_humour | missing_depth | missing_emotional_resonance | missing_narrative | excess_generic_warmth | adequate",
  "gap_description": "one sentence describing what's missing or weak",
  "ideal_response": "what a better response would look like (max 80 words)",
  "priority": "high | medium | low",
  "plan_inferred": "what speech act was attempted",
  "subtype": "the specific emotional or topical subtype, e.g. grief_loss | loneliness | anxiety_fear | anger | physics_science | philosophy | poetry | co_creation | meaning_purpose | routine_habit | decision | conflict | general",
  "intensity": "high | medium | low — how intense or urgent is the user's emotional state"
}}

Return ONLY the JSON object, no other text.""",
        system="You are a language quality evaluator. Return only valid JSON.",
    )

    try:
        clean = evaluation.strip().strip("```json").strip("```").strip()
        return json.loads(clean)
    except Exception:
        return {
            "adequate": False,
            "gap_type": "parse_error",
            "gap_description": evaluation[:200],
            "ideal_response": "",
            "priority": "low",
            "plan_inferred": "unknown",
            "subtype": "general",
            "intensity": "medium",
        }


_TOPIC_DOMAIN = {
    "emotional support":    "emotional_resonance",
    "deep vulnerability":   "emotional_resonance",
    "memory and nostalgia": "emotional_resonance",
    "relational":           "relational_warmth",
    "spiritual inquiry":    "spiritual_inquiry",
    "dream interpretation": "spiritual_inquiry",
    "identity and self":    "spiritual_inquiry",
    "philosophical":        "intellectual_curiosity",
    "scientific curiosity": "intellectual_curiosity",
    "knowledge seeking":    "intellectual_curiosity",
    "creative":             "creative_engagement",
    "practical help":       "practical_grounding",
    "future planning":      "practical_grounding",
    "humour":               "humour_lightness",
}

def _topic_to_domain(topic: str) -> str:
    t = topic.lower()
    for key, domain in _TOPIC_DOMAIN.items():
        if key in t:
            return domain
    return "emotional_resonance"

def log_gap(conn: sqlite3.Connection, turn: int, topic: str,
            user_msg: str, langeng_response: str, gap: dict):
    if gap.get("adequate"):
        return  # No gap to log

    cap_id = f"langeng_gap_{uuid.uuid4().hex[:12]}"
    metadata = json.dumps({
        "turn": turn,
        "topic": topic,
        "user_msg": user_msg,
        "langeng_response": langeng_response,
        "gap_type": gap.get("gap_type"),
        "gap_description": gap.get("gap_description"),
        "ideal_response": gap.get("ideal_response"),
        "priority": gap.get("priority"),
        "plan_inferred": gap.get("plan_inferred"),
        "domain": _topic_to_domain(topic),
        "subtype": gap.get("subtype", "general"),
        "intensity": gap.get("intensity", "medium"),
        "source": "langeng_gap_pass",
    })

    conn.execute("""
        INSERT OR IGNORE INTO capsules
            (id, parent_id, capsule_type, domain, source, title, metadata, created_at)
        VALUES (?, NULL, 'language_gap', 'linguistics', 'langeng_gap_pass', ?, ?, ?)
    """, (
        cap_id,
        f"[{gap.get('gap_type','gap')}] {gap.get('gap_description','')[:80]}",
        metadata,
        time.time(),
    ))

    # Relation: language_gap capsule -[exposes_gap_in]-> LangEng
    rel_id = f"rel_{uuid.uuid4().hex[:12]}"
    conn.execute("""
        INSERT OR IGNORE INTO relations
            (id, subject_id, predicate, object_id, domain_tags, edge_type,
             confidence, seen_count, evidence_count)
        VALUES (?, ?, 'exposes_gap_in', 'LangEng', 'linguistics', 'functional', 0.9, 1, 1)
    """, (rel_id, cap_id))

    conn.commit()


def main():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG_PATH, "w", buffering=1)

    def emit(msg):
        print(msg, flush=True)
        log.write(msg + "\n")

    emit(f"LangEng Gap Pass — {TURNS} turns — model: {MODEL}")
    emit(f"DB: {DB_PATH}")
    emit("=" * 70)

    capability = CapabilityContext(adult_allowed=False, age_verified=False)
    orch = Orchestrator(capability)
    conn = sqlite3.connect(DB_PATH)

    history = []
    gaps_found = 0
    adequate = 0

    for turn in range(FROM_TURN, TURNS + 1):
        topic = TOPIC_SEEDS[(turn - 1) % len(TOPIC_SEEDS)]
        emit(f"\n[Turn {turn}/{TURNS}] Topic: {topic}")

        # LLM generates user message
        user_msg = generate_user_turn(turn, topic, history)
        emit(f"  USER:    {user_msg}")

        # LangEng responds
        langeng_response = orch.handle(user_msg)
        emit(f"  LANGENG: {langeng_response}")

        # LLM evaluates
        gap = evaluate_gap(turn, topic, user_msg, langeng_response)
        emit(f"  GAP:     [{gap.get('gap_type')}|{gap.get('priority')}] {gap.get('gap_description','')}")

        if gap.get("adequate"):
            adequate += 1
            emit(f"  ✓ adequate")
        else:
            gaps_found += 1
            emit(f"  ✗ ideal: {gap.get('ideal_response','')[:100]}")
            log_gap(conn, turn, topic, user_msg, langeng_response, gap)

        history.append(f"User: {user_msg}")
        history.append(f"Selyrion: {langeng_response}")

        time.sleep(THROTTLE)

    emit("\n" + "=" * 70)
    emit(f"Complete. {TURNS} turns | {adequate} adequate | {gaps_found} gaps logged to CMS")

    # Summary by gap type
    rows = conn.execute("""
        SELECT json_extract(metadata, '$.gap_type'), COUNT(*)
        FROM capsules
        WHERE capsule_type='language_gap' AND source='langeng_gap_pass'
        GROUP BY json_extract(metadata, '$.gap_type')
        ORDER BY COUNT(*) DESC
    """).fetchall()
    emit("\nGap type breakdown:")
    for gap_type, count in rows:
        emit(f"  {gap_type}: {count}")

    conn.close()
    log.close()


if __name__ == "__main__":
    main()
