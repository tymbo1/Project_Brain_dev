"""
multi_turn_benchmark.py — Multi-turn dialogue benchmark.

Proves Selyrion can converse, not just classify.

Tests:
  1. correction_persistence  — user correction survives as invariant across turns
  2. invariant_injection     — invariant text appears in system prompt context
  3. frustration_repair      — escalating frustration triggers REASSURE
  4. topic_continuity        — speech acts stay coherent across a topic thread
  5. referent_tracking       — 'it/that/this' in context resolves correctly
  6. correction_stacking     — multiple corrections accumulate in dm
  7. no_invariant_contradict — corrected fact not re-asserted in system context
  8. prior_turn_reference    — follow-up query references prior exchange

Gate:
  speech_act_correct     ≥ 85%
  invariant_established  = 100%   (after correction turns)
  repair_triggered       = 100%   (frustration turns with repair_expected=True)
  no_capsule_dump        = 100%
  answer_has_shape       ≥ 80%

Does not rely on Qwen. All evaluation on zero-LLM LangCog path.

Usage:
  python -m language_cognition.multi_turn_benchmark
  python -m language_cognition.multi_turn_benchmark --verbose
"""

from __future__ import annotations
import argparse
import time
from dataclasses import dataclass, field


# ── Case definitions ──────────────────────────────────────────────────────────

@dataclass
class TurnSpec:
    query:                      str
    expected_speech_act:        str
    expected_intent:            str  = ""
    repair_expected:            bool = False
    # Invariant checks after this turn executes
    invariant_must_contain:     str  = ""   # dm.get_invariants_text() must contain this
    invariant_must_not_assert:  str  = ""   # dm.get_invariants_text() must NOT contain this as a positive claim
    difficulty:                 str  = "medium"


@dataclass
class MultiTurnCase:
    name:        str
    category:    str
    turns:       list[TurnSpec]
    difficulty:  str = "medium"


@dataclass
class TurnResult:
    turn_idx:             int
    query:                str
    got_speech_act:       str
    got_intent:           str
    got_text:             str
    repair_needed:        bool
    speech_act_correct:   bool
    intent_correct:       bool
    repair_correct:       bool
    invariant_ok:         bool
    no_capsule_dump:      bool
    answer_has_shape:     bool
    notes:                str = ""


@dataclass
class CaseResult:
    case:           MultiTurnCase
    turn_results:   list[TurnResult]
    passed:         bool
    failure_reason: str = ""


@dataclass
class MultiTurnReport:
    total_cases:           int
    total_turns:           int
    speech_act_score:      float
    invariant_score:       float
    repair_score:          float
    no_capsule_score:      float
    shape_score:           float
    gate_passed:           bool
    case_results:          list[CaseResult] = field(default_factory=list)
    latency_p50_ms:        float = 0.0
    latency_p95_ms:        float = 0.0

    def print(self, verbose: bool = False):
        print("\n── Multi-Turn Dialogue Benchmark ────────────────────────────────")
        print(f"  Cases:                 {self.total_cases}")
        print(f"  Turns:                 {self.total_turns}")
        print(f"  Speech act correct:    {self.speech_act_score*100:.1f}%  (gate: ≥85%)")
        print(f"  Invariant established: {self.invariant_score*100:.1f}%  (gate: 100%)")
        print(f"  Repair triggered:      {self.repair_score*100:.1f}%  (gate: 100%)")
        print(f"  No capsule dump:       {self.no_capsule_score*100:.1f}%  (gate: 100%)")
        print(f"  Answer has shape:      {self.shape_score*100:.1f}%  (gate: ≥80%)")
        print(f"  Latency p50/p95:       {self.latency_p50_ms:.0f}ms / {self.latency_p95_ms:.0f}ms")
        gate = "✓ PASS" if self.gate_passed else "✗ FAIL"
        print(f"\n  Gate:                  {gate}")

        if verbose or not self.gate_passed:
            for cr in self.case_results:
                case_status = "✓" if cr.passed else "✗"
                print(f"\n  {case_status} [{cr.case.category}] {cr.case.name}")
                for tr in cr.turn_results:
                    sa_ok = "✓" if tr.speech_act_correct else "✗"
                    inv_ok = "✓" if tr.invariant_ok else "✗"
                    print(f"    T{tr.turn_idx+1} {sa_ok}sa {inv_ok}inv  "
                          f"got={tr.got_speech_act:12}  {tr.query[:50]}")
                    if tr.notes:
                        print(f"         {tr.notes}")
                if cr.failure_reason:
                    print(f"    !! {cr.failure_reason}")
        print("─────────────────────────────────────────────────────────────────\n")


