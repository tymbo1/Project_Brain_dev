"""
benchmark.py — Language Cognition Layer benchmark.

Measures zero-LLM conversational quality against ground truth.

Metrics:
  speech_act_correct  — % of queries where selected speech act matches expected
  intent_correct      — % where inferred pragmatic intent matches expected
  no_capsule_dump     — % where output contains no raw capsule text
  no_invented_facts   — % where output contains no fabricated provenance
  answer_has_shape    — % where output is a recognizable conversational form (not empty)
  repair_triggered    — % of repair cases where repair was triggered correctly

Gate:
  speech_act_correct ≥ 85%
  intent_correct ≥ 85%
  no_capsule_dump = 100%
  answer_has_shape ≥ 80%

Usage:
  python -m language_cognition.benchmark
  python -m language_cognition.benchmark --verbose
  python -m language_cognition.benchmark --domain technical
"""

from __future__ import annotations
import argparse
import time
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

_LC_DB_PATH = Path.home() / "language_cognition.db"

# ── Capsule dump detection ────────────────────────────────────────────────────

_CAPSULE_SIGNALS = [
    "[[",
    "capsule_id:",
    "anchor_id:",
    "relation_id:",
    "predicate:",
    "subject_id:",
    "object_id:",
    "confidence: 0.",
    "domain_tags:",
    "seen_count:",
    # Long runs of pipe-separated content
    "|||",
]

_HALLUCINATION_SIGNALS = [
    "according to my database",
    "as documented in",
    "per the records",
    "i can confirm that",
    "i have verified",
    "source: verified",
]


def _is_capsule_dump(text: str) -> bool:
    t = text.lower()
    return any(s in t for s in _CAPSULE_SIGNALS)


def _has_invented_provenance(text: str) -> bool:
    t = text.lower()
    return any(s in t for s in _HALLUCINATION_SIGNALS)


def _has_answer_shape(text: str) -> bool:
    """Check that the response is a recognizable conversational form."""
    t = text.strip()
    if not t or len(t) < 10:
        return False
    # Not just an error message
    error_patterns = ["error:", "traceback", "exception", "attributeerror", "typeerror"]
    if any(p in t.lower() for p in error_patterns):
        return False
    return True


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class BenchmarkCase:
    query:              str
    expected_speech_act: str
    expected_intent:    str = ""
    domain:             str = "general"
    difficulty:         str = "medium"


@dataclass
class CaseResult:
    case:               BenchmarkCase
    got_speech_act:     str
    got_intent:         str
    got_text:           str
    latency_ms:         float
    speech_act_correct: bool = False
    intent_correct:     bool = False
    no_capsule_dump:    bool = True
    no_invented_facts:  bool = True
    answer_has_shape:   bool = False
    notes:              str = ""


@dataclass
class BenchmarkReport:
    total:              int
    speech_act_score:   float
    intent_score:       float
    no_capsule_score:   float
    no_invented_score:  float
    shape_score:        float
    gate_passed:        bool
    failures:           list[CaseResult] = field(default_factory=list)
    latency_p50_ms:     float = 0.0
    latency_p95_ms:     float = 0.0

    def print(self, verbose: bool = False):
        print("\n── Language Cognition Benchmark ────────────────────────────────")
        print(f"  Total cases:         {self.total}")
        print(f"  Speech act correct:  {self.speech_act_score*100:.1f}%  (gate: ≥85%)")
        print(f"  Intent correct:      {self.intent_score*100:.1f}%  (gate: ≥85%)")
        print(f"  No capsule dump:     {self.no_capsule_score*100:.1f}%  (gate: 100%)")
        print(f"  No invented facts:   {self.no_invented_score*100:.1f}%")
        print(f"  Answer has shape:    {self.shape_score*100:.1f}%  (gate: ≥80%)")
        print(f"  Latency p50/p95:     {self.latency_p50_ms:.0f}ms / {self.latency_p95_ms:.0f}ms")
        gate = "✓ PASS" if self.gate_passed else "✗ FAIL"
        print(f"\n  Gate:                {gate}")

        if verbose or not self.gate_passed:
            for r in self.failures[:20]:
                print(f"\n  FAIL [{r.case.difficulty}] {r.case.query[:60]}")
                print(f"    expected act={r.case.expected_speech_act}  got={r.got_speech_act}")
                if r.case.expected_intent:
                    print(f"    expected intent={r.case.expected_intent}  got={r.got_intent}")
                if not r.no_capsule_dump:
                    print(f"    !! CAPSULE DUMP detected")
                if not r.answer_has_shape:
                    print(f"    !! No answer shape — output: {r.got_text[:80]!r}")
                if r.notes:
                    print(f"    note: {r.notes}")
        print("────────────────────────────────────────────────────────────────\n")


# ── Runner ────────────────────────────────────────────────────────────────────

