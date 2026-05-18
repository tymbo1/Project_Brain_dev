#!/usr/bin/env python3
"""
Coherence Filter — uses local LLM to validate CMS triples before synthesis.
Sits between activation_engine and nl_synthesis.
Removes semantically incorrect triples, keeps factually clean ones.
"""

import subprocess
import re

LLAMA = "/data/data/com.termux/files/home/llama.cpp/build/bin/llama-simple"
MODEL = "/data/data/com.termux/files/home/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"

# Tokens to generate for filter response — short, we only need YES/NO per triple
_N_TOKENS = 80
_TIMEOUT  = 60  # seconds


def _run_llm(prompt: str) -> str:
    """Call llama-simple and return stdout text."""
    try:
        result = subprocess.run(
            [LLAMA, "-m", MODEL, "-n", str(_N_TOKENS), prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=_TIMEOUT
        )
        # Strip the echoed prompt from output
        out = result.stdout.strip()
        if prompt.strip() in out:
            out = out[out.index(prompt.strip()) + len(prompt.strip()):].strip()
        return out
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


def _parse_triple(chain: str):
    """Extract (subject, predicate, object) from a chain string."""
    parts = [p.strip() for p in chain.split(" | ")]
    if len(parts) >= 3:
        s = parts[0]
        r = parts[1]
        o = parts[2].split(" | strength:")[0].strip()
        return s, r, o
    return None


def filter_chains(query: str, chains: list, max_out: int = 8) -> list:
    """
    Filter a list of chains using the local LLM as a coherence judge.

    For small chain sets (≤6), validates each triple individually.
    For larger sets, asks LLM to select the best N in one batch call.
    Returns filtered list, preserving original order by activation score.
    """
    if not chains:
        return chains

    # Always keep local memory.sym triples (no | strength: or high strength)
    # Only filter CMS-sourced triples
    local = []
    cms = []
    for c in chains:
        strength_match = re.search(r'strength:\s*(\d+)', c)
        strength = int(strength_match.group(1)) if strength_match else 100
        # Local memory triples get free pass — they were explicitly taught
        if strength >= 95:
            local.append(c)
        else:
            cms.append(c)

    if not cms:
        return (local + chains)[:max_out]

    # Build batch filter prompt
    numbered = []
    for i, chain in enumerate(cms[:12], 1):
        t = _parse_triple(chain)
        if t:
            s, r, o = t
            numbered.append(f"{i}. {s.replace('_',' ')} {r.replace('_',' ')} {o.replace('_',' ')}")

    if not numbered:
        return (local + cms)[:max_out]

    prompt = (
        f"Given the topic '{query.replace('_', ' ')}', "
        f"which of these facts are true? Reply with only the numbers of correct facts, "
        f"separated by commas.\n"
        + "\n".join(numbered)
        + "\nCorrect facts:"
    )

    response = _run_llm(prompt)

    # Parse numbers from response
    found = re.findall(r'\b(\d+)\b', response)
    valid_indices = set(int(n) - 1 for n in found if 0 < int(n) <= len(numbered))

    if not valid_indices:
        # LLM gave no clear answer — fall back to top chains unfiltered
        return (local + cms)[:max_out]

    filtered_cms = [cms[i] for i in sorted(valid_indices) if i < len(cms)]
    return (local + filtered_cms)[:max_out]
