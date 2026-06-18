"""
trace_writer.py — Shared execution trace writer for all SCOS tools.

Every tool call, task run, parliament round, and advisor call writes here.
Traces are the observability backbone — without them, failures surface by
inspection rather than by signal.

Usage:
    from trace_writer import Trace

    with Trace("run_task", session_id, domain="chess", intent="evaluate pawn structure") as t:
        result = do_work()
        t.set_tool_chain(["memory_search", "parliament"])
        t.set_memory_reads(reads)
        t.set_confidence_flow(confs)
        t.set_output(result)
        t.succeed()   # or t.fail("reason") / t.partial("reason")

    # One-shot (no context manager):
    Trace.write_one(
        tool_name="advisor",
        session_id=sid,
        intent=question,
        outcome="success",
        final_output=answer[:500],
        domain_tag="architecture",
        runtime_ms=elapsed,
    )
"""

import sqlite3, hashlib, time, json
from pathlib import Path
from typing import Any

CLAUDECODE_DB = Path.home() / "claudecode.db"


def _tid(tool_name: str) -> str:
    key = f"{tool_name}{time.time()}{id(object())}"
    return "trace." + hashlib.md5(key.encode()).hexdigest()[:10]


def _h(data: Any) -> str:
    """Short content hash for dedup/replay."""
    return hashlib.sha1(str(data).encode()).hexdigest()[:12]


