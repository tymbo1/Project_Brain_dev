"""
P4 polysemy-gate canary — substrate-only audit seam #7.

In-process binary witness. No LLM, no HTTP.

Asserts: when substrate produces a substantive definition/nature unit, the
polysemy clarifier ("Did you mean X as in '...', or more as '...'?") must
NOT fire. When substrate is empty and the focus is genuinely ambiguous, the
clarifier may still fire (control case for non-regression).

Cases (suppression):
  • "What is a graph?"                 — confident DEFINE, polysemous focus
  • "Define entropy."                  — confident DEFINE, polysemous focus
  • "What is the purpose of the soul?" — confident DEFINE, polysemous focus

Cases (control / non-regression):
  • "I feel lonely lately ..."         — REASSURE path, clarifier irrelevant
  • "Tell me a story about a city of dreams." — non-DEFINE path
"""
from __future__ import annotations
import json
import re
import sys

from inference.activation_engine import ActivationEngine
from cognitive_operators.pipeline import run_pipeline as cog_run
from language_cognition.pipeline import run_language_cognition

_POLY_RE = re.compile(r"did you mean .* as in .* or more as ", re.IGNORECASE)


CASES = [
    # (id, prompt, kind)
    ("poly_graph",   "What is a graph?",                  "suppress"),
    ("poly_entropy", "Define entropy.",                   "suppress"),
    ("poly_soul",    "What is the purpose of the soul?",  "suppress"),
    ("ctrl_lonely",  "I feel lonely lately and the pain is hard.", "control"),
    ("ctrl_story",   "Tell me a story about a city of dreams.",    "control"),
]


def main() -> int:
    engine = ActivationEngine()
    results = []
    total_pass = 0
    for cid, prompt, kind in CASES:
        try:
            res = engine.infer(prompt, max_chains=12)
            chains = res.get("chains", [])
            plan = cog_run(query=prompt, chains=chains, source_lane="knowledge")
            lc = run_language_cognition(query=prompt, response_plan=plan)

            units = lc.plan.meaning_units
            polysemy_unit_present = any(
                u.type == "follow_up" and "did you mean" in (u.content or "").lower()
                and " as in " in (u.content or "").lower()
                for u in units
            )
            polysemy_text_present = bool(_POLY_RE.search(lc.text or ""))
            has_definition_like = any(
                u.type in ("definition", "nature")
                and len((u.content or "").strip()) >= 30
                for u in units
            )

            if kind == "suppress":
                case_pass = (not polysemy_unit_present) and (not polysemy_text_present)
            else:
                case_pass = True  # control: emission allowed, just no crash

            if case_pass:
                total_pass += 1
            results.append({
                "id": cid,
                "kind": kind,
                "prompt": prompt,
                "speech_act": lc.speech_act,
                "polysemy_unit_present": polysemy_unit_present,
                "polysemy_text_present": polysemy_text_present,
                "has_definition_like": has_definition_like,
                "unit_types": sorted({u.type for u in units}),
                "text_tail": (lc.text or "")[-200:],
                "pass": case_pass,
            })
        except Exception as e:
            results.append({"id": cid, "kind": kind, "prompt": prompt, "error": str(e), "pass": False})

    overall = total_pass == len(CASES)
    print(json.dumps({
        "P4_POLYSEMY_GATE_CANARY_PASS": overall,
        "passed": total_pass,
        "total": len(CASES),
        "cases": results,
    }, indent=2))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
