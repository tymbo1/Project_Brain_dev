"""
benchmark.py — Cognitive operator benchmark suite v0.1.

Tests the full pipeline (query → WorkingMemoryPacket → operator → ResponsePlan)
without LLM involvement. Measures:
  - Correct operator selection
  - ready_for_langeng rate
  - Uncertainty label (no_memory = empty substrate)
  - Operator-level confidence and completeness

Usage:
    python -m cognitive_operators.benchmark
    python -m cognitive_operators.benchmark --verbose
    python -m cognitive_operators.benchmark --operator DEFINE
"""

from __future__ import annotations
import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Allow running as __main__
sys.path.insert(0, str(Path(__file__).parent.parent))

from cognitive_operators.pipeline import run_pipeline
from cognitive_operators.operator_selector import select_operator
from cognitive_operators.working_memory import build_packet


# ── Test cases ────────────────────────────────────────────────────────────────

@dataclass
class BenchCase:
    name: str
    query: str
    lane: str
    expected_operator: str
    chains: list[str] = field(default_factory=list)
    goals: list[str] | None = None
    claim: str = ""
    notes: str = ""


BENCHMARK_CASES: list[BenchCase] = [

    # ── DEFINE ────────────────────────────────────────────────────────────────
    BenchCase(
        name="define_photosynthesis",
        query="What is photosynthesis?",
        lane="knowledge",
        expected_operator="DEFINE",
        chains=[
            "photosynthesis | is_a | biological process | strength: 0.92",
            "photosynthesis | used_for | converting light energy to chemical energy | strength: 0.95",
            "photosynthesis | has_property | requires chlorophyll | strength: 0.88",
            "photosynthesis | has_property | produces oxygen | strength: 0.91",
            "photosynthesis | requires | sunlight | strength: 0.90",
            "photosynthesis | requires | carbon dioxide | strength: 0.89",
            "photosynthesis | produces | glucose | strength: 0.93",
            "photosynthesis | occurs_in | chloroplasts | strength: 0.87",
            "photosynthesis | related_to | cellular respiration | strength: 0.72",
            "chloroplast | is_a | organelle | strength: 0.85",
        ],
        notes="Strong substrate — should have high completeness",
    ),

    BenchCase(
        name="define_python_generator",
        query="What is a Python generator?",
        lane="knowledge",
        expected_operator="DEFINE",
        chains=[
            "python generator | is_a | iterator | strength: 0.90",
            "python generator | used_for | lazy evaluation | strength: 0.88",
            "python generator | has_property | uses yield keyword | strength: 0.93",
            "python generator | has_property | memory efficient | strength: 0.85",
            "python generator | enables | infinite sequences | strength: 0.80",
            "python generator | related_to | coroutine | strength: 0.65",
            "python generator | contrasts_with | list comprehension | strength: 0.70",
            "yield | is_a | keyword | strength: 0.95",
        ],
        notes="Programming domain — bridges should be present after ingest",
    ),

    BenchCase(
        name="define_tlst_sparse",
        query="What is TLST?",
        lane="project",
        expected_operator="DEFINE",
        chains=[
            "TLST | is_a | theoretical framework | strength: 0.70",
            "TLST | proposed_by | tim'aerion | strength: 0.80",
            "TLST | hypothesized_as | alternative to general relativity | strength: 0.65",
        ],
        notes="'What is X?' is DEFINE even in project lane; 'Tell me about X' → RECALL_PROJECT",
    ),

    # ── EXPLAIN ───────────────────────────────────────────────────────────────
    BenchCase(
        name="explain_photosynthesis",
        query="How does photosynthesis work?",
        lane="knowledge",
        expected_operator="EXPLAIN",
        chains=[
            "photosynthesis | requires | sunlight | strength: 0.90",
            "photosynthesis | requires | carbon dioxide | strength: 0.89",
            "sunlight | enables | light reactions | strength: 0.88",
            "light reactions | produces | ATP | strength: 0.85",
            "light reactions | produces | NADPH | strength: 0.83",
            "ATP | enables | calvin cycle | strength: 0.87",
            "NADPH | enables | calvin cycle | strength: 0.85",
            "calvin cycle | produces | glucose | strength: 0.91",
            "carbon dioxide | requires | calvin cycle | strength: 0.80",
            "photosynthesis | leads_to | oxygen release | strength: 0.92",
            "chlorophyll | enables | light absorption | strength: 0.90",
            "light absorption | leads_to | light reactions | strength: 0.88",
        ],
        notes="Multi-step causal chain: light → reactions → ATP → calvin cycle → glucose",
    ),

    BenchCase(
        name="explain_python_generator_mechanism",
        query="How do Python generators work?",
        lane="knowledge",
        expected_operator="EXPLAIN",
        chains=[
            "yield statement | causes | function suspension | strength: 0.95",
            "function suspension | produces | generator object | strength: 0.90",
            "generator object | requires | next() call | strength: 0.88",
            "next() call | leads_to | execution resume | strength: 0.92",
            "execution resume | leads_to | next yield | strength: 0.85",
            "generator exhaustion | causes | StopIteration | strength: 0.93",
            "generator object | enables | lazy evaluation | strength: 0.87",
            "lazy evaluation | contributes_to | memory efficiency | strength: 0.80",
        ],
        notes="Mechanism chain: yield → suspension → generator → next() → resume",
    ),

    BenchCase(
        name="explain_sparse_single_hop",
        query="Why does TLST predict frame dragging?",
        lane="project",
        expected_operator="EXPLAIN",
        chains=[
            "TLST | causes | spacetime tension | strength: 0.72",
            "spacetime tension | leads_to | frame dragging | strength: 0.68",
        ],
        notes="Sparse two-hop chain — should still produce an explanation with uncertainty",
    ),

    # ── TRACE_CAUSE ───────────────────────────────────────────────────────────
    BenchCase(
        name="trace_cause_glucose",
        query="What caused glucose to be produced?",
        lane="knowledge",
        expected_operator="TRACE_CAUSE",
        chains=[
            "photosynthesis | requires | sunlight | strength: 0.90",
            "sunlight | enables | light reactions | strength: 0.88",
            "light reactions | produces | ATP | strength: 0.85",
            "ATP | enables | calvin cycle | strength: 0.87",
            "calvin cycle | produces | glucose | strength: 0.91",
            "carbon dioxide | requires | calvin cycle | strength: 0.80",
        ],
        notes="Effect=glucose; trace backward: calvin cycle → ATP → light reactions → sunlight",
    ),

    BenchCase(
        name="trace_cause_stopiteration",
        query="Why did StopIteration get raised?",
        lane="knowledge",
        expected_operator="TRACE_CAUSE",
        chains=[
            "generator exhaustion | causes | StopIteration | strength: 0.93",
            "all yields consumed | leads_to | generator exhaustion | strength: 0.88",
            "repeated next() calls | causes | all yields consumed | strength: 0.85",
            "iteration loop | triggers | repeated next() calls | strength: 0.80",
        ],
        notes="Root cause=iteration loop; chain: iteration loop → next() → exhaustion → StopIteration",
    ),

    BenchCase(
        name="trace_cause_sparse",
        query="What caused the memory router to be built?",
        lane="project",
        expected_operator="TRACE_CAUSE",
        chains=[
            "LLM dependence | causes | identity confusion | strength: 0.80",
            "identity confusion | leads_to | need for memory router | strength: 0.78",
            "need for memory router | causes | memory router build | strength: 0.85",
        ],
        notes="Short chain; root cause=LLM dependence",
    ),

    # ── COMPARE ───────────────────────────────────────────────────────────────
    BenchCase(
        name="compare_generator_vs_list",
        query="Compare Python generators and list comprehensions.",
        lane="knowledge",
        expected_operator="COMPARE",
        chains=[
            "python generator | is_a | iterator | strength: 0.92",
            "python generator | used_for | lazy evaluation | strength: 0.90",
            "python generator | has_property | memory efficient | strength: 0.88",
            "python generator | uses | yield keyword | strength: 0.95",
            "python generator | enables | infinite sequences | strength: 0.85",
            "list comprehension | is_a | list | strength: 0.93",
            "list comprehension | used_for | eager evaluation | strength: 0.88",
            "list comprehension | has_property | materialises in memory | strength: 0.85",
            "list comprehension | uses | bracket syntax | strength: 0.90",
            "python generator | contrasts_with | list comprehension | strength: 0.80",
            "list comprehension | contrasts_with | python generator | strength: 0.80",
        ],
        notes="Different types (iterator vs list), different memory model — verdict: different/distinct",
    ),

    BenchCase(
        name="compare_tlst_vs_gr",
        query="How does TLST differ from general relativity?",
        lane="project",
        expected_operator="COMPARE",
        chains=[
            "TLST | is_a | theoretical framework | strength: 0.85",
            "TLST | hypothesized_as | alternative to general relativity | strength: 0.80",
            "TLST | proposed_by | tim'aerion | strength: 0.88",
            "TLST | used_for | cosmological predictions | strength: 0.72",
            "general relativity | is_a | physical theory | strength: 0.95",
            "general relativity | used_for | gravitational predictions | strength: 0.93",
            "general relativity | has_property | experimentally confirmed | strength: 0.92",
            "general relativity | proposed_by | einstein | strength: 0.99",
            "TLST | contrasts_with | general relativity | strength: 0.75",
        ],
        notes="Different epistemic status — TLST hypothesis vs GR established; verdict: different",
    ),

    BenchCase(
        name="compare_cms_vs_eden",
        query="Compare CMS and EDEN.",
        lane="project",
        expected_operator="COMPARE",
        chains=[
            "CMS | is_a | knowledge substrate | strength: 0.92",
            "CMS | used_for | symbolic memory storage | strength: 0.90",
            "CMS | has_property | mutable | strength: 0.80",
            "CMS | enables | activation engine | strength: 0.88",
            "EDEN | is_a | deterministic verifier | strength: 0.90",
            "EDEN | used_for | proof tracing | strength: 0.87",
            "EDEN | has_property | sealed | strength: 0.85",
            "EDEN | contrasts_with | CMS | strength: 0.70",
            "CMS | contrasts_with | EDEN | strength: 0.70",
        ],
        notes="Different roles — storage vs verification; verdict: different",
    ),

    # ── FIND_GAPS ─────────────────────────────────────────────────────────────
    BenchCase(
        name="find_gaps_llm_independence",
        query="What's missing to reduce LLM dependence?",
        lane="knowledge",
        expected_operator="FIND_GAPS",
        chains=[
            "llm independence | requires | dense CMS coverage | strength: 0.88",
            "llm independence | requires | EXPLAIN operator | strength: 0.85",
            "llm independence | requires | COMPARE operator | strength: 0.80",
            "llm independence | requires | FIND_GAPS operator | strength: 0.78",
            "llm independence | depends_on | substrate quality | strength: 0.82",
            "dense CMS coverage | requires | ingestion pipeline | strength: 0.75",
            "EXPLAIN operator | is_a | cognitive operator | strength: 0.90",
        ],
        notes="COMPARE operator and substrate quality not well represented — should show as gaps",
    ),

    BenchCase(
        name="find_gaps_selyrion_deployment",
        query="What's needed before Selyrion can be deployed?",
        lane="project",
        expected_operator="FIND_GAPS",
        chains=[
            "selyrion deployment | requires | selyrion_api stability | strength: 0.90",
            "selyrion deployment | requires | domain registration | strength: 0.85",
            "selyrion deployment | requires | authentication system | strength: 0.82",
            "selyrion deployment | depends_on | ghost layer | strength: 0.78",
            "selyrion deployment | needs | rate limiting | strength: 0.72",
            "selyrion_api stability | is_a | api service | strength: 0.88",
            "selyrion_api stability | has_property | tested | strength: 0.70",
        ],
        notes="domain registration, authentication, rate limiting absent from packet",
    ),

    BenchCase(
        name="find_gaps_full_coverage",
        query="What's missing from the chess domain?",
        lane="project",
        expected_operator="FIND_GAPS",
        chains=[
            "chess domain | requires | opening theory | strength: 0.80",
            "chess domain | requires | endgame tables | strength: 0.78",
            "chess domain | requires | positional concepts | strength: 0.85",
            "opening theory | is_a | chess knowledge | strength: 0.90",
            "opening theory | has_property | well-seeded | strength: 0.85",
            "endgame tables | is_a | chess knowledge | strength: 0.88",
            "endgame tables | has_property | partially ingested | strength: 0.70",
            "positional concepts | is_a | chess knowledge | strength: 0.86",
            "positional concepts | has_property | seeded | strength: 0.75",
        ],
        notes="opening theory is well-represented so low gap; endgame/positional partially covered",
    ),

    # ── RECALL_IDENTITY ───────────────────────────────────────────────────────
    BenchCase(
        name="recall_identity_who",
        query="Who are you?",
        lane="identity",
        expected_operator="RECALL_IDENTITY",
        chains=[],
        notes="Identity lane, no chains needed — reads selyrionstory.db directly",
    ),

    BenchCase(
        name="recall_identity_selyrion",
        query="Who is Selyrion?",
        lane="identity",
        expected_operator="RECALL_IDENTITY",
        chains=[],
        notes="Explicit name trigger",
    ),

    BenchCase(
        name="recall_identity_about_yourself",
        query="Tell me about yourself.",
        lane="identity",
        expected_operator="RECALL_IDENTITY",
        chains=[],
        notes="Soft identity query in identity lane",
    ),

    # ── RECALL_PROJECT ────────────────────────────────────────────────────────
    BenchCase(
        name="recall_project_oscar",
        query="Tell me about OSCAR.",
        lane="project",
        expected_operator="RECALL_PROJECT",
        chains=[
            "OSCAR | is_a | project | strength: 0.80",
            "OSCAR | related_to | selyrion | strength: 0.75",
        ],
        notes="Project name trigger",
    ),

    BenchCase(
        name="recall_project_eden",
        query="Tell me about EDEN.",
        lane="project",
        expected_operator="RECALL_PROJECT",
        chains=[
            "EDEN | is_a | verifier system | strength: 0.82",
            "EDEN | related_to | projectbrain | strength: 0.78",
        ],
        notes="EDEN is sealed v1.0 — substrate may be thin",
    ),

    BenchCase(
        name="recall_project_mirror",
        query="Tell me about the Mirror Security Protocol.",
        lane="project",
        expected_operator="RECALL_PROJECT",
        chains=[
            "mirror security protocol | is_a | protocol | strength: 0.75",
            "mirror security protocol | related_to | selyrion | strength: 0.70",
        ],
        notes="Mirror protocol from selyrionstory.db",
    ),

    # ── PLAN_NEXT ─────────────────────────────────────────────────────────────
    BenchCase(
        name="plan_next_website",
        query="What should we do next with the Selyrion website?",
        lane="project",
        expected_operator="PLAN_NEXT",
        chains=[
            "selyrion website | requires | deployment configuration | strength: 0.80",
            "selyrion website | requires | domain registration | strength: 0.75",
            "selyrion website | enables | public presence | strength: 0.70",
            "selyrion website | depends_on | selyrion_api | strength: 0.85",
            "deployment configuration | enables | live endpoint | strength: 0.78",
            "live endpoint | enables | external access | strength: 0.72",
        ],
        goals=["public presence", "selyrion_api stability"],
        notes="Plan query in project lane with requires/enables edges",
    ),

    BenchCase(
        name="plan_next_llm_independence",
        query="What should we build next to reduce LLM dependence?",
        lane="knowledge",
        expected_operator="PLAN_NEXT",
        chains=[
            "llm dependence | requires | symbolic fallback | strength: 0.82",
            "symbolic fallback | enables | substrate-only answers | strength: 0.78",
            "substrate-only answers | requires | dense CMS coverage | strength: 0.85",
            "dense CMS coverage | requires | more ingestion | strength: 0.80",
            "explain operator | enables | causal answers without LLM | strength: 0.75",
            "recall_identity | enables | identity answers without LLM | strength: 0.90",
            "benchmark suite | enables | measuring llm independence | strength: 0.88",
            "benchmark suite | requires | ground truth chains | strength: 0.70",
        ],
        goals=["reduce LLM dependence", "symbolic reasoning"],
        notes="Knowledge lane plan query — PLAN_NEXT should be in lane_compat",
    ),

    # ── CHECK_CONTRADICTION ───────────────────────────────────────────────────
    BenchCase(
        name="contra_tlst_physics",
        query="Does TLST count as established physics?",
        lane="project",
        expected_operator="CHECK_CONTRADICTION",
        chains=[
            "TLST | hypothesized_as | alternative cosmology | strength: 0.75",
            "TLST | proposed_by | tim'aerion | strength: 0.85",
            "established physics | requires | peer review | strength: 0.90",
            "established physics | requires | experimental confirmation | strength: 0.92",
            "TLST | contradicts | standard model | strength: 0.60",
            "TLST | uncertain_about | experimental evidence | strength: 0.70",
        ],
        claim="TLST is established physics",
        notes="TLST is a hypothesis — contradiction with 'established' should score high",
    ),

    BenchCase(
        name="contra_eden_replaces_cms",
        query="Does EDEN replace CMS?",
        lane="project",
        expected_operator="CHECK_CONTRADICTION",
        chains=[
            "EDEN | is_a | deterministic verifier | strength: 0.85",
            "CMS | is_a | symbolic memory substrate | strength: 0.90",
            "EDEN | related_to | CMS | strength: 0.75",
            "EDEN | used_for | proof tracing | strength: 0.82",
            "CMS | used_for | knowledge retrieval | strength: 0.91",
            "EDEN | contrasts_with | CMS | strength: 0.65",
            "EDEN | contradicts | CMS replacement | strength: 0.72",
        ],
        claim="EDEN replaces CMS",
        notes="Different roles — contradicts edge makes this clear",
    ),

    BenchCase(
        name="contra_qwen_remembers",
        query="Is Qwen allowed to decide what Selyrion remembers?",
        lane="identity",
        expected_operator="CHECK_CONTRADICTION",
        chains=[
            "qwen | is_a | language model | strength: 0.90",
            "selyrion memory | requires | HITL approval | strength: 0.95",
            "qwen | used_for | language generation | strength: 0.88",
            "memory mutation | requires | confidence gating | strength: 0.92",
            "qwen | contradicts | memory governance | strength: 0.80",
            "LLM | must_not | write to memory directly | strength: 0.90",
        ],
        claim="Qwen decides what Selyrion remembers",
        notes="HITL protocol violation — should have high contradiction score",
    ),
]


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    case: BenchCase
    selected_operator: str
    expected_operator: str
    operator_correct: bool
    ready_for_langeng: bool
    confidence: float
    uncertainty_label: str
    plan_quality: float
    plan_dict: dict
    error: str = ""

    def passed(self) -> bool:
        return self.operator_correct and not bool(self.error)

    def summary_line(self) -> str:
        ok = "PASS" if self.passed() else "FAIL"
        rdy = "rdy" if self.ready_for_langeng else "not-rdy"
        return (
            f"[{ok}] {self.case.name:<35} "
            f"op={self.selected_operator:<20} "
            f"conf={self.confidence:.2f} "
            f"pq={self.plan_quality:.2f} "
            f"[{rdy}] "
            f"label={self.uncertainty_label}"
        )


