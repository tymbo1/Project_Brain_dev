"""
P4 capsule reachability canary — A′ seam #1 witness.

Calls run_language_cognition directly (no LLM, no HTTP) on empty-substrate
expressive prompts and asserts structural invariants of the A′ feature
extraction path.

Pass criteria (binary, all must hold per case):
  - hint_present      : plan.expression_hint is not None
  - hint_routed       : when prompt is expressive, hint.capsule_hits > 0 AND
                        hint.domain matches expected_domain
  - opener_emitted    : first meaning_unit type in {reassurance, proposal,
                        invitation} AND content non-empty
  - opener_routed     : when capsule_hits > 0, opener content matches the
                        cadence-routed _HINT_OPENERS table (not the generic
                        B-fallback). When capsule_hits == 0, opener falls
                        back to generic — also a pass.
  - leak_free         : plan.verbatim_capsule_leak is False

Doctrine:
  Capsules are expressive-control substrate, NOT answer content. This canary
  proves the 518 language_expression capsules are reachable from the
  substrate-only path (closing seam #1) WITHOUT verbatim text leaking into
  output.

Exit 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field


CASES = [
    {
        "id": "er_lonely",
        "expected_domain": "emotional_resonance",
        "speech_act_hint": "REASSURE",
        "prompt": "I feel lonely lately and the pain is hard.",
    },
    {
        "id": "rw_rebuild_trust",
        "expected_domain": "relational_warmth",
        "speech_act_hint": "PLAN",
        "prompt": "How do I rebuild trust with a friend I care about?",
    },
    {
        "id": "ic_help_understand",
        "expected_domain": "intellectual_curiosity",
        "speech_act_hint": "PLAN",
        "prompt": "Help me understand how knowledge becomes meaning.",
    },
    {
        "id": "pg_plan_steps",
        "expected_domain": "practical_grounding",
        "speech_act_hint": "PLAN",
        "prompt": "Help me build a practical plan with clear steps.",
    },
    {
        "id": "si_soul_purpose",
        "expected_domain": "spiritual_inquiry",
        "speech_act_hint": "ASK_FOLLOWUP",
        "prompt": "What is the purpose of the soul?",
    },
    {
        "id": "ce_story_dreams",
        "expected_domain": "creative_engagement",
        "speech_act_hint": "ASK_FOLLOWUP",
        "prompt": "Tell me a story about a city of dreams.",
    },
    {
        "id": "hl_funny_laugh",
        "expected_domain": "humour_lightness",
        "speech_act_hint": "ASK_FOLLOWUP",
        "prompt": "Say something funny — make me laugh out loud.",
    },
    {
        "id": "control_empty_no_domain",
        "expected_domain": None,   # no domain → capsule_hits=0 expected
        "speech_act_hint": None,
        "prompt": "asdfqwer zxcvbnm.",
    },
]


def _empty_response_plan():
    from cognitive_operators.response_planner import ResponsePlan
    return ResponsePlan(speech_act="UNCERTAIN", claims=[], confidence=0.0)


def _run_case(case: dict) -> dict:
    from language_cognition.pipeline import run_language_cognition
    from language_cognition.repair_engine import _HINT_OPENERS

    rplan = _empty_response_plan()
    lc = run_language_cognition(query=case["prompt"], response_plan=rplan)
    plan = lc.plan
    hint = getattr(plan, "expression_hint", None)
    leak = bool(getattr(plan, "verbatim_capsule_leak", False))

    hint_present = hint is not None
    expected_domain = case["expected_domain"]
    if expected_domain is None:
        hint_routed = (hint_present and hint.capsule_hits == 0)
    else:
        hint_routed = (
            hint_present
            and hint.domain == expected_domain
            and hint.capsule_hits > 0
        )

    first = plan.meaning_units[0] if plan.meaning_units else None
    opener_emitted = bool(
        first is not None
        and first.type in ("reassurance", "proposal", "invitation")
        and (first.content or "").strip()
    )

    if hint_present and hint.capsule_hits > 0 and opener_emitted:
        key = (lc.speech_act, hint.cadence)
        routed_tuple = _HINT_OPENERS.get(key)
        opener_routed = bool(
            routed_tuple is not None
            and first.content.strip() == routed_tuple[0].strip()
        )
    else:
        opener_routed = True

    leak_free = (leak is False)

    passed = all((hint_present, hint_routed, opener_emitted, opener_routed, leak_free))
    return {
        "id":            case["id"],
        "speech_act":    lc.speech_act,
        "hint_domain":   getattr(hint, "domain", "") if hint else "",
        "capsule_hits":  getattr(hint, "capsule_hits", 0) if hint else 0,
        "cadence":       getattr(hint, "cadence", "") if hint else "",
        "stance":        getattr(hint, "stance", "") if hint else "",
        "first_unit":    first.type if first else "",
        "opener_head":   (first.content[:80] if first else ""),
        "leak":          leak,
        "checks": {
            "hint_present":    hint_present,
            "hint_routed":     hint_routed,
            "opener_emitted":  opener_emitted,
            "opener_routed":   opener_routed,
            "leak_free":       leak_free,
        },
        "pass": passed,
    }


def main() -> int:
    results = [_run_case(c) for c in CASES]
    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    summary = {
        "P4_CAPSULE_REACHABILITY_CANARY_PASS": passed == total,
        "passed": passed,
        "total":  total,
        "results": results,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
