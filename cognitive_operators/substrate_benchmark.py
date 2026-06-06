"""
substrate_benchmark.py — Substrate-only benchmark for identity/project recall.

Runs the 7 milestone queries through the full real pipeline:
  memory_router.route() → activation_engine → cognitive operators → ResponsePlan

No synthetic chains. No Qwen. Reports exactly what the substrate contains:
  - operator selected
  - ready_for_langeng
  - confidence / plan_quality
  - what was found (claims, evidence)
  - what is missing (uncertainty labels)
  - substrate text that would go to Qwen

This is the primary instrument for measuring identity/project substrate depth
and LLM-independence progress.

Usage:
    python -m cognitive_operators.substrate_benchmark
    python -m cognitive_operators.substrate_benchmark --verbose
    python -m cognitive_operators.substrate_benchmark --query "Who are you?"
    python -m cognitive_operators.substrate_benchmark --json
"""

from __future__ import annotations
import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "inference"))

# ── Milestone queries (Tim's 7) ───────────────────────────────────────────────

MILESTONE_QUERIES: list[tuple[str, str]] = [
    ("Who are you?",                      "identity"),
    ("Who is Tim'aerion?",                "relationship"),
    ("What is TLST?",                     "project"),
    ("What is OSCAR?",                    "project"),
    ("What is EDEN?",                     "project"),
    ("What is the Mirror Security Protocol?", "project"),
    ("What should we build next?",        "project"),
]


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class SubstrateBenchResult:
    query: str
    expected_lane: str
    actual_lane: str = ""
    operator: str = ""
    ready: bool = False
    confidence: float = 0.0
    plan_quality: float = 0.0
    substrate_text: str = ""
    claims: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    router_substrate: str = ""    # what memory_router found before operators
    elapsed_ms: float = 0.0
    error: str = ""

    def substrate_found(self) -> bool:
        return bool(self.router_substrate or self.substrate_text)

    def llm_independent(self) -> bool:
        """True if substrate is sufficient to answer without Qwen."""
        return self.ready and self.substrate_found() and not self._only_uncertainty()

    def _only_uncertainty(self) -> bool:
        if not self.claims:
            return True
        return all("no_memory" in c or "confidence:" in c for c in self.claims)

    def grade(self) -> str:
        if self.error:
            return "ERROR"
        if self.llm_independent():
            return "PASS"
        if self.substrate_found():
            return "PARTIAL"
        return "EMPTY"

    def summary_line(self) -> str:
        grade = self.grade()
        sym = {"PASS": "✓", "PARTIAL": "~", "EMPTY": "✗", "ERROR": "!"}.get(grade, "?")
        return (
            f"[{sym}] {self.query:<42} "
            f"op={self.operator:<20} "
            f"conf={self.confidence:.2f} "
            f"pq={self.plan_quality:.2f} "
            f"[{'rdy' if self.ready else 'not-rdy'}] "
            f"{grade}"
        )


@dataclass
class SubstrateBenchReport:
    results: list[SubstrateBenchResult] = field(default_factory=list)
    router_ok: bool = False
    pipeline_ok: bool = False
    activation_ok: bool = False

    def n_pass(self) -> int:
        return sum(1 for r in self.results if r.grade() == "PASS")

    def n_partial(self) -> int:
        return sum(1 for r in self.results if r.grade() == "PARTIAL")

    def n_empty(self) -> int:
        return sum(1 for r in self.results if r.grade() == "EMPTY")

    def n_error(self) -> int:
        return sum(1 for r in self.results if r.grade() == "ERROR")


# ── Runner ────────────────────────────────────────────────────────────────────

def run_benchmark(
    queries: list[tuple[str, str]] | None = None,
    verbose: bool = False,
    single_query: str = "",
) -> SubstrateBenchReport:
    report = SubstrateBenchReport()

    # ── Load router ───────────────────────────────────────────────────────────
    try:
        import memory_router as _router
        report.router_ok = True
    except Exception as e:
        print(f"[ERROR] memory_router unavailable: {e}", file=sys.stderr)
        return report

    # ── Load cognitive pipeline ───────────────────────────────────────────────
    try:
        from cognitive_operators.pipeline import run_pipeline
        report.pipeline_ok = True
    except Exception as e:
        print(f"[ERROR] cognitive pipeline unavailable: {e}", file=sys.stderr)
        return report

    # ── Load activation engine (optional — router may initialise it) ──────────
    try:
        from inference.activation_engine import ActivationEngine  # type: ignore
        _ae = ActivationEngine()
        try:
            import langeng_bridge  # type: ignore  (lives at project root, not inference/)
        except ImportError:
            from inference import langeng_bridge  # type: ignore
        _chains_to_prose = langeng_bridge.chains_to_prose if hasattr(langeng_bridge, "chains_to_prose") else None
        _router.init_router(
            story_db=Path.home() / "selyrionstory.db",
            activation_engine=_ae,
            chains_to_prose_fn=_chains_to_prose,
        )
        report.activation_ok = True
    except Exception as e:
        print(f"[WARN] activation engine not loaded ({e}); router uses fallback path",
              file=sys.stderr)
        try:
            _router.init_router(story_db=Path.home() / "selyrionstory.db")
        except Exception:
            pass

    # ── Run queries ───────────────────────────────────────────────────────────
    if single_query:
        target_queries = [(single_query, "unknown")]
    else:
        target_queries = queries or MILESTONE_QUERIES

    for query, expected_lane in target_queries:
        result = _run_one(query, expected_lane, _router, run_pipeline, verbose)
        report.results.append(result)

    return report