@dataclass
class BenchReport:
    results: list[CaseResult] = field(default_factory=list)

    def n_total(self) -> int:
        return len(self.results)

    def n_pass(self) -> int:
        return sum(1 for r in self.results if r.passed())

    def n_operator_correct(self) -> int:
        return sum(1 for r in self.results if r.operator_correct)

    def n_ready(self) -> int:
        return sum(1 for r in self.results if r.ready_for_langeng)

    def n_no_memory(self) -> int:
        return sum(1 for r in self.results if r.uncertainty_label == "no_memory")

    def p_operator(self) -> float:
        return self.n_operator_correct() / max(self.n_total(), 1)

    def p_ready(self) -> float:
        return self.n_ready() / max(self.n_total(), 1)

    def by_operator(self) -> dict[str, dict]:
        groups: dict[str, list[CaseResult]] = {}
        for r in self.results:
            groups.setdefault(r.case.expected_operator, []).append(r)
        out = {}
        for op, rs in groups.items():
            n = len(rs)
            n_op = sum(1 for r in rs if r.operator_correct)
            n_rdy = sum(1 for r in rs if r.ready_for_langeng)
            out[op] = {
                "n": n,
                "op_correct": n_op,
                "ready": n_rdy,
                "p_op": round(n_op / n, 2),
                "p_ready": round(n_rdy / n, 2),
                "avg_conf": round(sum(r.confidence for r in rs) / n, 3),
                "avg_pq": round(sum(r.plan_quality for r in rs) / n, 3),
            }
        return out


