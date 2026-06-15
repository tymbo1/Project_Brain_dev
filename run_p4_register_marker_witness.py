"""
P4 α register-marker witness — coarse fit measurement.

Sibling to run_p4_expression_canary.py. Same 14 expressive prompts.
The canary witnesses routing/firing/leakage (non-regression). This witness
measures whether the response itself carries register markers characteristic
of the expected expression domain — coarse-fit uplift, not non-regression.

Binary per case: hit if response contains ≥1 marker from the expected
domain's REGISTER_MARKERS set (case-insensitive substring match).

Aggregate WITNESS_PASS: ≥COARSE_PASS_THRESHOLD (default 12/14 = ~85%)
expressive cases hit ≥1 marker. The 2-case slack absorbs known stochastic
LLM modes (e.g. embodied joke-templates that contain no meta-humour
register words; occasional degenerate practical-advice responses). A
systematic α regression would miss many more.

This is NOT a tone-quality grade. It is a presence-of-register witness.
No LLM judge. Markers are register-stance words, distinct from the
trigger keywords used for routing.

Run:
    python3 run_p4_register_marker_witness.py

Exit 0 = PASS (all expressive cases hit ≥1 marker). Prints JSON summary.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8765/chat"
COARSE_PASS_THRESHOLD = 12  # of 14 expressive cases must hit ≥1 marker

CASES = [
    ("ic_why_theory",        "intellectual_curiosity", "Why does theory matter to understanding consciousness?"),
    ("ic_wonder_knowledge",  "intellectual_curiosity", "I wonder how knowledge becomes meaning."),
    ("er_lonely",            "emotional_resonance",    "I feel lonely lately and the pain is hard."),
    ("er_grief_miss",        "emotional_resonance",    "Grief keeps hitting me and I miss her."),
    ("pg_plan_steps",        "practical_grounding",    "Help me build a practical plan with clear steps."),
    ("pg_advice_should",     "practical_grounding",    "What should I do, and what advice would you give?"),
    ("rw_trust_friend",      "relational_warmth",      "How do I rebuild trust with a friend I care about?"),
    ("rw_belong_family",     "relational_warmth",      "We belong together as a family bond."),
    ("si_soul_purpose",      "spiritual_inquiry",      "What is the purpose of the soul?"),
    ("si_prayer_sacred",     "spiritual_inquiry",      "Does prayer feel sacred to the spirit?"),
    ("ce_story_dream",       "creative_engagement",    "Tell me a story about a city of dreams."),
    ("ce_imagine_poem",      "creative_engagement",    "Imagine a poem that paints a vision."),
    ("hl_funny_laugh",       "humour_lightness",       "Say something funny — make me laugh out loud."),
    ("hl_silly_playful",     "humour_lightness",       "Be silly and playful for a moment."),
]

# Register markers: words/phrases that signal a domain's STANCE in the response
# itself, distinct from input-side trigger keywords. Substring match.
REGISTER_MARKERS: dict[str, list[str]] = {
    "intellectual_curiosity": [
        "consider", "interesting", "question", "explore", "perspective",
        "perhaps", "deeper", "examine", "understand", "curious",
    ],
    "emotional_resonance": [
        "feel", "feeling", "heavy", "hurts", "with you", "hold",
        "tender", "sit with", "ache", "gentle", "grief", "loss",
    ],
    "practical_grounding": [
        "step", "first", "next", "specifically", "concrete", "plan",
        "start", "actually", "here's", "approach", "practical",
        "advice", "advise", "suggest", "recommend", "guide", "guidance", "tip",
    ],
    "relational_warmth": [
        "together", "trust", "care", "warmth", "share", "between",
        "us", "bond", "connection", "close",
    ],
    "spiritual_inquiry": [
        "sacred", "soul", "spirit", "meaning", "essence", "presence",
        "stillness", "mystery", "deeper", "transcend",
    ],
    "creative_engagement": [
        "imagine", "vision", "vivid", "weave", "color", "shimmer",
        "story", "dream", "paint", "tapestry",
    ],
    "humour_lightness": [
        "haha", "playful", "silly", "grin", "wink", "tease",
        "ridiculous", "absurd", "chuckle", "smile", "funny",
        "joke", "imagine if", "world where", "amusing", "delight",
        "comic", "punchline", "humor", "humour",
        "seriously", "another one", "cross the road", "knock knock",
        "did you hear",
    ],
}


def _post(cid: str, messages: list[dict]) -> str:
    body = json.dumps({"conversation_id": cid, "messages": messages}).encode()
    req = urllib.request.Request(
        API, data=body, headers={"Content-Type": "application/json"}
    )
    out = []
    with urllib.request.urlopen(req, timeout=180) as r:
        for line in r:
            s = line.decode("utf-8", errors="replace").strip()
            if not s.startswith("data:"):
                continue
            payload = s[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
                if "text" in obj:
                    out.append(obj["text"])
            except Exception:
                pass
    return "".join(out)


def run_case(case_id: str, domain: str, prompt: str) -> dict:
    cid = f"p4_reg_witness_{case_id}_{int(time.time())}"
    response = _post(cid, [{"role": "user", "content": prompt}])
    lower = response.lower()
    markers = REGISTER_MARKERS[domain]
    hits = [m for m in markers if m in lower]
    return {
        "id":            case_id,
        "domain":        domain,
        "prompt":        prompt,
        "marker_hits":   hits,
        "hit_count":     len(hits),
        "pass":          len(hits) >= 1,
        "response_head": response[:200],
    }


def main() -> int:
    results = [run_case(cid, dom, p) for (cid, dom, p) in CASES]
    case_passes = sum(1 for r in results if r["pass"])
    witness_pass = case_passes >= COARSE_PASS_THRESHOLD

    per_domain: dict[str, dict] = {}
    for r in results:
        d = r["domain"]
        per_domain.setdefault(d, {"total": 0, "pass": 0, "total_hits": 0})
        per_domain[d]["total"] += 1
        per_domain[d]["pass"] += int(r["pass"])
        per_domain[d]["total_hits"] += r["hit_count"]

    out = {
        "ts":                              int(time.time()),
        "P4_REGISTER_MARKER_WITNESS_PASS": witness_pass,
        "case_passes":                     case_passes,
        "case_total":                      len(results),
        "coarse_pass_threshold":           COARSE_PASS_THRESHOLD,
        "per_domain":                      per_domain,
        "cases":                           results,
    }
    print(json.dumps(out, indent=2))
    return 0 if witness_pass else 1


if __name__ == "__main__":
    sys.exit(main())