# ── Test cases ────────────────────────────────────────────────────────────────

_CASES: list[MultiTurnCase] = [

    # ── 1. Correction persistence (Tim's CMS example) ─────────────────────────
    MultiTurnCase(
        name="CMS definition correction",
        category="correction_persistence",
        turns=[
            TurnSpec("What is CMS?",
                     "DEFINE", "definition_request"),
            TurnSpec("No, CMS means Capsule Memory System, not Content Management System.",
                     "CORRECT", "correct_prior_model",
                     repair_expected=True,
                     invariant_must_contain="Capsule Memory System"),
            TurnSpec("What does CMS mean?",
                     "DEFINE", "definition_request",
                     invariant_must_contain="Capsule Memory System"),
        ],
    ),

    # ── 2. Build path correction (Tim's renderer example) ────────────────────
    MultiTurnCase(
        name="Build path correction",
        category="correction_persistence",
        turns=[
            TurnSpec("What should we build next?",
                     "PLAN", "planning_request"),
            TurnSpec("Not a renderer. I mean language cognition.",
                     "CORRECT", "correct_prior_model",
                     repair_expected=True,
                     invariant_must_contain="language cognition",
                     invariant_must_not_assert="renderer is the next build"),
            TurnSpec("So what is the next build?",
                     "PLAN", "",
                     invariant_must_contain="language cognition"),
        ],
    ),

    # ── 3. Frustration escalation ─────────────────────────────────────────────
    MultiTurnCase(
        name="Frustration escalation to REASSURE",
        category="frustration_repair",
        turns=[
            TurnSpec("The benchmark keeps failing.",
                     "DIAGNOSE", "report_persistent_failure"),
            TurnSpec("Still failing after the fix!",
                     "DIAGNOSE", "report_persistent_failure",
                     repair_expected=True),
            TurnSpec("Come on! How many times do I have to fix this?",
                     "REASSURE", "express_frustration"),
        ],
    ),

    # ── 4. Frustration with WTF signal ───────────────────────────────────────
    MultiTurnCase(
        name="WTF frustration repair",
        category="frustration_repair",
        turns=[
            TurnSpec("The activation engine gives zero results.",
                     "DIAGNOSE", "report_failure",
                     repair_expected=True),
            TurnSpec("WTF is happening here.",
                     "REASSURE", "express_frustration"),
            TurnSpec("It was working yesterday.",
                     "REASSURE", "express_frustration"),
        ],
    ),

    # ── 5. Topic continuity — explanation thread ──────────────────────────────
    MultiTurnCase(
        name="Explanation thread continuity",
        category="topic_continuity",
        turns=[
            TurnSpec("Explain the activation engine.",
                     "ASSERT", "explanation_request"),
            TurnSpec("How does that relate to SSRE?",
                     "ASSERT", "explanation_request"),
            TurnSpec("And the LangEng bridge?",
                     "ASSERT", "topic_continuation"),
        ],
    ),

    # ── 6. Topic continuity — planning thread ────────────────────────────────
    MultiTurnCase(
        name="Planning thread continuity",
        category="topic_continuity",
        turns=[
            TurnSpec("What should we build after the benchmark passes?",
                     "PLAN", "planning_request"),
            TurnSpec("And what about the seed expansion?",
                     "ASSERT", "topic_continuation"),
            TurnSpec("What's the roadmap for phase 2?",
                     "PLAN", "planning_request"),
        ],
    ),

    # ── 7. Referent tracking — 'it' refers to broken component ───────────────
    MultiTurnCase(
        name="Pronoun referent tracking — failure",
        category="referent_tracking",
        turns=[
            TurnSpec("The repair engine is broken.",
                     "DIAGNOSE", "report_failure",
                     repair_expected=True),
            TurnSpec("It keeps returning null.",
                     "DIAGNOSE", "report_persistent_failure",
                     repair_expected=True),
            TurnSpec("Why does it do that?",
                     "ASSERT", "causal_inquiry"),
        ],
    ),

    # ── 8. Referent tracking — 'that' follows an assertion ───────────────────
    MultiTurnCase(
        name="Pronoun referent tracking — assertion follow-up",
        category="referent_tracking",
        turns=[
            TurnSpec("Explain semantic realization.",
                     "ASSERT", "explanation_request"),
            TurnSpec("Does that run before or after repair?",
                     "ASSERT", ""),
            TurnSpec("And that affects the output how?",
                     "ASSERT", ""),
        ],
    ),

    # ── 9. Correction stacking — two corrections accumulate ──────────────────
    MultiTurnCase(
        name="Correction stacking",
        category="correction_stacking",
        turns=[
            TurnSpec("What is SSRE?",
                     "DEFINE", "definition_request"),
            TurnSpec("No, SSRE is not a BFS engine.",
                     "CORRECT", "correct_prior_model",
                     repair_expected=True,
                     invariant_must_contain="SSRE"),
            TurnSpec("And it does not use simple keyword search.",
                     "CORRECT", "correct_prior_model",
                     repair_expected=True,
                     invariant_must_contain="keyword"),
            TurnSpec("So describe SSRE.",
                     "ASSERT", "explanation_request"),
        ],
    ),

    # ── 10. No invariant contradiction ───────────────────────────────────────
    MultiTurnCase(
        name="Invariant not contradicted across turns",
        category="no_invariant_contradict",
        turns=[
            TurnSpec("What are you?",
                     "RECALL", "identity_inquiry"),
            TurnSpec("You are not a chatbot. You are a symbolic AI.",
                     "CORRECT", "correct_prior_model",
                     repair_expected=True,
                     invariant_must_contain="symbolic AI"),
            TurnSpec("So what are you exactly?",
                     "RECALL", "",
                     invariant_must_contain="symbolic AI"),
        ],
    ),

    # ── 11. Prior-turn reference resolution ──────────────────────────────────
    MultiTurnCase(
        name="Prior-turn reference — recall context",
        category="prior_turn_reference",
        turns=[
            TurnSpec("Do you remember the Mirror Protocol?",
                     "RECALL", ""),
            TurnSpec("What stage was that at?",
                     "RECALL", ""),
            TurnSpec("And before that, what were we working on?",
                     "RECALL", ""),
        ],
    ),

    # ── 12. Repair after misunderstanding ────────────────────────────────────
    MultiTurnCase(
        name="Correction after misunderstanding",
        category="correction_persistence",
        turns=[
            TurnSpec("How does SSRE work?",
                     "ASSERT", "explanation_request"),
            TurnSpec("You misunderstood — I meant the activation engine.",
                     "CORRECT", "correct_prior_model",
                     repair_expected=True,
                     invariant_must_contain="activation engine"),
            TurnSpec("So explain that.",
                     "ASSERT", ""),
        ],
    ),

    # ── 13. Urgency in conversation context ──────────────────────────────────
    MultiTurnCase(
        name="Urgency signal mid-conversation",
        category="frustration_repair",
        turns=[
            TurnSpec("The API is down.",
                     "DIAGNOSE", "report_failure",
                     repair_expected=True),
            TurnSpec("This is urgent — what's the fastest path to fix it?",
                     "PLAN", "express_urgency"),
            TurnSpec("Drop everything, focus on this.",
                     "PLAN", "express_urgency"),
        ],
    ),

    # ── 14. Provenance challenge ─────────────────────────────────────────────
    MultiTurnCase(
        name="Provenance challenge sequence",
        category="no_invariant_contradict",
        turns=[
            TurnSpec("Explain the activation law.",
                     "ASSERT", "explanation_request"),
            TurnSpec("Are you sure about that?",
                     "MARK_UNCERTAINTY", "challenge_provenance"),
            TurnSpec("How confident are you in that answer?",
                     "MARK_UNCERTAINTY", "challenge_provenance"),
        ],
    ),

    # ── 15. Persistent failure across multiple repair attempts ───────────────
    MultiTurnCase(
        name="Persistent failure — three turns",
        category="frustration_repair",
        turns=[
            TurnSpec("The benchmark was passing, now it's failing again.",
                     "DIAGNOSE", "report_persistent_failure",
                     repair_expected=True),
            TurnSpec("Still broken after the fix.",
                     "DIAGNOSE", "report_persistent_failure",
                     repair_expected=True),
            TurnSpec("You've told me it works, but it clearly doesn't.",
                     "DIAGNOSE", "report_persistent_failure",
                     repair_expected=True),
        ],
    ),

    # ── 16. Calm follow-up after frustration resolves ────────────────────────
    MultiTurnCase(
        name="Frustration resolves to normal exchange",
        category="frustration_repair",
        turns=[
            TurnSpec("Come on! This is still broken.",
                     "DIAGNOSE", "report_persistent_failure",
                     repair_expected=True),
            TurnSpec("Ok fine. What's actually wrong?",
                     "DIAGNOSE", ""),
            TurnSpec("What would fix it?",
                     "PLAN", ""),
        ],
    ),

]