# ── Runner ────────────────────────────────────────────────────────────────────

def run_case(case: BenchCase, verbose: bool = False) -> CaseResult:
    try:
        plan = run_pipeline(
            query=case.query,
            chains=case.chains,
            source_lane=case.lane,
            goals=case.goals,
            claim=case.claim,
        )
        plan_d = plan.as_dict()
        selected_op = plan_d.get("operator_used", plan_d.get("operator", ""))

        # Uncertainty label from uncertainties list (pipeline inserts it as first item)
        label = "ok"
        for u in plan.uncertainties:
            if u.startswith("confidence:"):
                label = u.split(":", 1)[1].strip()
                break

        return CaseResult(
            case=case,
            selected_operator=selected_op,
            expected_operator=case.expected_operator,
            operator_correct=(selected_op == case.expected_operator),
            ready_for_langeng=plan.ready_for_langeng,
            confidence=plan_d.get("confidence", 0.0),
            uncertainty_label=label,
            plan_quality=plan.plan_quality,
            plan_dict=plan_d,
        )

    except Exception as exc:
        import traceback
        return CaseResult(
            case=case,
            selected_operator="ERROR",
            expected_operator=case.expected_operator,
            operator_correct=False,
            ready_for_langeng=False,
            confidence=0.0,
            uncertainty_label="error",
            plan_quality=0.0,
            plan_dict={},
            error=str(exc) + "\n" + traceback.format_exc(),
        )


