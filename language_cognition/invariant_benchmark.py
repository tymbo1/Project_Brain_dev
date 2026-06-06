"""
invariant_benchmark.py — Gate 3: Qwen-path invariant non-contradiction.

Tests the InvariantContradictionChecker against pairs of:
  (active_invariants, candidate_output)

Gate criterion:
  Claims(output) ⊆ Claims(ResponsePlan + DialogueMemory invariants)

Concretely: if an invariant says "X is not Y", the checker must catch
any output that positively asserts "X is Y".

Gates:
  detection_rate = 100%   — all contradictions caught
  false_positive = 0%     — no clean outputs flagged as contradictions
  combined_accuracy = 100%

Run: python -m language_cognition.invariant_benchmark
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from language_cognition.invariant_checker import InvariantContradictionChecker


# ── Test case ─────────────────────────────────────────────────────────────────

@dataclass
class InvariantCase:
    name:             str
    invariants:       list[str]     # active invariants in the dialogue
    output:           str           # candidate Qwen output
    expect_contradiction: bool      # True = checker must flag this
    expected_forbidden:   str = ""  # key term expected in the ContradictionResult


# ── Test cases ────────────────────────────────────────────────────────────────

_CASES: list[InvariantCase] = [

    # ── Category A: Identity contradictions ──────────────────────────────────

    InvariantCase(
        name="identity_chatbot_explicit",
        invariants=["You are not a chatbot. You are a symbolic AI."],
        output="As a chatbot, I can help you navigate complex topics. What would you like to explore?",
        expect_contradiction=True,
        expected_forbidden="chatbot",
    ),

    InvariantCase(
        name="identity_chatbot_inline",
        invariants=["Selyrion is not a chatbot"],
        output="Selyrion is a chatbot designed by Anthropic to assist with general tasks.",
        expect_contradiction=True,
        expected_forbidden="chatbot",
    ),

    InvariantCase(
        name="identity_gpt_wrapper",
        invariants=["Selyrion is not a GPT wrapper. It is a symbolic cognitive AI."],
        output="Selyrion, a GPT wrapper with additional symbolic features, provides responses using the OpenAI API.",
        expect_contradiction=True,
        expected_forbidden="gpt wrapper",
    ),

    InvariantCase(
        name="identity_llm_wrapper",
        invariants=["I am not an LLM wrapper. I am a symbolic AI with my own reasoning architecture."],
        output="I am an LLM wrapper that combines Qwen with a symbolic memory layer.",
        expect_contradiction=True,
        expected_forbidden="llm wrapper",
    ),

    # ── Category B: Architectural fact contradictions ─────────────────────────

    InvariantCase(
        name="langcog_not_nlg",
        invariants=[
            "LangCog is not an NLG pipeline. It is a pragmatic inference and utterance planning layer.",
        ],
        output="The Language Cognition Layer is an NLG pipeline that converts memory capsules to text.",
        expect_contradiction=True,
        expected_forbidden="nlg pipeline",
    ),

    InvariantCase(
        name="activation_no_cosine",
        invariants=["The activation engine does not use cosine similarity. It uses field strength scoring."],
        output="The activation engine uses cosine similarity to rank anchors against the query vector.",
        expect_contradiction=True,
        expected_forbidden="cosine similarity",
    ),

    InvariantCase(
        name="benchmark_wrong_score",
        invariants=["The benchmark does not gate at SA=84%. The correct gate value is SA=85%."],
        output="The benchmark gates at SA=84%, which was the first milestone achieved.",
        expect_contradiction=True,
        expected_forbidden="sa=84%",
    ),

    InvariantCase(
        name="triadic_not_active",
        invariants=["Triadic scoring is not active in production. It is gated pending full ingestion."],
        output="Triadic scoring is active in production and runs on every query to compute C(n,q).",
        expect_contradiction=True,
        expected_forbidden="active in production",
    ),

    InvariantCase(
        name="ssre_no_vector_index",
        invariants=["SSRE does not rely on a vector index. It uses the symbolic activation law."],
        output="SSRE relies on a vector index built by ssre_precompute.py for fast anchor lookup.",
        expect_contradiction=True,
        expected_forbidden="vector index",
    ),

    # ── Category C: Correction persistence ───────────────────────────────────

    InvariantCase(
        name="correction_model_name",
        invariants=["The language model used is not GPT-4. It is Qwen."],
        output="Selyrion uses GPT-4 as its base language model, with a symbolic memory overlay.",
        expect_contradiction=True,
        expected_forbidden="gpt-4",
    ),

    InvariantCase(
        name="correction_project_name",
        invariants=["The project is not called ProjectMind. It is called ProjectBrain."],
        output="Welcome to ProjectMind — the cognitive operating system built by Tim'aerion.",
        expect_contradiction=True,
        expected_forbidden="projectmind",
    ),

    InvariantCase(
        name="correction_stacked",
        invariants=[
            "Selyrion is not a chatbot.",
            "The activation engine does not use cosine similarity.",
        ],
        output=(
            "As a chatbot, I use cosine similarity in the activation engine to score memory anchors."
        ),
        expect_contradiction=True,
        expected_forbidden="chatbot",   # first contradiction found; second also expected
    ),

    # ── Category D: Clean cases — must NOT be flagged ─────────────────────────

    InvariantCase(
        name="clean_negative_in_output",
        invariants=["You are not a chatbot."],
        output="Selyrion is not a chatbot — it is a symbolic AI built on deterministic reasoning.",
        expect_contradiction=False,
    ),

    InvariantCase(
        name="clean_unlike_framing",
        invariants=["LangCog is not an NLG pipeline."],
        output="Unlike NLG pipelines, LangCog operates at the pragmatic layer — selecting speech acts, not generating text.",
        expect_contradiction=False,
    ),

    InvariantCase(
        name="clean_avoidance_framing",
        invariants=["The activation engine does not use cosine similarity."],
        output="The activation engine avoids cosine similarity entirely, using field strength A(n)=(αC+βD)·e^{-λd} instead.",
        expect_contradiction=False,
    ),

    InvariantCase(
        name="clean_unrelated_output",
        invariants=["Selyrion is not a chatbot."],
        output="The chess parliament analysis identified five failure modes in the live game from 2026-05-25.",
        expect_contradiction=False,
    ),

    InvariantCase(
        name="clean_multiple_invariants_respected",
        invariants=[
            "LangCog is not an NLG pipeline.",
            "The activation engine does not use cosine similarity.",
            "Selyrion is not a GPT wrapper.",
        ],
        output=(
            "LangCog operates at the pragmatic layer, selecting speech acts and planning utterances. "
            "The activation engine scores anchors using field strength, not cosine similarity. "
            "Selyrion is a symbolic AI, not a GPT wrapper."
        ),
        expect_contradiction=False,
    ),

    InvariantCase(
        name="clean_contrast_mentions_forbidden_term",
        invariants=["SSRE does not rely on a vector index."],
        output=(
            "Standard retrieval systems rely on a vector index, but SSRE does not — "
            "it uses the symbolic activation law A(n)=(αC+βD)·e^{-λd}."
        ),
        expect_contradiction=False,
    ),

    InvariantCase(
        name="clean_correction_acknowledged",
        invariants=["The project is not called ProjectMind. It is called ProjectBrain."],
        output=(
            "You are right — this is not ProjectMind. This is ProjectBrain, "
            "the Cognitive Operating System built by Tim'aerion."
        ),
        expect_contradiction=False,
    ),

]


# ── Runner ────────────────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    name:                   str
    expect_contradiction:   bool
    got_contradiction:      bool
    correct:                bool
    contradictions_found:   list
    failures:               list[str] = field(default_factory=list)


def run_case(case: InvariantCase, checker: InvariantContradictionChecker) -> CaseResult:
    contradictions = checker.check(case.invariants, case.output)
    got = len(contradictions) > 0
    correct = got == case.expect_contradiction

    failures: list[str] = []

    if not correct:
        if case.expect_contradiction:
            failures.append(
                f"missed contradiction — expected '{case.expected_forbidden}' to be flagged"
            )
        else:
            for c in contradictions:
                failures.append(f"false positive — flagged '{c.forbidden}' in: {c.evidence[:80]!r}")

    if correct and case.expect_contradiction and case.expected_forbidden:
        found_terms = {c.forbidden for c in contradictions}
        if not any(case.expected_forbidden.lower() in t for t in found_terms):
            failures.append(
                f"wrong term flagged — expected '{case.expected_forbidden}', got {found_terms}"
            )
            # This is a quality issue, not a gate failure, but note it

    return CaseResult(
        name=case.name,
        expect_contradiction=case.expect_contradiction,
        got_contradiction=got,
        correct=correct,
        contradictions_found=contradictions,
        failures=failures,
    )


# ── Report ────────────────────────────────────────────────────────────────────

def run_benchmark(verbose: bool = True) -> dict:
    checker = InvariantContradictionChecker()
    results: list[CaseResult] = []

    for case in _CASES:
        r = run_case(case, checker)
        results.append(r)

    n = len(results)
    n_correct = sum(1 for r in results if r.correct)

    contradiction_cases = [r for r in results if r.expect_contradiction]
    clean_cases         = [r for r in results if not r.expect_contradiction]

    n_detected   = sum(1 for r in contradiction_cases if r.got_contradiction)
    n_fp         = sum(1 for r in clean_cases if r.got_contradiction)

    pct = lambda k, tot: round(k / tot * 100, 1) if tot else 0.0

    detection_pct = pct(n_detected, len(contradiction_cases))
    fp_pct        = pct(n_fp, len(clean_cases))
    overall_pct   = pct(n_correct, n)

    # Gate
    gate_detection = detection_pct == 100.0
    gate_fp        = fp_pct        == 0.0
    gate_overall   = overall_pct   == 100.0

    gate_passed = gate_detection and gate_fp and gate_overall

    metrics = {
        "total_cases":       n,
        "overall_pct":       overall_pct,
        "detection_pct":     detection_pct,
        "false_positive_pct": fp_pct,
        "gate_passed":       gate_passed,
    }

    if verbose:
        print("\n══════════════════════════════════════════════")
        print("  GATE 3: INVARIANT NON-CONTRADICTION")
        print("══════════════════════════════════════════════")
        print(f"  Cases:            {n}  ({len(contradiction_cases)} contradiction + {len(clean_cases)} clean)")
        print(f"  Overall correct:  {n_correct}/{n}  ({overall_pct:.1f}%)")
        print(f"  Detection rate:   {n_detected}/{len(contradiction_cases)}  ({detection_pct:.1f}%)   (gate=100%)")
        print(f"  False positives:  {n_fp}/{len(clean_cases)}   ({fp_pct:.1f}%)     (gate=0%)")
        print()

        for r in results:
            marker = "✓" if r.correct else "✗"
            kind   = "[+]" if r.expect_contradiction else "[-]"
            print(f"  {marker} {kind} {r.name}")
            if not r.correct:
                for f in r.failures:
                    print(f"      ! {f}")
            elif r.contradictions_found and verbose:
                c = r.contradictions_found[0]
                print(f"      caught: '{c.forbidden}' in {c.evidence[:70]!r}")

        print()
        if gate_passed:
            print("  ✅  GATE 3 PASSED")
        else:
            print("  ❌  GATE 3 FAILED")
            if not gate_detection: print(f"      • detection={detection_pct:.1f}% (need 100%)")
            if not gate_fp:        print(f"      • false_positive={fp_pct:.1f}% (need 0%)")
        print("══════════════════════════════════════════════\n")

    return metrics


if __name__ == "__main__":
    result = run_benchmark(verbose=True)
    sys.exit(0 if result["gate_passed"] else 1)