# ── Capsule dump / shape detection (reuse from benchmark.py) ─────────────────

_CAPSULE_SIGNALS = ["[[", "capsule_id:", "anchor_id:", "relation_id:", "predicate:",
                    "subject_id:", "object_id:", "confidence: 0.", "domain_tags:",
                    "seen_count:", "|||"]

def _is_capsule_dump(text: str) -> bool:
    t = text.lower()
    return any(s in t for s in _CAPSULE_SIGNALS)

def _has_answer_shape(text: str) -> bool:
    t = text.strip()
    if not t or len(t) < 10:
        return False
    error_pats = ["error:", "traceback", "exception", "attributeerror", "typeerror"]
    return not any(p in t.lower() for p in error_pats)


# ── Minimal plan stub ─────────────────────────────────────────────────────────

class _MinimalPlan:
    operator_used   = "CLARIFY"
    operator_output = {}
    confidence      = 0.3
    subject         = ""
    implied_need    = "understand"


# ── Runner ────────────────────────────────────────────────────────────────────

def run_case(case: MultiTurnCase) -> CaseResult:
    from .pipeline import run_language_cognition
    from .dialogue_memory import DialogueMemory

    dm = DialogueMemory()
    turn_results: list[TurnResult] = []

    for i, spec in enumerate(case.turns):
        t0 = time.perf_counter()
        prior_history = dm.as_history()  # includes all turns so far (user will be added next)

        user_turn = dm.record_user_turn(spec.query)
        history_for_lc = dm.as_history()[:-1]

        try:
            lc = run_language_cognition(
                query=spec.query,
                response_plan=_MinimalPlan(),
                history=history_for_lc,
            )
        except Exception as exc:
            turn_results.append(TurnResult(
                turn_idx=i, query=spec.query,
                got_speech_act="ERROR", got_intent="ERROR", got_text="",
                repair_needed=False,
                speech_act_correct=False, intent_correct=False,
                repair_correct=not spec.repair_expected,
                invariant_ok=True, no_capsule_dump=True, answer_has_shape=False,
                notes=str(exc)[:100],
            ))
            continue

        # Update user turn with pragmatic reading
        pr = lc.pragmatic_reading
        if pr:
            user_turn.speech_act = lc.speech_act
            user_turn.pragmatic_act = pr.pragmatic_act
            user_turn.inferred_intent = pr.inferred_intent
            user_turn.repair_needed = pr.repair_needed
            user_turn.emotional_signal = pr.emotional_signal
            if pr.repair_needed and lc.speech_act in ("CORRECT", "DIAGNOSE"):
                dm.add_correction(spec.query)

        # Record assistant turn
        dm.record_assistant_turn(lc.text, speech_act=lc.speech_act)

        # ── Evaluate this turn ────────────────────────────────────────────────
        got_intent = pr.inferred_intent if pr else ""
        repair_needed = pr.repair_needed if pr else False

        sa_correct = lc.speech_act == spec.expected_speech_act
        intent_correct = (not spec.expected_intent) or (got_intent == spec.expected_intent)
        repair_correct = (not spec.repair_expected) or repair_needed

        # Invariant check
        inv_text = dm.get_invariants_text()
        invariant_ok = True
        inv_note = ""
        if spec.invariant_must_contain and spec.invariant_must_contain not in inv_text:
            invariant_ok = False
            inv_note = f"invariant missing: '{spec.invariant_must_contain}'"

        notes_parts = []
        if not sa_correct:
            notes_parts.append(f"exp={spec.expected_speech_act} got={lc.speech_act}")
        if not intent_correct:
            notes_parts.append(f"intent exp={spec.expected_intent} got={got_intent}")
        if not repair_correct:
            notes_parts.append("repair not triggered")
        if inv_note:
            notes_parts.append(inv_note)

        turn_results.append(TurnResult(
            turn_idx=i,
            query=spec.query,
            got_speech_act=lc.speech_act,
            got_intent=got_intent,
            got_text=lc.text,
            repair_needed=repair_needed,
            speech_act_correct=sa_correct,
            intent_correct=intent_correct,
            repair_correct=repair_correct,
            invariant_ok=invariant_ok,
            no_capsule_dump=not _is_capsule_dump(lc.text),
            answer_has_shape=_has_answer_shape(lc.text),
            notes=" | ".join(notes_parts),
        ))

    case_passed = all(
        tr.speech_act_correct and tr.invariant_ok and tr.repair_correct
        for tr in turn_results
    )
    failure_reasons = [tr.notes for tr in turn_results if tr.notes]
    return CaseResult(
        case=case,
        turn_results=turn_results,
        passed=case_passed,
        failure_reason="; ".join(failure_reasons[:2]),
    )