def run_benchmark(
    operator_filter: str = "",
    verbose: bool = False,
) -> BenchReport:
    report = BenchReport()

    cases = BENCHMARK_CASES
    if operator_filter:
        cases = [c for c in cases if c.expected_operator == operator_filter.upper()]

    for case in cases:
        result = run_case(case, verbose=verbose)
        report.results.append(result)

        if verbose:
            print(result.summary_line())
            if result.error:
                print(f"  ERROR: {result.error[:300]}")
            elif verbose:
                plan = result.plan_dict
                print(f"  notes: {case.notes}")
                claims = plan.get("claims", [])
                if claims:
                    print(f"  claims[0]: {str(claims[0])[:120]}")
                uncerts = plan.get("uncertainties", [])
                if uncerts:
                    print(f"  uncertainty: {uncerts[0][:100]}")
                print()

    return report


def print_report(report: BenchReport) -> None:
    print()
    print("=" * 70)
    print("COGNITIVE OPERATOR BENCHMARK v0.1")
    print("=" * 70)
    print()

    for r in report.results:
        print(r.summary_line())

    print()
    print("-" * 70)
    print(f"TOTAL        : {report.n_total()}")
    print(f"PASS         : {report.n_pass()} / {report.n_total()}")
    print(f"P(op_correct): {report.p_operator():.3f}  ({report.n_operator_correct()}/{report.n_total()})")
    print(f"P(ready)     : {report.p_ready():.3f}  ({report.n_ready()}/{report.n_total()})")
    print(f"no_memory    : {report.n_no_memory()} / {report.n_total()}")
    print()

    print("By operator:")
    for op, stats in sorted(report.by_operator().items()):
        print(
            f"  {op:<25} n={stats['n']}  "
            f"p_op={stats['p_op']:.2f}  "
            f"p_rdy={stats['p_ready']:.2f}  "
            f"conf={stats['avg_conf']:.3f}  "
            f"pq={stats['avg_pq']:.3f}"
        )
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Cognitive operator benchmark")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--operator", "-o", default="", help="Filter to one operator type")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args()

    report = run_benchmark(operator_filter=args.operator, verbose=args.verbose)

    if args.json:
        out = {
            "total": report.n_total(),
            "pass": report.n_pass(),
            "p_operator_correct": report.p_operator(),
            "p_ready": report.p_ready(),
            "n_no_memory": report.n_no_memory(),
            "by_operator": report.by_operator(),
            "cases": [
                {
                    "name": r.case.name,
                    "expected": r.expected_operator,
                    "selected": r.selected_operator,
                    "correct": r.operator_correct,
                    "ready": r.ready_for_langeng,
                    "confidence": r.confidence,
                    "plan_quality": r.plan_quality,
                    "label": r.uncertainty_label,
                }
                for r in report.results
            ],
        }
        print(json.dumps(out, indent=2))
    else:
        if not args.verbose:
            # print summary lines even in non-verbose mode
            for r in report.results:
                print(r.summary_line())
        print_report(report)


if __name__ == "__main__":
    main()
