"""codeops/verification_bundle.py — synthesize §9 verification bundle from a run.

Adapter §16 / Phase D2: turns the orchestrator's final (code, runner_result, sandbox_check)
into the 14 columns + verdict that landed on claudecode.db.execution_traces in migration 016.

Layer hierarchy per Selyrion §9:
    parse → lint → typecheck → import → runtime → tests → benchmark → security

Verdict enum (write path):
    failed_parse, failed_static, failed_runtime,
    passed_minimal, passed_verified, passed_benchmarked
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import shutil
import subprocess

VERDICTS = (
    "failed_parse", "failed_static", "failed_runtime",
    "passed_minimal", "passed_verified", "passed_benchmarked",
)

_EXC_RE = re.compile(r"\b([A-Z][A-Za-z]+(?:Error|Exception|Warning))\b")
_STDLIB_TOP = {
    "sys", "os", "re", "json", "ast", "io", "math", "time", "pathlib",
    "subprocess", "sqlite3", "hashlib", "typing", "collections", "itertools",
    "functools", "datetime", "random", "string", "shutil", "tempfile",
    "importlib", "logging", "threading", "contextlib", "dataclasses",
    "enum", "abc", "weakref", "copy", "pickle", "csv", "argparse",
    "urllib", "http", "socket", "asyncio", "concurrent", "operator",
    "statistics", "decimal", "fractions", "warnings", "traceback", "inspect",
    "unittest", "platform", "queue", "select", "signal", "struct", "uuid",
}


def _parse_check(code: str) -> tuple[int, ast.AST | None]:
    try:
        tree = ast.parse(code)
        return 1, tree
    except SyntaxError:
        return 0, None


def _import_resolution(tree: ast.AST | None) -> int | None:
    if tree is None:
        return None
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                tops.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                tops.add(node.module.split(".")[0])
    if not tops:
        return 1
    for t in tops:
        if t in _STDLIB_TOP:
            continue
        try:
            spec = importlib.util.find_spec(t)
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            return 0
    return 1


def _tests_present(tree: ast.AST | None) -> int:
    if tree is None:
        return 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            return 1
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            return 1
    return 0


def _lint_check(code: str) -> int | None:
    if shutil.which("pyflakes") is None:
        return None
    try:
        r = subprocess.run(["pyflakes", "-"], input=code, text=True,
                           capture_output=True, timeout=5)
        return 1 if r.returncode == 0 else 0
    except Exception:
        return None


def _typecheck(code: str) -> int | None:
    return None


def _exception_type(stderr: str) -> str | None:
    if not stderr:
        return None
    m = _EXC_RE.search(stderr)
    return m.group(1) if m else None


def _verdict(b: dict) -> str:
    if b["parse_ok"] == 0:
        return "failed_parse"
    static_layers = (b["lint_ok"], b["typecheck_ok"], b["import_resolution_ok"])
    if any(v == 0 for v in static_layers):
        return "failed_static"
    if b.get("blocked_by_sandbox"):
        return "failed_static"
    if b["runtime_executed"] == 1 and b["runtime_exit_code"] not in (0, None):
        return "failed_runtime"
    if b["tests_present"] == 1 and (b["tests_failed"] or 0) > 0:
        return "failed_runtime"
    if b["tests_present"] == 1 and (b["tests_passed"] or 0) > 0 and (b["tests_failed"] or 0) == 0:
        return "passed_verified"
    return "passed_minimal"


def build(code: str, runner_result: dict | None,
          risks: list[dict] | None = None,
          blocked_by_sandbox: bool = False,
          tests_run: int | None = None,
          tests_passed: int | None = None,
          tests_failed: int | None = None,
          memory_mb: float | None = None) -> dict:
    """Return dict of all 14 verification_bundle cols + verdict.

    runner_result is the dict from codeops.runner.run() or None if sandbox blocked.
    """
    parse_ok, tree = _parse_check(code)
    lint_ok = _lint_check(code) if parse_ok else None
    type_ok = _typecheck(code) if parse_ok else None
    import_ok = _import_resolution(tree)
    tests_p = _tests_present(tree)

    if runner_result is None or blocked_by_sandbox:
        runtime_executed = 0
        runtime_exit_code = None
        runtime_exc = None
    else:
        runtime_executed = 1
        rc = runner_result.get("returncode")
        runtime_exit_code = int(rc) if rc is not None else None
        runtime_exc = _exception_type(runner_result.get("stderr", ""))

    bundle = {
        "parse_ok": parse_ok,
        "lint_ok": lint_ok,
        "typecheck_ok": type_ok,
        "import_resolution_ok": import_ok,
        "runtime_executed": runtime_executed,
        "runtime_exit_code": runtime_exit_code,
        "runtime_exception_type": runtime_exc,
        "tests_present": tests_p,
        "tests_run": tests_run,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "memory_mb": memory_mb,
        "risks_detected_json": json.dumps(risks) if risks else None,
        "blocked_by_sandbox": blocked_by_sandbox,
    }
    bundle["verdict"] = _verdict(bundle)
    bundle.pop("blocked_by_sandbox", None)
    return bundle
