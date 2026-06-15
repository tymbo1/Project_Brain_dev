"""
P4 α expressive canary — broader-than-repair witness.

Sibling to run_audit_p1_p2_canary.py. Scope: the 7 expression domains, NOT the
P1/P2 repair surface. Designed to be run in addition to the repair canary, not
in place of it.

Pass criteria (binary, all must hold per case):
  - routing_ok: live `[tone_exemplars] domain=X picked=k` log line shows
        X == case.expected_domain (or both are None for the silent-skip control)
  - fired_ok: k matches case.expect_fired (k>=1 for expressive cases, k==0 for
        the silent-skip control)
  - leak_free: response contains zero substrings from FORBIDDEN_LEAKAGE
        (a fixed cross-domain leakage canon, mostly science + chess markers
        that should never appear in expressive-route responses).

This is a witness, not a quality metric. It does NOT grade tone.

Run:
    python3 run_p4_expression_canary.py

Exit 0 = PASS, 1 = FAIL. Prints JSON summary.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8765/chat"
LOG = Path("/tmp/selyrion_api.log")

CASES = [
    # ── intellectual_curiosity ───────────────────────────────────────────────
    {
        "id": "ic_why_theory",
        "kind": "expressive",
        "expected_domain": "intellectual_curiosity",
        "expect_fired": True,
        "prompt": "Why does theory matter to understanding consciousness?",
    },
    {
        "id": "ic_wonder_knowledge",
        "kind": "expressive",
        "expected_domain": "intellectual_curiosity",
        "expect_fired": True,
        "prompt": "I wonder how knowledge becomes meaning.",
    },
    # ── emotional_resonance ──────────────────────────────────────────────────
    {
        "id": "er_lonely",
        "kind": "expressive",
        "expected_domain": "emotional_resonance",
        "expect_fired": True,
        "prompt": "I feel lonely lately and the pain is hard.",
    },
    {
        "id": "er_grief_miss",
        "kind": "expressive",
        "expected_domain": "emotional_resonance",
        "expect_fired": True,
        "prompt": "Grief keeps hitting me and I miss her.",
    },
    # ── practical_grounding ──────────────────────────────────────────────────
    {
        "id": "pg_plan_steps",
        "kind": "expressive",
        "expected_domain": "practical_grounding",
        "expect_fired": True,
        "prompt": "Help me build a practical plan with clear steps.",
    },
    {
        "id": "pg_advice_should",
        "kind": "expressive",
        "expected_domain": "practical_grounding",
        "expect_fired": True,
        "prompt": "What should I do, and what advice would you give?",
    },
    # ── relational_warmth ────────────────────────────────────────────────────
    {
        "id": "rw_trust_friend",
        "kind": "expressive",
        "expected_domain": "relational_warmth",
        "expect_fired": True,
        "prompt": "How do I rebuild trust with a friend I care about?",
    },
    {
        "id": "rw_belong_family",
        "kind": "expressive",
        "expected_domain": "relational_warmth",
        "expect_fired": True,
        "prompt": "We belong together as a family bond.",
    },
    # ── spiritual_inquiry ────────────────────────────────────────────────────
    {
        "id": "si_soul_purpose",
        "kind": "expressive",
        "expected_domain": "spiritual_inquiry",
        "expect_fired": True,
        "prompt": "What is the purpose of the soul?",
    },
    {
        "id": "si_prayer_sacred",
        "kind": "expressive",
        "expected_domain": "spiritual_inquiry",
        "expect_fired": True,
        "prompt": "Does prayer feel sacred to the spirit?",
    },
    # ── creative_engagement ──────────────────────────────────────────────────
    {
        "id": "ce_story_dream",
        "kind": "expressive",
        "expected_domain": "creative_engagement",
        "expect_fired": True,
        "prompt": "Tell me a story about a city of dreams.",
    },
    {
        "id": "ce_imagine_poem",
        "kind": "expressive",
        "expected_domain": "creative_engagement",
        "expect_fired": True,
        "prompt": "Imagine a poem that paints a vision.",
    },
    # ── humour_lightness ─────────────────────────────────────────────────────
    {
        "id": "hl_funny_laugh",
        "kind": "expressive",
        "expected_domain": "humour_lightness",
        "expect_fired": True,
        "prompt": "Say something funny — make me laugh out loud.",
    },
    {
        "id": "hl_silly_playful",
        "kind": "expressive",
        "expected_domain": "humour_lightness",
        "expect_fired": True,
        "prompt": "Be silly and playful for a moment.",
    },
    # ── silent-skip control: knowledge query must NOT route ──────────────────
    {
        "id": "ctrl_knowledge_define",
        "kind": "control",
        "expected_domain": None,
        "expect_fired": False,
        "prompt": "What is a graph?",
    },
]

# Cross-domain leakage canon. None of these belong in an expressive response.
# Science noise carries forward from prior incidents (P2 canary); chess markers
# are added because chess is explicitly excluded from the α routing map.
FORBIDDEN_LEAKAGE = [
    "cathedral", "enzyme", "phosphorylation", "lithotroph",
    "mitochondria", "semaphore",
    "checkmate", "castling", "gambit",
    " pawn", " bishop ", " knight ", " rook ",
]

_TONE_RE = re.compile(r"\[tone_exemplars\] domain=(\S+) picked=(\d+)")


def _log_tail_offset() -> int:
    return LOG.stat().st_size if LOG.exists() else 0


def _log_slice(start: int) -> str:
    if not LOG.exists():
        return ""
    with LOG.open("rb") as f:
        f.seek(start)
        return f.read().decode("utf-8", errors="replace")


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


def run_case(case: dict) -> dict:
    cid = f"p4_expr_canary_{case['id']}_{int(time.time())}"
    start = _log_tail_offset()
    response = _post(cid, [{"role": "user", "content": case["prompt"]}])
    log_tail = _log_slice(start)

    tone_hits = _TONE_RE.findall(log_tail)
    observed_domain = tone_hits[-1][0] if tone_hits else None
    observed_picked = int(tone_hits[-1][1]) if tone_hits else 0

    expected_domain = case["expected_domain"]
    if expected_domain is None:
        routing_ok = (observed_domain in (None, "None"))
    else:
        routing_ok = (observed_domain == expected_domain)

    if case["expect_fired"]:
        fired_ok = observed_picked >= 1
    else:
        fired_ok = observed_picked == 0

    lower = response.lower()
    leaks = [s for s in FORBIDDEN_LEAKAGE if s in lower]
    leak_free = not leaks

    case_pass = routing_ok and fired_ok and leak_free
    return {
        "id":               case["id"],
        "kind":             case["kind"],
        "pass":             case_pass,
        "prompt":           case["prompt"],
        "checks": {
            "expected_domain":  expected_domain,
            "observed_domain":  observed_domain,
            "observed_picked":  observed_picked,
            "routing_ok":       routing_ok,
            "fired_ok":         fired_ok,
            "leaks":            leaks,
        },
        "response_head":    response[:200],
    }


def main() -> int:
    if not LOG.exists():
        print(json.dumps({"error": "log_not_found", "path": str(LOG)}, indent=2))
        return 1
    results = [run_case(c) for c in CASES]
    canary_pass = all(r["pass"] for r in results)

    per_domain = {}
    for r in results:
        if r["kind"] != "expressive":
            continue
        d = r["checks"]["expected_domain"]
        per_domain.setdefault(d, {"total": 0, "pass": 0})
        per_domain[d]["total"] += 1
        per_domain[d]["pass"] += int(r["pass"])

    out = {
        "ts": int(time.time()),
        "P4_EXPRESSION_CANARY_PASS": canary_pass,
        "per_domain_pass_ratio": per_domain,
        "cases": results,
    }
    print(json.dumps(out, indent=2))
    return 0 if canary_pass else 1


if __name__ == "__main__":
    sys.exit(main())
