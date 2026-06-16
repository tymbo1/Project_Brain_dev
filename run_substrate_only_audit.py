"""
Substrate-only fluency audit — calls Selyrion's symbolic NLG pipeline
directly, with NO LLM articulator.

Mirrors the path SELYRION_SUBSTRATE_ONLY=1 takes in selyrion_api.py:
  query
    → activation_engine.infer (knowledge chains)
    → langeng_bridge.chains_to_prose (raw substrate prose)
    → cognitive_operators.pipeline.run_pipeline (ResponsePlan)
    → language_cognition.pipeline.run_language_cognition (LC.text)

Prints, per prompt:
  • SUBSTRATE_PROSE  — langeng_bridge raw prose
  • LC_PLAN          — speech_act + stance + meaning-unit count
  • LC_TEXT          — final no-LLM realized text (what substrate-only mode would emit)

Use this to assess fluency at the symbolic layer alone.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROMPTS = [
    "What is a graph?",
    "Define entropy.",
    "Why does theory matter to understanding consciousness?",
    "I feel lonely lately and the pain is hard.",
    "Help me build a practical plan with clear steps.",
    "How do I rebuild trust with a friend I care about?",
    "What is the purpose of the soul?",
    "Tell me a story about a city of dreams.",
    "Say something funny — make me laugh out loud.",
]


def main() -> int:
    from inference.activation_engine import ActivationEngine
    from langeng_bridge import chains_to_prose
    from cognitive_operators.pipeline import run_pipeline as cog_run
    from language_cognition.pipeline import run_language_cognition

    engine = ActivationEngine()
    print(f"# substrate-only fluency audit — {len(PROMPTS)} prompts")
    print(f"# t0 = {int(time.time())}\n")

    for i, q in enumerate(PROMPTS, 1):
        print("=" * 78)
        print(f"[{i}/{len(PROMPTS)}] {q}")
        print("=" * 78)
        try:
            res = engine.infer(q, max_chains=12)
            chains = res.get("chains", [])
            prose = chains_to_prose(q, chains) if chains else ""
            print(f"\n--- SUBSTRATE_PROSE ({len(prose)} chars) ---")
            print(prose[:1200] if prose else "(empty)")

            plan = cog_run(query=q, chains=chains, source_lane="knowledge")
            print(f"\n--- COG_PLAN ---")
            print(f"  operator_used:     {getattr(plan, 'operator_used', None)}")
            print(f"  ready_for_langeng: {getattr(plan, 'ready_for_langeng', None)}")
            print(f"  confidence:        {getattr(plan, 'confidence', None)}")
            plan_text = plan.to_substrate_text().strip() if hasattr(plan, "to_substrate_text") else ""
            print(f"  plan_text head:    {plan_text[:300]!r}")

            try:
                lc = run_language_cognition(query=q, response_plan=plan)
                hint = getattr(lc.plan, "expression_hint", None)
                leak = bool(getattr(lc.plan, "verbatim_capsule_leak", False))
                print(f"\n--- LC_PLAN ---")
                print(f"  speech_act:    {lc.speech_act}")
                print(f"  stance:        {lc.plan.stance}")
                print(f"  meaning_units: {len(lc.plan.meaning_units)}")
                print(f"  confidence:    {lc.confidence:.3f}")
                if hint is not None:
                    print(f"\n--- EXPRESSION_HINT ---")
                    print(f"  domain:           {hint.domain or '(none)'}")
                    print(f"  capsule_hits:     {hint.capsule_hits}")
                    print(f"  hint_stance:      {hint.stance or '(none)'}")
                    print(f"  hint_cadence:     {hint.cadence or '(none)'}")
                    print(f"  warmth/direct/play: "
                          f"{hint.warmth:.2f}/{hint.directness:.2f}/{hint.playfulness:.2f}")
                    print(f"  allow_question:   {hint.allow_question}")
                    print(f"  banned_ngrams:    {len(hint.banned_surface_ngrams)}")
                    print(f"  verbatim_leak:    {leak}")
                print(f"\n--- LC_TEXT (no-LLM realized) ---")
                print(lc.text if lc.text else "(empty)")
            except Exception as e:
                print(f"\n--- LC ERROR: {e}")
        except Exception as e:
            print(f"PROMPT ERROR: {e}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