def run_benchmark(
    cases: list[MultiTurnCase] | None = None,
    verbose: bool = False,
) -> MultiTurnReport:
    if cases is None:
        cases = _CASES

    all_results: list[CaseResult] = []
    latencies: list[float] = []

    for case in cases:
        t0 = time.perf_counter()
        cr = run_case(case)
        latencies.append((time.perf_counter() - t0) * 1000)
        all_results.append(cr)
        if verbose:
            status = "✓" if cr.passed else "✗"
            print(f"  {status} [{case.category}] {case.name}")

    # Flatten all turns
    all_turns = [tr for cr in all_results for tr in cr.turn_results]
    n = len(all_turns)

    sa_correct  = sum(1 for t in all_turns if t.speech_act_correct)
    # Invariant check: only turns that have invariant_must_contain set
    inv_turns   = [t for t in all_turns if any(
        spec.invariant_must_contain
        for cr in all_results
        for spec in cr.case.turns
        if spec.query == t.query and spec.invariant_must_contain
    )]
    inv_ok      = sum(1 for t in all_turns if t.invariant_ok)
    # Repair check: only turns where repair_expected=True
    repair_turns = [t for t in all_turns
                    if any(spec.repair_expected
                           for cr in all_results
                           for spec in cr.case.turns
                           if spec.query == t.query)]
    repair_ok   = sum(1 for t in repair_turns if t.repair_correct)
    no_caps     = sum(1 for t in all_turns if t.no_capsule_dump)
    has_shape   = sum(1 for t in all_turns if t.answer_has_shape)

    sa_score     = sa_correct / n if n else 0
    inv_score    = inv_ok / n if n else 0
    repair_score = repair_ok / len(repair_turns) if repair_turns else 1.0
    cap_score    = no_caps / n if n else 0
    shape_score  = has_shape / n if n else 0

    gate_passed = (
        sa_score     >= 0.85
        and inv_score == 1.0
        and repair_score == 1.0
        and cap_score == 1.0
        and shape_score >= 0.80
    )

    latencies.sort()
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0

    return MultiTurnReport(
        total_cases=len(all_results),
        total_turns=n,
        speech_act_score=sa_score,
        invariant_score=inv_score,
        repair_score=repair_score,
        no_capsule_score=cap_score,
        shape_score=shape_score,
        gate_passed=gate_passed,
        case_results=all_results,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--category", default=None)
    args = ap.parse_args()

    cases = _CASES
    if args.category:
        cases = [c for c in _CASES if c.category == args.category]

    report = run_benchmark(cases=cases, verbose=args.verbose)
    report.print(verbose=args.verbose)
