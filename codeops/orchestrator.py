"""
codeops/orchestrator.py — Full autonomous generate→execute→fix→learn loop.

Usage:
  from codeops.orchestrator import run
  result = run(code)
  result = run(code, lang="bash", max_attempts=5, original_problem="...")
"""
from . import sandbox, runner, parser, fixer, reasoning_logger
from . import cms_query

MAX_ATTEMPTS = 5


def run(code: str, lang: str = "", max_attempts: int = MAX_ATTEMPTS,
        original_problem: str = "") -> dict:

    problem = original_problem or code

    # ── CMS knowledge check before first attempt ──────────────────────────────
    # Ask Selyrion: do I already know how to solve this?
    cms = cms_query.check(problem)
    cms_context = cms["context"] if cms["has_knowledge"] else ""

    for attempt in range(1, max_attempts + 1):

        # Safety gate
        safe, reason = sandbox.is_safe(code)
        if not safe:
            return {"status": "blocked", "reason": reason, "attempts": attempt,
                    "cms_confidence": cms["confidence"]}

        # Execute
        result = runner.run(code, lang=lang)
        error_class, subtype = parser.classify(result.get("stderr", ""))

        if result["returncode"] == 0:
            if attempt > 1:
                reasoning_logger.log_success(code, problem, code)
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
