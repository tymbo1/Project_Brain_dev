"""
codeops/orchestrator.py — Full autonomous generate→execute→fix→learn loop.

Usage:
  from codeops.orchestrator import run
  result = run(code)
  result = run(code, lang="bash", max_attempts=5, original_problem="...")
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from . import sandbox, runner, parser, fixer, reasoning_logger
from . import cms_query
from . import verification_bundle as _vbundle
try:
    from trace_writer import write_verification_trace as _write_vtrace
except Exception:
    _write_vtrace = None

MAX_ATTEMPTS = 5


def _emit_verification_trace(code, runner_result, blocked, attempts,
                             problem, lang, started_at, outcome, final_out):
    if _write_vtrace is None:
        return
    risks = sandbox.risks_detected(code) if blocked else None
    bundle = _vbundle.build(
        code=code,
        runner_result=runner_result,
        risks=risks,
        blocked_by_sandbox=blocked,
    )
    try:
        _write_vtrace(
            tool_name="codeops.orchestrator",
            session_id="codeops",
            intent=problem[:200],
            domain_tag="programming",
            outcome=outcome,
            final_output=final_out[:2000],
            runtime_ms=int((time.time() - started_at) * 1000),
            bundle=bundle,
            tool_chain=["sandbox", "runner", "parser", "fixer"][:1 + 3 * (not blocked)],
        )
    except Exception:
        pass


def run(code: str, lang: str = "", max_attempts: int = MAX_ATTEMPTS,
        original_problem: str = "") -> dict:

    problem = original_problem or code
    _t0 = time.time()

    # ── CMS knowledge check before first attempt ──────────────────────────────
    # Ask Selyrion: do I already know how to solve this?
    cms = cms_query.check(problem)
    cms_context = cms["context"] if cms["has_knowledge"] else ""

    result = None
    for attempt in range(1, max_attempts + 1):

        # Safety gate
        safe, reason = sandbox.is_safe(code)
        if not safe:
            _emit_verification_trace(code, None, True, attempt, problem, lang,
                                     _t0, "blocked", reason)
            return {"status": "blocked", "reason": reason, "attempts": attempt,
                    "cms_confidence": cms["confidence"]}

        # Execute
        result = runner.run(code, lang=lang)
        error_class, subtype = parser.classify(result.get("stderr", ""))

        if result["returncode"] == 0:
            if attempt > 1:
                reasoning_logger.log_success(code, problem, code)
            _emit_verification_trace(code, result, False, attempt, problem, lang,
                                     _t0, "success", result.get("stdout", ""))
            return {
                "status":          "success",
                "code":            code,
                "output":          result["stdout"],
                "attempts":        attempt,
                "lang":            result.get("lang", lang),
                "elapsed":         result.get("elapsed", 0),
                "cms_confidence":  cms["confidence"],
                "cms_used":        cms["has_knowledge"],
            }

        # Re-check CMS with error class now known — may surface better context
        if attempt == 1 and (error_class or subtype):
            cms_err = cms_query.check(
                problem, error_class=error_class, subtype=subtype
            )
            if cms_err["confidence"] > cms["confidence"]:
                cms = cms_err
                cms_context = cms["context"]

        # Log failure
        reasoning_logger.log(attempt, code, result, error_class, subtype, "")

        if attempt == max_attempts:
            break

        # Attempt fix — inject CMS context into LLM tier if available
        fixed_code, fix_desc = fixer.apply(
            code, result.get("stderr", ""), error_class, subtype,
            cms_context=cms_context
        )

        reasoning_logger.log(attempt, code, result, error_class, subtype, fix_desc)

        if fixed_code == code:
            break

        code = fixed_code

    _emit_verification_trace(code, result, False, attempt, problem, lang,
                             _t0, "failure", result.get("stderr", "") if result else "")
    return {
        "status":         "failed",
        "last_code":      code,
        "error":          result.get("stderr", ""),
        "error_class":    error_class,
        "subtype":        subtype,
        "attempts":       attempt,
        "cms_confidence": cms["confidence"],
        "cms_context":    cms_context,
    }


def run_parallel(candidates: list[str], lang: str = "",
                 original_problem: str = "") -> dict:
    """Run multiple code candidates simultaneously, return first success or best result."""
    import threading

    # Single CMS check shared across all candidates
    problem = original_problem or (candidates[0] if candidates else "")
    cms = cms_query.check(problem)

    results = {}
    lock    = threading.Lock()
    winner  = [None]

    def worker(idx: int, code: str):
        safe, _ = sandbox.is_safe(code)
        if not safe:
            return
        r = runner.run(code, lang=lang)
        with lock:
            results[idx] = (code, r)
            if r["returncode"] == 0 and winner[0] is None:
                winner[0] = idx

    threads = [threading.Thread(target=worker, args=(i, c))
               for i, c in enumerate(candidates)]
    for t in threads: t.start()
    for t in threads: t.join()

    if winner[0] is not None:
        code, r = results[winner[0]]
        return {"status": "success", "code": code, "output": r["stdout"],
                "candidate": winner[0], "cms_confidence": cms["confidence"]}

    best_idx = min(results, key=lambda i: len(results[i][1].get("stderr", "")))
    code, r  = results[best_idx]
    return {"status": "failed", "last_code": code,
            "error": r.get("stderr", ""), "candidate": best_idx,
            "cms_confidence": cms["confidence"]}
