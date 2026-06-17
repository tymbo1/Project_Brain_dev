"""
P4 query-echo canary — substrate-only audit seam #4.

In-process binary witness. No LLM, no HTTP.

Validates that the LC pipeline drops MeaningUnits whose content is a verbatim
echo of the user query, so the realizer cannot emit the user's own words back
as a property / follow-up / relation.

Cases:
  • Echo cases — real prompts where substrate seeds the query as a property
    object. Assert: no unit.content normalizes to query; final text does NOT
    end with verbatim query.
  • Control cases — prompts whose substantive units (definitions, relations)
    must survive the filter.

Output:
  Top-level JSON with P4_QUERY_ECHO_CANARY_PASS, per-case checks.
"""
from __future__ import annotations
import json
import re
import sys

from inference.activation_engine import ActivationEngine
from cognitive_operators.pipeline import run_pipeline as cog_run
from language_cognition.pipeline import run_language_cognition

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


CASES = [
    # (id, prompt, kind, must_keep_unit_types)
    ("echo_define_entropy", "Define entropy.", "echo", ()),
    ("echo_what_is_graph", "What is a graph?", "control", ("definition",)),
    ("echo_lonely",        "I feel lonely lately and the pain is hard.", "control", ("reassurance",)),
    ("echo_plan_trust",    "How do I rebuild trust with a friend I care about?", "control", ("proposal",)),
    ("echo_story_dreams",  "Tell me a story about a city of dreams.", "control", ()),
    ("echo_funny",         "Say something funny — make me laugh out loud.", "control", ()),
]


def main() -> int:
    engine = ActivationEngine()
    results = []
    total_pass = 0
    for cid, prompt, kind, must_keep in CASES:
        try:
            res = engine.infer(prompt, max_chains=12)
            chains = res.get("chains", [])
            plan = cog_run(query=prompt, chains=chains, source_lane="knowledge")
            lc = run_language_cognition(query=prompt, response_plan=plan)
            nq = _norm(prompt)

            unit_echo_present = any(_norm(u.content or "") == nq for u in lc.plan.meaning_units)
            text_norm = _norm(lc.text)
            text_ends_with_query = text_norm.endswith(nq) if nq and text_norm else False
            kept_types = {u.type for u in lc.plan.meaning_units}
            must_keep_ok = all(t in kept_types for t in must_keep)

            checks = {
                "unit_echo_absent": not unit_echo_present,
                "text_no_trailing_echo": not text_ends_with_query,
                "must_keep_types_present": must_keep_ok,
            }
            case_pass = all(checks.values())
            if case_pass:
                total_pass += 1
            results.append({
                "id": cid,
                "kind": kind,
                "prompt": prompt,
                "speech_act": lc.speech_act,
                "checks": checks,
                "unit_types": sorted(kept_types),
                "text_tail": lc.text[-180:] if lc.text else "",
                "pass": case_pass,
            })
        except Exception as e:
            results.append({"id": cid, "kind": kind, "prompt": prompt, "error": str(e), "pass": False})

    overall = total_pass == len(CASES)
    print(json.dumps({
        "P4_QUERY_ECHO_CANARY_PASS": overall,
        "passed": total_pass,
        "total": len(CASES),
        "cases": results,
    }, indent=2))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