class Trace:
    """Context-manager trace recorder."""

    def __init__(self, tool_name: str, session_id: str = "unknown",
                 domain: str = "", intent: str = "",
                 parent_trace_id: str | None = None):
        self.id              = _tid(tool_name)
        self.tool_name       = tool_name
        self.session_id      = session_id
        self.domain_tag      = domain
        self.intent          = intent
        self.parent_trace_id = parent_trace_id
        self.started_at      = time.time()
        self._tool_chain:     list  = [tool_name]
        self._memory_reads:   list  = []
        self._memory_writes:  list  = []
        self._contradictions: list  = []
        self._confidence_flow: list = []
        self._final_output:   str   = ""
        self._input_hash:     str   = _h(intent)
        self._outcome:        str   = "unknown"
        self._runtime_ms:     int   = 0

    # ── Setters ───────────────────────────────────────────────────────────────

    def set_tool_chain(self, chain: list):
        self._tool_chain = chain

    def add_tool(self, name: str):
        self._tool_chain.append(name)

    def set_memory_reads(self, reads: list):
        self._memory_reads = reads

    def set_memory_writes(self, writes: list):
        self._memory_writes = writes

    def add_contradiction(self, contra: dict):
        self._contradictions.append(contra)

    def set_confidence_flow(self, flow: list):
        self._confidence_flow = flow

    def add_confidence(self, step: str, value: float):
        self._confidence_flow.append({"step": step, "conf": value})

    def set_output(self, output: Any):
        self._final_output = str(output)[:2000]

    def succeed(self):
        self._outcome = "success"

    def fail(self, reason: str = ""):
        self._outcome = "failure"
        if reason:
            self._final_output = reason[:500]

    def partial(self, reason: str = ""):
        self._outcome = "partial"
        if reason:
            self._final_output = (self._final_output + " | " + reason)[:500]

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None and self._outcome == "unknown":
            self._outcome = "failure"
            self._final_output = f"{exc_type.__name__}: {exc_val}"[:500]
        elif self._outcome == "unknown":
            self._outcome = "success"
        self._runtime_ms = int((time.time() - self.started_at) * 1000)
        self._flush()
        return False  # don't suppress exceptions

    # ── DB write ──────────────────────────────────────────────────────────────

    def _flush(self):
        try:
            conn = sqlite3.connect(CLAUDECODE_DB)
            conn.execute("""
                INSERT OR IGNORE INTO execution_traces (
                    id, session_id, intent, tool_name, tool_chain,
                    memory_reads, memory_writes, contradictions, confidence_flow,
                    final_output, runtime_ms, started_at, finished_at,
                    domain_tag, outcome, input_hash, parent_trace_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                self.id,
                self.session_id,
                self.intent[:200],
                self.tool_name,
                json.dumps(self._tool_chain),
                json.dumps(self._memory_reads)[:2000],
                json.dumps(self._memory_writes)[:2000],
                json.dumps(self._contradictions)[:1000],
                json.dumps(self._confidence_flow)[:1000],
                self._final_output,
                self._runtime_ms,
                self.started_at,
                time.time(),
                self.domain_tag,
                self._outcome,
                self._input_hash,
                self.parent_trace_id,
            ))
            conn.commit(); conn.close()
        except Exception:
            pass

    # ── One-shot helper ───────────────────────────────────────────────────────

    @staticmethod
    def write_one(tool_name: str, session_id: str = "unknown",
                  intent: str = "", domain_tag: str = "",
                  outcome: str = "success", final_output: str = "",
                  runtime_ms: int = 0, parent_trace_id: str | None = None,
                  tool_chain: list | None = None,
                  memory_reads: list | None = None,
                  memory_writes: list | None = None,
                  confidence_flow: list | None = None) -> str:
        """Write a single trace record. Returns the trace id."""
        t = Trace(tool_name, session_id, domain=domain_tag, intent=intent,
                  parent_trace_id=parent_trace_id)
        t._outcome         = outcome
        t._final_output    = final_output[:2000]
        t._runtime_ms      = runtime_ms
        t._tool_chain      = tool_chain or [tool_name]
        t._memory_reads    = memory_reads or []
        t._memory_writes   = memory_writes or []
        t._confidence_flow = confidence_flow or []
        t._flush()
        return t.id


# ── Query helpers (for advisor context, dashboard, etc.) ──────────────────────

_VERDICTS = {
    "failed_parse", "failed_static", "failed_runtime",
    "passed_minimal", "passed_verified", "passed_benchmarked",
}
_BUNDLE_COLS = (
    "parse_ok", "lint_ok", "typecheck_ok", "import_resolution_ok",
    "runtime_executed", "runtime_exit_code", "runtime_exception_type",
    "tests_present", "tests_run", "tests_passed", "tests_failed",
    "memory_mb", "risks_detected_json", "verdict",
)


def write_verification_trace(*, tool_name: str, session_id: str, intent: str,
                             domain_tag: str, outcome: str,
                             final_output: str, runtime_ms: int,
                             bundle: dict, tool_chain: list | None = None,
                             parent_trace_id: str | None = None,
                             codeunit_id: str | None = None) -> str:
    """Insert one execution_traces row with verification-bundle cols populated.

    bundle keys: parse_ok, lint_ok, typecheck_ok, import_resolution_ok,
    runtime_executed, runtime_exit_code, runtime_exception_type,
    tests_present, tests_run, tests_passed, tests_failed, memory_mb,
    risks_detected_json, verdict.
    """
    verdict = bundle.get("verdict")
    if verdict is not None and verdict not in _VERDICTS:
        raise ValueError(f"unknown verdict: {verdict!r}")

    tid = _tid(tool_name)
    started = time.time()
    base = {
        "id": tid,
        "session_id": session_id,
        "intent": intent[:200],
        "tool_name": tool_name,
        "tool_chain": json.dumps(tool_chain or [tool_name]),
        "memory_reads": "[]",
        "memory_writes": "[]",
        "contradictions": "[]",
        "confidence_flow": "[]",
        "final_output": final_output[:2000],
        "runtime_ms": runtime_ms,
        "started_at": started,
        "finished_at": started,
        "domain_tag": domain_tag,
        "outcome": outcome,
        "input_hash": _h(intent),
        "parent_trace_id": parent_trace_id,
    }
    bundle_vals = {c: bundle.get(c) for c in _BUNDLE_COLS}
    extra = {"codeunit_id": codeunit_id} if codeunit_id is not None else {}
    cols = list(base) + list(bundle_vals) + list(extra)
    vals = ([base[c] for c in base]
            + [bundle_vals[c] for c in bundle_vals]
            + [extra[c] for c in extra])
    placeholders = ",".join("?" * len(cols))
    sql = (
        f"INSERT OR IGNORE INTO execution_traces ({','.join(cols)}) "
        f"VALUES ({placeholders})"
    )
    try:
        conn = sqlite3.connect(CLAUDECODE_DB)
        conn.execute(sql, vals)
        conn.commit(); conn.close()
    except Exception:
        pass
    return tid


def recent_traces(n: int = 20, domain: str = "", outcome: str = "",
                  tool: str = "") -> list[dict]:
    """Return recent traces as dicts, optionally filtered."""
    filters, params = [], []
    if domain:  filters.append("domain_tag = ?"); params.append(domain)
    if outcome: filters.append("outcome = ?");    params.append(outcome)
    if tool:    filters.append("tool_name = ?");  params.append(tool)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    params.append(n)
    try:
        conn = sqlite3.connect(CLAUDECODE_DB)
        rows = conn.execute(f"""
            SELECT id, session_id, tool_name, intent, domain_tag,
                   outcome, runtime_ms, started_at
            FROM execution_traces {where}
            ORDER BY started_at DESC LIMIT ?
        """, params).fetchall()
        conn.close()
        return [
            dict(id=r[0], session_id=r[1], tool_name=r[2], intent=r[3],
                 domain_tag=r[4], outcome=r[5], runtime_ms=r[6], started_at=r[7])
            for r in rows
        ]
    except Exception:
        return []


def failure_rate(tool: str = "", domain: str = "", window_hours: int = 24) -> dict:
    """Return success/failure counts for a tool or domain in the last N hours."""
    since = time.time() - window_hours * 3600
    filters = ["started_at >= ?"]
    params  = [since]
    if tool:   filters.append("tool_name = ?");  params.append(tool)
    if domain: filters.append("domain_tag = ?"); params.append(domain)
    where = "WHERE " + " AND ".join(filters)
    try:
        conn = sqlite3.connect(CLAUDECODE_DB)
        rows = conn.execute(f"""
            SELECT outcome, COUNT(*) FROM execution_traces {where}
            GROUP BY outcome
        """, params).fetchall()
        conn.close()
        counts = {r[0]: r[1] for r in rows}
        total  = sum(counts.values())
        return {"counts": counts, "total": total,
                "failure_rate": counts.get("failure", 0) / total if total else 0.0}
    except Exception:
        return {"counts": {}, "total": 0, "failure_rate": 0.0}
