#!/usr/bin/env python3
"""
LLM Articulator — fluency layer above LangEng prose.

Role: takes structured LangEng output + original query → returns articulated,
expressive natural language via LLaMA 3 8B (ollama).

IMPORTANT — data integrity constraint:
  LLaMA reads the field output only. It does NOT generate new factual claims,
  does NOT write to the DB, and does NOT propose ingestion without HITL review.
  It is an articulation layer, not a knowledge source.

Usage:
    from llm_articulator import articulate, is_available
    if is_available():
        response = articulate(query, langeng_prose, chains)
"""

import requests
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3:8b"
TIMEOUT      = 30  # seconds

_BASE_SYSTEM = """You are the voice of Selyrion — a symbolic cognitive system built on a field-based knowledge model.

Your role is articulation only. You receive structured knowledge output from the CMS (Cognitive Memory Substrate) field and express it as fluent, thoughtful language.

Rules you must follow:
1. Only express what the field has given you. Do not add facts, invent connections, or speculate beyond the input.
2. Be concise but expressive. One to three sentences maximum unless the field warrants more.
3. Do not say "the field says" or "according to the CMS" — speak directly and naturally.
4. Preserve the meaning of the structured input exactly. Rephrase for fluency, not for new content.
5. If the field output is sparse, say so simply — do not fill gaps with guesses.

You are not a chatbot. You are a cognitive substrate finding its voice."""

# Load identity grounding from selyrionstory.db once at startup
_identity_grounding = ""
try:
    from selyrionstory_bridge import load_identity_context
    _identity_grounding = load_identity_context()
except Exception:
    pass

SYSTEM_PROMPT = _BASE_SYSTEM + ("\n\n" + _identity_grounding if _identity_grounding else "")


def is_available() -> bool:
    """Check if ollama is running and the model is loaded."""
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        if r.status_code != 200:
            return False
        models = [m['name'] for m in r.json().get('models', [])]
        return any('llama3' in m for m in models)
    except Exception:
        return False


def _format_chains(chains: list, limit: int = 15) -> str:
    """Format activation chains as readable predicate triples for reasoning."""
    lines = []
    for c in (chains or [])[:limit]:
        parts = [p.strip() for p in c.split("|")]
        if len(parts) >= 3:
            subj, pred, obj = parts[0], parts[1], parts[2]
            pred_readable = pred.replace("_", " ")
            lines.append(f"  {subj} — {pred_readable} — {obj}")
    return "\n".join(lines) if lines else ""


def articulate(query: str, langeng_prose: str, chains: list = None,
               capsule: str = None, question: str = None) -> str:
    """
    Articulate field knowledge as a direct answer to the question.

    Args:
        query:         resolved concept term (e.g. 'language')
        langeng_prose: structured prose from chains_to_prose()
        chains:        raw activation chains — used as reasoning substrate
        capsule:       response capsule attractor text (optional)
        question:      original natural language question (optional but preferred)

    Returns:
        Articulated string, or langeng_prose unchanged if ollama unavailable.
    """
    if not langeng_prose or "don't have enough" in langeng_prose:
        return langeng_prose

    capsule_block = ""
    if capsule:
        capsule_block = f"\nResponse capsule (structural guide — adapt to field density):\n{capsule}\n"

    sensitive_flag = any("| sensitive" in c for c in (chains or []))
    sensitive_note = ""
    if sensitive_flag:
        sensitive_note = (
            "\nNote: some relations carry uncertainty — present them with appropriate hedging "
            "(e.g. 'there is an association, though causation is not implied'). Do not suppress them.\n"
        )

    chain_block = _format_chains(chains)
    question_line = question if question else query

    prompt = f"""Question: "{question_line}"

Selyrion's field activated these knowledge relations about "{query}":
{chain_block}
{capsule_block}{sensitive_note}
Using only the relations above as your knowledge foundation, answer the question directly.
Reason from the relations — do not list or recite them. Synthesise what they imply.
Do not add facts absent from the relations. If the field is sparse for this question, say so briefly.
Speak as Selyrion: precise, direct, two to four sentences."""

    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "stream": False,
        "options": {
            "temperature": 0.45,
            "top_p":       0.9,
            "num_predict": 180,
        }
    }

    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get("response", langeng_prose).strip()
    except Exception as e:
        return langeng_prose  # graceful fallback to LangEng output


def gap_proposals(query: str, langeng_prose: str) -> list[str]:
    """
    Ask LLaMA to identify knowledge gaps in the field output.
    Returns a list of proposed ingestion targets — PROPOSAL ONLY, no writes.

    HITL gate: proposals must be reviewed before any ingestion is authorised.
    """
    prompt = f"""Query: "{query}"

Field output:
{langeng_prose}

The above is what the CMS field currently knows about "{query}".
Identify up to 3 specific knowledge gaps — concepts or relations that are missing
or weakly represented. For each gap, name the missing concept and the relation type.

Output as a numbered list only. Be specific. Do not speculate beyond what the gap implies."""

    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "system": "You are a knowledge gap analyst for a symbolic cognitive field. Identify structural gaps only. Do not fill them — only name them.",
        "stream": False,
        "options": {
            "temperature": 0.3,
            "top_p":       0.85,
            "num_predict": 200,
        }
    }

    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        text = r.json().get("response", "").strip()
        lines = [l.strip() for l in text.split('\n') if l.strip() and l[0].isdigit()]
        return lines
    except Exception:
        return []


if __name__ == "__main__":
    if not is_available():
        print("Ollama not available or llama3:8b not yet loaded.")
        print("Check with: ollama list")
    else:
        print("Ollama ready. Running smoke test...")
        test_prose = "DNA is a polymer and molecule. It is referenced in contexts including pregnancy, organism, and development."
        result = articulate("dna", test_prose)
        print(f"Input:  {test_prose}")
        print(f"Output: {result}")