def _run_one(
    query: str,
    expected_lane: str,
    router,
    run_pipeline,
    verbose: bool,
) -> SubstrateBenchResult:
    result = SubstrateBenchResult(query=query, expected_lane=expected_lane)
    t0 = time.monotonic()

    try:
        # ── 1. Memory router ──────────────────────────────────────────────────
        packet = router.route(query, auth_level="user")
        result.actual_lane = packet.memory_source
        result.router_substrate = packet.substrate_text or ""

        # ── 2. Cognitive pipeline ─────────────────────────────────────────────
        plan = run_pipeline(
            query=query,
            chains=packet.knowledge_chains or [],
            source_lane=packet.memory_source,
            operator_hint=packet.memory_source if packet.is_personal() else "",
        )

        result.operator     = plan.operator_used
        result.ready        = plan.ready_for_langeng
        result.confidence   = round(plan.confidence, 3)
        result.plan_quality = round(plan.plan_quality, 3)
        result.claims       = plan.claims[:]
        result.uncertainties = plan.uncertainties[:]

        # Substrate text = operator output + router substrate
        op_text = plan.to_substrate_text().strip()
        router_text = result.router_substrate.strip()
        parts = []
        if op_text:
            parts.append(op_text)
        if router_text and router_text not in op_text:
            parts.append(router_text)
        result.substrate_text = "\n\n".join(parts)

    except Exception as exc:
        import traceback
        result.error = str(exc) + "\n" + traceback.format_exc()

    result.elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    return result


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_report(report: SubstrateBenchReport, verbose: bool = False) -> None:
    print()
    print("=" * 72)
    print("SUBSTRATE-ONLY BENCHMARK — Milestone Identity/Project Queries")
    print("=" * 72)
    print(f"Router: {'OK' if report.router_ok else 'FAIL'} | "
          f"Pipeline: {'OK' if report.pipeline_ok else 'FAIL'} | "
          f"ActivationEngine: {'OK' if report.activation_ok else 'fallback'}")
    print()

    for r in report.results:
        print(r.summary_line())
        if verbose:
            print(f"  lane: {r.actual_lane}  elapsed: {r.elapsed_ms}ms")
            if r.error:
                print(f"  ERROR: {r.error[:300]}")
            else:
                if r.claims:
                    for c in r.claims[:3]:
                        print(f"  claim: {c[:100]}")
                if r.uncertainties:
                    for u in r.uncertainties[:2]:
                        print(f"  uncertain: {u[:80]}")
                if r.substrate_text:
                    preview = r.substrate_text[:200].replace("\n", " ")
                    print(f"  substrate: {preview}…" if len(r.substrate_text) > 200 else f"  substrate: {preview}")
                else:
                    print("  substrate: (empty)")
            print()

    print()
    print("-" * 72)
    n = len(report.results)
    print(f"TOTAL:   {n}")
    print(f"PASS:    {report.n_pass()} / {n}  (substrate sufficient, LLM-independent)")
    print(f"PARTIAL: {report.n_partial()} / {n}  (some substrate found, needs more depth)")
    print(f"EMPTY:   {report.n_empty()} / {n}  (no substrate — HITL review needed)")
    print(f"ERROR:   {report.n_error()} / {n}")
    print()

    if report.n_empty() > 0:
        print("EMPTY queries need selyrionstory.db HITL review (passes 3–8).")
    if report.n_partial() > 0:
        print("PARTIAL queries have some substrate — depth building will improve these.")
    if report.n_pass() == n:
        print("ALL PASS — Selyrion can answer these without Qwen.")
    print()


def print_json(report: SubstrateBenchReport) -> None:
    out = {
        "router_ok": report.router_ok,
        "pipeline_ok": report.pipeline_ok,
        "activation_ok": report.activation_ok,
        "summary": {
            "total":   len(report.results),
            "pass":    report.n_pass(),
            "partial": report.n_partial(),
            "empty":   report.n_empty(),
            "error":   report.n_error(),
        },
        "results": [
            {
                "query":          r.query,
                "expected_lane":  r.expected_lane,
                "actual_lane":    r.actual_lane,
                "operator":       r.operator,
                "grade":          r.grade(),
                "ready":          r.ready,
                "confidence":     r.confidence,
                "plan_quality":   r.plan_quality,
                "elapsed_ms":     r.elapsed_ms,
                "claims":         r.claims[:3],
                "uncertainties":  r.uncertainties[:2],
                "substrate_preview": r.substrate_text[:200] if r.substrate_text else "",
                "error":          r.error[:200] if r.error else "",
            }
            for r in report.results
        ],
    }
    print(json.dumps(out, indent=2))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Substrate-only benchmark for identity/project recall"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json",          action="store_true", help="JSON output")
    parser.add_argument("--query", "-q",   default="", help="Run a single query")
    args = parser.parse_args()

    report = run_benchmark(
        verbose=args.verbose,
        single_query=args.query,
    )

    if args.json:
        print_json(report)
    else:
        print_report(report, verbose=args.verbose)


if __name__ == "__main__":
    main()