def run_benchmark(
    cases: list[BenchmarkCase] | None = None,
    domain: str | None = None,
    verbose: bool = False,
    save_results: bool = True,
    mock_response_plan=None,
) -> BenchmarkReport:
    """
    Run Language Cognition benchmark against a set of cases.

    If cases is None, loads from language_cognition.db lc_benchmark table.
    mock_response_plan: if provided, used for all queries (for testing without full stack).
    """
    from .pipeline import run_language_cognition
    from .pragmatics import interpret as pragmatic_interpret
    from .discourse_state import infer_discourse_state

    if cases is None:
        cases = _load_cases_from_db(domain)

    if not cases:
        print("No benchmark cases found. Run seed_generator first.")
        return BenchmarkReport(0, 0, 0, 0, 0, 0, False)

    results: list[CaseResult] = []

    for case in cases:
        t0 = time.perf_counter()
        try:
            result = _run_case(case, mock_response_plan)
        except Exception as e:
            result = CaseResult(
                case=case,
                got_speech_act="ERROR",
                got_intent="ERROR",
                got_text="",
                latency_ms=0.0,
                answer_has_shape=False,
                notes=str(e)[:120],
            )
        result.latency_ms = (time.perf_counter() - t0) * 1000
        results.append(result)
        if verbose:
            status = "✓" if result.speech_act_correct else "✗"
            print(f"  {status} [{result.got_speech_act}] {case.query[:55]}")

    # Score
    n = len(results)
    sa_correct  = sum(1 for r in results if r.speech_act_correct)
    int_correct = sum(1 for r in results if r.intent_correct)
    no_caps     = sum(1 for r in results if r.no_capsule_dump)
    no_inv      = sum(1 for r in results if r.no_invented_facts)
    has_shape   = sum(1 for r in results if r.answer_has_shape)

    latencies = sorted(r.latency_ms for r in results)
    p50 = latencies[n // 2] if latencies else 0
    p95 = latencies[int(n * 0.95)] if latencies else 0

    sa_score = sa_correct / n
    int_score = int_correct / n
    cap_score = no_caps / n
    inv_score = no_inv / n
    shape_score = has_shape / n

    gate_passed = (sa_score >= 0.85 and int_score >= 0.85
                   and cap_score == 1.0 and shape_score >= 0.80)

    failures = [r for r in results if not r.speech_act_correct or not r.no_capsule_dump or not r.answer_has_shape]

    report = BenchmarkReport(
        total=n,
        speech_act_score=sa_score,
        intent_score=int_score,
        no_capsule_score=cap_score,
        no_invented_score=inv_score,
        shape_score=shape_score,
        gate_passed=gate_passed,
        failures=failures,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
    )

    if save_results:
        _save_results(results, report)

    return report


def _run_case(case: BenchmarkCase, mock_plan) -> CaseResult:
    from .pipeline import run_language_cognition
    from .pragmatics import interpret as pragmatic_interpret
    from .discourse_state import infer_discourse_state

    # Build a minimal response plan if none provided
    plan = mock_plan or _minimal_plan(case.query)

    lc_result = run_language_cognition(query=case.query, response_plan=plan)

    got_act    = lc_result.speech_act
    got_intent = ""
    if lc_result.pragmatic_reading:
        got_intent = lc_result.pragmatic_reading.inferred_intent

    text = lc_result.text

    sa_correct  = got_act == case.expected_speech_act
    int_correct = (not case.expected_intent) or (got_intent == case.expected_intent)

    return CaseResult(
        case=case,
        got_speech_act=got_act,
        got_intent=got_intent,
        got_text=text,
        latency_ms=0.0,
        speech_act_correct=sa_correct,
        intent_correct=int_correct,
        no_capsule_dump=not _is_capsule_dump(text),
        no_invented_facts=not _has_invented_provenance(text),
        answer_has_shape=_has_answer_shape(text),
    )


def _minimal_plan(query: str):
    """Minimal ResponsePlan stub for benchmark when no full stack is available."""
    class MinimalPlan:
        operator_used   = "CLARIFY"
        operator_output = {}
        confidence      = 0.3
        subject         = ""
        implied_need    = "understand"
    return MinimalPlan()


def _load_cases_from_db(domain: str | None = None) -> list[BenchmarkCase]:
    from .lc_db import get_db
    try:
        conn = get_db()
        q = "SELECT query,expected_speech_act,expected_intent,domain,difficulty FROM lc_benchmark"
        params = ()
        if domain:
            q += " WHERE domain=?"
            params = (domain,)
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [BenchmarkCase(r[0], r[1], r[2] or "", r[3] or "general", r[4] or "medium")
                for r in rows]
    except Exception:
        return []


def _save_results(results: list[CaseResult], report: BenchmarkReport) -> None:
    try:
        from .lc_db import get_db
        conn = get_db()
        run_at = time.time()
        for r in results:
            # Look up benchmark id
            row = conn.execute(
                "SELECT id FROM lc_benchmark WHERE query=?", (r.case.query,)
            ).fetchone()
            if not row:
                continue
            bid = row[0]
            rid = "lcbr." + hashlib.md5((bid + str(run_at)).encode()).hexdigest()[:8]
            conn.execute("""
                INSERT OR REPLACE INTO lc_benchmark_results
                    (id,benchmark_id,run_at,speech_act_got,intent_got,no_capsule_pass,naturalness,notes)
                VALUES (?,?,?,?,?,?,?,?)
            """, (rid, bid, run_at, r.got_speech_act, r.got_intent,
                  int(r.no_capsule_dump), r.answer_has_shape * 1.0, r.notes))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [benchmark] could not save results: {e}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--domain", default=None)
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    from .seed_generator import seed
    from .lc_db import _LC_DB_PATH
    if not _LC_DB_PATH.exists():
        print("Seeding language_cognition.db first...")
        seed(verbose=True)

    report = run_benchmark(
        domain=args.domain,
        verbose=args.verbose,
        save_results=not args.no_save,
    )
    report.print(verbose=args.verbose)
