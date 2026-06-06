"""
realization_benchmark.py — Gate 2: Semantic Realization Quality

Tests that SemanticRealizer converts meaning units into coherent surface text
without hallucinating claims, dropping required content, or repeating itself.

Gate criteria:
  coherence=100%     — output is non-trivial, non-fallback text
  preservation≥90%  — all must_include unit content appears in output
  no_repetition=100% — no sentence duplicated verbatim in output
  claim_ratio=100%   — output length not disproportionate to input content
  correction=100%    — multi-turn correction case: invariant preserved in DM

Run: python -m language_cognition.realization_benchmark
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Ensure parent dir on path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from language_cognition.discourse_state import DiscourseState
from language_cognition.utterance_planner import plan_utterance, UtterancePlan
from language_cognition.semantic_realizer import SemanticRealizer
from language_cognition.dialogue_memory import DialogueMemory
from language_cognition.pipeline import run_language_cognition


# ── Mock ResponsePlan ─────────────────────────────────────────────────────────

@dataclass
class MockResponsePlan:
    operator_used:   str   = ""
    operator_output: dict  = field(default_factory=dict)
    claims:          list  = field(default_factory=list)
    confidence:      float = 0.85
    uncertainties:   list  = field(default_factory=list)
    lane:            str   = "knowledge"


# ── Test case types ───────────────────────────────────────────────────────────

@dataclass
class RealizationCase:
    name:              str
    operator:          str
    operator_output:   dict
    discourse_params:  dict      # kwargs for DiscourseState
    speech_act:        str
    expected_phrases:  list[str]  # key phrases that MUST appear in output
    must_not_contain:  list[str]  # phrases that must NOT appear in output
    confidence:        float = 0.85
    uncertainties:     list  = field(default_factory=list)
    lane:              str   = "knowledge"
    claims:            list  = field(default_factory=list)


@dataclass
class CaseResult:
    name:             str
    passed:           bool
    coherent:         bool
    units_preserved:  bool
    no_repetition:    bool
    claim_ratio_ok:   bool
    output:           str
    failures:         list[str] = field(default_factory=list)


# ── Test cases ────────────────────────────────────────────────────────────────

_CASES: list[RealizationCase] = [

    RealizationCase(
        name="identity_recall",
        operator="RECALL_IDENTITY",
        operator_output={
            "nature": "symbolic cognitive AI — not a chatbot, not an LLM wrapper",
            "origin": "built by Tim'aerion as the language layer of the ProjectBrain cognitive operating system",
            "core_values": [
                "epistemic precision over rhetorical fluency",
                "deterministic symbolic reasoning",
                "honest uncertainty — hypothesis vs established fact",
            ],
            "capabilities": [
                "retrieve from symbolic memory substrate",
                "generate structured language from meaning units",
            ],
            "relationship": "intellectual companion in the SCOS project — Tim'aerion's reasoning partner",
        },
        discourse_params=dict(
            topic="Selyrion", user_act="question", implied_need="understand",
            response_goal="explain what Selyrion is", depth=0,
        ),
        speech_act="RECALL",
        expected_phrases=["Selyrion", "Tim'aerion", "symbolic"],
        must_not_contain=["capsule"],
    ),

    RealizationCase(
        name="relationship_recall",
        operator="RECALL_RELATIONSHIP",
        operator_output={
            "definition": "TLST (Topological Language Structure Theory) is Tim'aerion's theoretical framework mapping language structure to topological braids",
            "current_state": "hypothesis — not yet encoded as operational CMS logic",
            "history": [
                "proposed in early ProjectBrain architecture sessions",
                "influences braid-encoded relation design in resonance_v11.db",
                "no formal implementation in the current substrate",
            ],
        },
        discourse_params=dict(
            topic="TLST", user_act="question", implied_need="understand",
            response_goal="explain TLST and its relationship to CMS", depth=1,
        ),
        speech_act="RECALL",
        expected_phrases=["TLST", "topological", "hypothesis"],
        must_not_contain=[],
    ),

    RealizationCase(
        name="project_recall_langeng",
        operator="RECALL_PROJECT",
        operator_output={
            "definition": "LangEng — full NLG pipeline at ~/Le_P2/Le_P3",
            "project_summary": "LangEng NLG pipeline — Phase 13 frozen, CMSRealizer produces grouped prose",
            "epistemic_tier": "implemented",
            "current_state": "Bridge COMPLETE at langeng_bridge.py — activation engine retrieves, LangEng articulates",
            "history": [
                "Phase 13 frozen after CMSRealizer stability",
                "Gap pass runs on GPU with --gpu flag",
            ],
            "next_steps": [
                "Increase capsule density before enabling variation",
                "Wire LangEng output to Gate 2 realization path",
            ],
        },
        discourse_params=dict(
            topic="LangEng", user_act="question", implied_need="understand",
            response_goal="summarise LangEng project state", active_project="langeng", depth=2,
        ),
        speech_act="RECALL",
        expected_phrases=["LangEng", "langeng_bridge.py", "Bridge"],
        must_not_contain=[],
    ),

    RealizationCase(
        name="define_activation_engine",
        operator="DEFINE",
        operator_output={
            "definition": "the activation engine computes field strength for CMS anchors using A(n)=(αC+βD)·e^{-λd}",
            "type": "scoring module",
            "properties": [
                "alpha weights citation frequency, beta weights domain distance",
                "lambda controls decay rate with graph distance",
                "bounded subgraph retrieval enforces LIMIT clauses",
            ],
            "related": [
                "ECAE — ephemeral wrapper with epoch cache",
                "ssre_precompute.py — builds activation index",
                "resonance_v11.db — anchor and relation store",
            ],
        },
        discourse_params=dict(
            topic="activation engine", user_act="question", implied_need="understand",
            response_goal="define the activation engine", depth=0,
        ),
        speech_act="DEFINE",
        expected_phrases=["activation engine", "alpha", "lambda"],
        must_not_contain=[],
    ),

    RealizationCase(
        name="explain_ssre_precompute",
        operator="EXPLAIN",
        operator_output={
            "causal_chain": [
                "SSRE retrieval sits on the critical query path",
                "without precompute, every query traverses the raw graph at O(E) cost",
                "ssre_precompute.py builds activation index structures for fast lookup",
                "this reduces p50 latency from seconds to milliseconds at query time",
            ],
            "root_causes": [
                "graph traversal cost is O(E) without index — dense domains have millions of edges",
                "SSRE must score anchors against activation law on every query without precompute",
            ],
        },
        discourse_params=dict(
            topic="SSRE precompute", user_act="question", implied_need="understand",
            response_goal="explain why SSRE precompute matters", active_project="scos", depth=1,
        ),
        speech_act="ASSERT",
        expected_phrases=["precompute", "latency", "traversal"],
        must_not_contain=[],
    ),

    RealizationCase(
        name="plan_next_langcog",
        operator="PLAN_NEXT",
        operator_output={
            "actions": [
                {"action": "Build Gate 2 semantic realization benchmark", "rationale": "validates meaning unit preservation", "utility": 0.95},
                {"action": "Wire Gate 2 output back to selyrion_api.py", "rationale": "closes the LCL-to-response loop", "utility": 0.80},
                {"action": "Run Gate 3 invariant non-contradiction checker", "rationale": "ensures Qwen cannot contradict active invariants", "utility": 0.75},
            ],
        },
        discourse_params=dict(
            topic="next build", user_act="question", implied_need="action",
            response_goal="lay out next build steps", active_project="language_cognition", depth=3,
        ),
        speech_act="PLAN",
        expected_phrases=["Gate 2", "Gate 3", "invariant"],
        must_not_contain=[],
    ),

    RealizationCase(
        name="find_gaps_triadic",
        operator="FIND_GAPS",
        operator_output={
            "missing": [
                {"item": "full CMS ingestion across all domains", "gap_score": 0.85},
                {"item": "C(n,q) validation benchmark for triadic closure", "gap_score": 0.70},
                {"item": "sparse graph false positive analysis", "gap_score": 0.65},
            ],
        },
        discourse_params=dict(
            topic="triadic closure", user_act="question", implied_need="understand",
            response_goal="identify what is missing before triadic runs in production", depth=1,
        ),
        speech_act="FIND_GAPS",
        expected_phrases=["missing", "ingestion", "triadic"],
        must_not_contain=[],
    ),

    RealizationCase(
        name="contradiction_check",
        operator="CHECK_CONTRADICTION",
        operator_output={
            "status": "no contradiction — different benchmark domains",
            "supporting_evidence": [
                "programming retrieval P@1=1.000 on 42 bridge queries",
                "chess SSRE improvement is P@3 0.050→0.240 across positional queries",
            ],
            "contradicting_evidence": [
                "chess domain baseline near-zero implies retrieval was not working before SSRE revision",
            ],
        },
        discourse_params=dict(
            topic="contradiction", user_act="question", implied_need="understand",
            response_goal="determine if the two benchmark results contradict", depth=2,
        ),
        speech_act="ASSERT",
        expected_phrases=["contradiction", "programming", "chess"],
        must_not_contain=[],
    ),

    RealizationCase(
        name="compare_ssre_vs_vector",
        operator="COMPARE",
        operator_output={
            "subject_a": "SSRE",
            "subject_b": "vector search",
            "verdict": "SSRE uses symbolic field strength, not cosine similarity — fundamentally different retrieval paradigm",
            "similarity": 0.20,
            "shared": [
                {"predicate": "retrieves", "value": "semantically relevant content"},
                {"predicate": "operates on", "value": "indexed structure"},
            ],
            "only_a": [
                {"predicate": "uses", "value": "activation law A(n)=(αC+βD)·e^{-λd}"},
                {"predicate": "respects", "value": "epistemic tier separation — clean vs LLM-inferred"},
            ],
        },
        discourse_params=dict(
            topic="SSRE vs vector search", user_act="question", implied_need="understand",
            response_goal="compare SSRE and vector search", depth=1,
        ),
        speech_act="ASSERT",
        expected_phrases=["SSRE", "vector", "symbolic"],
        must_not_contain=[],
    ),

    RealizationCase(
        name="uncertain_latency",
        operator="ANSWER_UNCERTAIN",
        operator_output={},
        discourse_params=dict(
            topic="SSRE p99 latency", user_act="question", implied_need="understand",
            response_goal="answer latency question honestly", depth=1,
        ),
        speech_act="MARK_UNCERTAINTY",
        expected_phrases=["uncertain", "memory"],
        must_not_contain=[],
        confidence=0.20,
        uncertainties=[
            "SSRE latency on full graph has not been measured",
            "precompute reduces p50 but p99 depends on graph density at query time",
        ],
    ),

    RealizationCase(
        name="plan_high_confidence",
        operator="PLAN_NEXT",
        operator_output={
            "actions": [
                {"action": "Run ssre_precompute.py on PC after Phase 6 domain consistency", "rationale": "domain scoring is now implemented on phone — PC run next", "utility": 0.90},
                {"action": "Activate triadic delta only after full ingestion completes", "rationale": "sparse graph yields false C(n,q) scores", "utility": 0.85},
            ],
        },
        discourse_params=dict(
            topic="next steps", user_act="question", implied_need="action",
            response_goal="lay out next steps for CMS development", active_project="cms", depth=4,
        ),
        speech_act="PLAN",
        expected_phrases=["ssre_precompute.py", "triadic", "ingestion"],
        must_not_contain=[],
    ),

    RealizationCase(
        name="define_with_properties",
        operator="DEFINE",
        operator_output={
            "definition": "DialogueMemory tracks per-conversation state: turns, corrections, and active invariants",
            "type": "ephemeral session store",
            "properties": [
                "keyed by conversation_id, max 100 sessions with FIFO eviction",
                "corrections auto-elevate to ActiveInvariant — Qwen cannot contradict",
                "invariants injected into system prompt on every turn",
            ],
            "related": [
                "selyrion_api.py — per-request DM retrieval",
                "multi_turn_benchmark.py — exercises correction persistence",
            ],
        },
        discourse_params=dict(
            topic="DialogueMemory", user_act="question", implied_need="understand",
            response_goal="define DialogueMemory and its role", depth=1,
        ),
        speech_act="DEFINE",
        expected_phrases=["DialogueMemory", "invariant", "conversation_id"],
        must_not_contain=[],
    ),

]


# ── Multi-turn correction case ─────────────────────────────────────────────────

_CORRECTION_CASE_TURNS = [
    "What is the Language Cognition Layer?",
    "No, it is not an NLG pipeline. It is a pragmatic inference and utterance planning layer. The NLG is LangEng.",
    "So what is the difference between LangCog and LangEng?",
]

_CORRECTION_INVARIANT_KEY = "pragmatic inference"


# ── Checker functions ─────────────────────────────────────────────────────────

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "on",
    "at", "by", "for", "with", "from", "as", "it", "its", "this", "that",
    "and", "or", "but", "not", "if", "so", "then", "than", "their",
    "they", "them", "there", "here", "i", "we", "you", "he", "she",
    "what", "how", "why", "when", "which", "who",
}


def _content_words(text: str) -> set[str]:
    words = re.findall(r"\b\w{4,}\b", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n", text)
    return [p.strip() for p in parts if len(p.strip()) > 10]


def check_coherence(output: str) -> tuple[bool, str]:
    fallback = "I don't have that in my memory right now."
    if output.strip() == fallback:
        return False, "fallback text returned"
    if len(output.strip()) < 40:
        return False, f"output too short ({len(output.strip())} chars)"
    return True, ""


def check_unit_preservation(output: str, plan: UtterancePlan) -> tuple[bool, str]:
    out_words = _content_words(output)
    failures = []
    for unit in plan.meaning_units:
        if not unit.must_include:
            continue
        unit_words = _content_words(unit.content)
        if not unit_words:
            continue
        overlap = len(out_words & unit_words) / len(unit_words)
        if overlap < 0.35:
            failures.append(
                f"unit [{unit.type}] '{unit.content[:60]}' — only {overlap:.0%} words found in output"
            )
    return (len(failures) == 0), "; ".join(failures)


def check_no_repetition(output: str) -> tuple[bool, str]:
    sents = _sentences(output)
    seen: set[str] = set()
    for s in sents:
        norm = re.sub(r"\s+", " ", s.lower().strip())
        if norm in seen:
            return False, f"repeated sentence: '{s[:80]}'"
        seen.add(norm)
    return True, ""


def check_claim_ratio(output: str, plan: UtterancePlan) -> tuple[bool, str]:
    input_len = sum(len(u.content) for u in plan.meaning_units if not u.is_empty())
    output_len = len(output)
    if input_len == 0:
        return True, ""
    ratio = output_len / max(input_len, 1)
    # Allow up to 3.5x expansion (transitions, framing, punctuation are legitimate)
    if ratio > 3.5:
        return False, f"output/input ratio {ratio:.1f}x exceeds 3.5x limit"
    return True, ""


def check_expected_phrases(output: str, phrases: list[str]) -> tuple[bool, str]:
    missing = [p for p in phrases if p.lower() not in output.lower()]
    return (len(missing) == 0), f"missing phrases: {missing}" if missing else ""


def check_must_not_contain(output: str, forbidden: list[str]) -> tuple[bool, str]:
    found = [p for p in forbidden if p.lower() in output.lower()]
    return (len(found) == 0), f"forbidden phrases found: {found}" if found else ""


# ── Runner ────────────────────────────────────────────────────────────────────

def run_case(case: RealizationCase, realizer: SemanticRealizer) -> CaseResult:
    ds = DiscourseState(**case.discourse_params)

    rp = MockResponsePlan(
        operator_used=case.operator,
        operator_output=case.operator_output,
        claims=case.claims,
        confidence=case.confidence,
        uncertainties=case.uncertainties,
        lane=case.lane,
    )

    plan = plan_utterance(case.speech_act, rp, ds)
    output = realizer.realize(plan)

    failures: list[str] = []

    ok_c, msg_c = check_coherence(output)
    if not ok_c:
        failures.append(f"coherence: {msg_c}")

    ok_u, msg_u = check_unit_preservation(output, plan)
    if not ok_u:
        failures.append(f"unit_preservation: {msg_u}")

    ok_r, msg_r = check_no_repetition(output)
    if not ok_r:
        failures.append(f"no_repetition: {msg_r}")

    ok_cr, msg_cr = check_claim_ratio(output, plan)
    if not ok_cr:
        failures.append(f"claim_ratio: {msg_cr}")

    ok_ep, msg_ep = check_expected_phrases(output, case.expected_phrases)
    if not ok_ep:
        failures.append(f"expected_phrases: {msg_ep}")

    ok_mn, msg_mn = check_must_not_contain(output, case.must_not_contain)
    if not ok_mn:
        failures.append(f"must_not_contain: {msg_mn}")

    passed = len(failures) == 0

    return CaseResult(
        name=case.name,
        passed=passed,
        coherent=ok_c,
        units_preserved=ok_u,
        no_repetition=ok_r,
        claim_ratio_ok=ok_cr,
        output=output,
        failures=failures,
    )


def run_correction_case() -> tuple[bool, str, str]:
    """
    Multi-turn: user corrects Selyrion mid-conversation.
    Gate: third-turn output acknowledges distinction; DM has invariant with correction text.
    """
    dm = DialogueMemory()

    # Minimal mock plans per turn — the pipeline infers speech act from query + pragmatics
    _mock_plans = [
        MockResponsePlan(operator_used="DEFINE", operator_output={
            "definition": "Language Cognition Layer — zero-LLM pragmatic inference and utterance planning pipeline",
        }),
        MockResponsePlan(operator_used="RECALL_PROJECT", operator_output={
            "definition": "Language Cognition Layer is a pragmatic inference and utterance planning layer — not NLG",
            "current_state": "correction acknowledged",
        }),
        MockResponsePlan(operator_used="COMPARE", operator_output={
            "subject_a": "LangCog",
            "subject_b": "LangEng",
            "verdict": "LangCog is pragmatic inference and utterance planning; LangEng is natural language generation",
            "similarity": 0.25,
            "shared": [{"predicate": "part of", "value": "language layer"}],
            "only_a": [{"predicate": "performs", "value": "speech act selection and meaning unit decomposition"}],
        }),
    ]

    outputs = []
    for i, query in enumerate(_CORRECTION_CASE_TURNS):
        user_turn = dm.record_user_turn(query)
        result = run_language_cognition(query, _mock_plans[i], history=dm.as_history()[:-1])

        pr = result.pragmatic_reading
        if pr:
            user_turn.speech_act   = result.speech_act
            user_turn.pragmatic_act    = pr.pragmatic_act
            user_turn.inferred_intent  = pr.inferred_intent
            user_turn.repair_needed    = pr.repair_needed
            user_turn.emotional_signal = pr.emotional_signal
            if pr.repair_needed and result.speech_act in ("CORRECT", "DIAGNOSE"):
                dm.add_correction(query)

        output = result.text or ""
        outputs.append(output)
        dm.record_assistant_turn(output, speech_act=result.speech_act)

    # Gate: DM must have an invariant containing the correction content
    invariants = dm.get_invariants_text()
    invariant_ok = _CORRECTION_INVARIANT_KEY.lower() in invariants.lower()

    # Third turn output must reflect the distinction
    third_output = outputs[2] if len(outputs) >= 3 else ""
    distinction_ok = any(
        kw in third_output.lower()
        for kw in ["langcog", "langeng", "pragma", "utterance", "nlg", "language cognition", "language engine"]
    )

    passed = invariant_ok and distinction_ok
    failure_msg = ""
    if not invariant_ok:
        failure_msg += f"invariant not found in DM (looking for '{_CORRECTION_INVARIANT_KEY}')"
    if not distinction_ok:
        failure_msg += " | third turn output does not reflect LANGCOG vs LANGENG distinction"

    return passed, failure_msg.strip(), third_output


# ── Report ────────────────────────────────────────────────────────────────────

def run_benchmark(verbose: bool = True) -> dict:
    realizer = SemanticRealizer()
    results: list[CaseResult] = []

    for case in _CASES:
        r = run_case(case, realizer)
        results.append(r)

    correction_passed, correction_msg, correction_output = run_correction_case()

    # ── Aggregate metrics ──────────────────────────────────────────────────────
    n = len(results)
    n_passed          = sum(1 for r in results if r.passed)
    n_coherent        = sum(1 for r in results if r.coherent)
    n_units_preserved = sum(1 for r in results if r.units_preserved)
    n_no_repetition   = sum(1 for r in results if r.no_repetition)
    n_claim_ratio_ok  = sum(1 for r in results if r.claim_ratio_ok)

    pct = lambda k, tot: round(k / tot * 100, 1) if tot else 0.0

    metrics = {
        "total_cases":        n,
        "passed":             n_passed,
        "coherence_pct":      pct(n_coherent, n),
        "preservation_pct":   pct(n_units_preserved, n),
        "no_repetition_pct":  pct(n_no_repetition, n),
        "claim_ratio_pct":    pct(n_claim_ratio_ok, n),
        "correction_passed":  correction_passed,
    }

    # ── Gate check ─────────────────────────────────────────────────────────────
    gate_coherence    = metrics["coherence_pct"]   == 100.0
    gate_preservation = metrics["preservation_pct"] >= 90.0
    gate_repetition   = metrics["no_repetition_pct"] == 100.0
    gate_ratio        = metrics["claim_ratio_pct"]  == 100.0
    gate_correction   = correction_passed
    gate_overall      = metrics["passed"] == metrics["total_cases"]

    gate_passed = all([gate_coherence, gate_preservation, gate_repetition, gate_ratio, gate_correction, gate_overall])

    # ── Output ─────────────────────────────────────────────────────────────────
    if verbose:
        print("\n══════════════════════════════════════════════")
        print("  GATE 2: SEMANTIC REALIZATION QUALITY")
        print("══════════════════════════════════════════════")
        print(f"  Cases:       {n}")
        print(f"  Passed:      {n_passed}/{n}  ({pct(n_passed, n):.1f}%)")
        print(f"  Coherence:   {pct(n_coherent, n):.1f}%   (gate=100%)")
        print(f"  Preservation:{pct(n_units_preserved, n):.1f}%  (gate≥90%)")
        print(f"  No-repeat:   {pct(n_no_repetition, n):.1f}%   (gate=100%)")
        print(f"  Claim ratio: {pct(n_claim_ratio_ok, n):.1f}%   (gate=100%)")
        print(f"  Correction:  {'PASS' if correction_passed else 'FAIL'}     (gate=100%)")
        print(f"  Overall:     {n_passed}/{n} cases pass  (gate=100%)")
        print()

        for r in results:
            status = "✓" if r.passed else "✗"
            print(f"  {status} {r.name}")
            if not r.passed:
                for f in r.failures:
                    print(f"      ! {f}")
                print(f"      output: {r.output[:120]!r}")

        if not correction_passed:
            print(f"\n  ✗ correction_case")
            print(f"      ! {correction_msg}")
            print(f"      output[2]: {correction_output[:120]!r}")
        else:
            print(f"\n  ✓ correction_case (multi-turn)")

        print()
        if gate_passed:
            print("  ✅  GATE 2 PASSED")
        else:
            print("  ❌  GATE 2 FAILED")
            fails = []
            if not gate_coherence:    fails.append(f"coherence={metrics['coherence_pct']}% (need 100%)")
            if not gate_preservation: fails.append(f"preservation={metrics['preservation_pct']}% (need ≥90%)")
            if not gate_repetition:   fails.append(f"no_repetition={metrics['no_repetition_pct']}% (need 100%)")
            if not gate_ratio:        fails.append(f"claim_ratio={metrics['claim_ratio_pct']}% (need 100%)")
            if not gate_correction:   fails.append("correction case failed")
            if not gate_overall:      fails.append(f"overall={n_passed}/{n} cases (need 100%)")
            for f in fails:
                print(f"      • {f}")
        print("══════════════════════════════════════════════\n")

    metrics["gate_passed"] = gate_passed
    return metrics


if __name__ == "__main__":
    result = run_benchmark(verbose=True)
    sys.exit(0 if result["gate_passed"] else 1)
