"""
Phase 2.0.4 — operator trace format + storage.

Locked policy:
- Q-2.0.J: summary always-on (mirror); full detail env-flag-gated.
- Q-2.0.K: JSONL 30d rolling; mirror keep-all but compact summary-only.

Storage:
- mirror: claudecode.db.operator_runs (compact: input_keys/output_keys/decision_count only)
- full:   ${OPERATOR_TRACE_DIR}/operator_trace_YYYY-MM-DD.jsonl (one record per line)

Flags read lazily per call so tests can flip them:
- OPERATOR_TRACE_FULL_ENABLED ("1" enables full JSONL)
- OPERATOR_TRACE_DIR (default /home/timbushnell/operator_traces)
- CLAUDECODE_DB_PATH (default /home/timbushnell/claudecode.db)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

DEFAULT_TRACE_DIR = "/home/timbushnell/operator_traces"
DEFAULT_CLAUDECODE_DB = "/home/timbushnell/claudecode.db"
TRACE_RETENTION_DAYS = 30


def _trace_dir() -> Path:
    p = Path(os.environ.get("OPERATOR_TRACE_DIR", DEFAULT_TRACE_DIR))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _claudecode_db_path() -> str:
    return os.environ.get("CLAUDECODE_DB_PATH", DEFAULT_CLAUDECODE_DB)


def _full_enabled() -> bool:
    return os.environ.get("OPERATOR_TRACE_FULL_ENABLED", "0") == "1"


def _default_session_id() -> str:
    return os.environ.get(
        "SESSION_ID",
        "session." + dt.date.today().isoformat(),
    )


def _ensure_mirror_schema() -> None:
    with sqlite3.connect(_claudecode_db_path()) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS operator_runs ("
            "  trace_id TEXT PRIMARY KEY,"
            "  ts INTEGER NOT NULL,"
            "  session_id TEXT,"
            "  operator TEXT NOT NULL,"
            "  working_set_id TEXT,"
            "  outcome TEXT NOT NULL,"
            "  duration_ms REAL,"
            "  score REAL,"
            "  summary TEXT NOT NULL"
            ")"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_oprun_ts ON operator_runs (ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_oprun_op ON operator_runs (operator)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_oprun_ws ON operator_runs (working_set_id)"
        )
        conn.commit()


def _compact_summary(operator: str, outcome: str,
                     inputs, outputs, decisions, score,
                     reason: str | None) -> dict:
    in_keys = list(inputs.keys()) if isinstance(inputs, dict) else []
    out_keys = list(outputs.keys()) if isinstance(outputs, dict) else []
    return {
        "operator": operator,
        "outcome": outcome,
        "score": score,
        "input_keys": in_keys,
        "output_keys": out_keys,
        "decision_count": len(decisions) if decisions is not None else 0,
        "reason": reason,
    }


def emit(operator: str,
         inputs=None,
         outputs=None,
         outcome: str = "ok",
         working_set_id: str | None = None,
         session_id: str | None = None,
         decisions=None,
         score: float | None = None,
         duration_ms: float | None = None,
         provenance: dict | None = None,
         reason: str | None = None) -> str:
    """
    Emit one trace record. Always writes a compact summary to the mirror;
    optionally appends a full record to JSONL when OPERATOR_TRACE_FULL_ENABLED=1.

    Returns the trace_id.
    """
    inputs = inputs if inputs is not None else {}
    outputs = outputs if outputs is not None else {}
    session_id = session_id or _default_session_id()

    trace_id = "tr." + uuid.uuid4().hex[:12]
    ts = int(time.time())

    summary = _compact_summary(operator, outcome, inputs, outputs,
                               decisions, score, reason)

    _ensure_mirror_schema()
    with sqlite3.connect(_claudecode_db_path()) as conn:
        conn.execute(
            "INSERT INTO operator_runs "
            "(trace_id, ts, session_id, operator, working_set_id, "
            " outcome, duration_ms, score, summary) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (trace_id, ts, session_id, operator, working_set_id,
             outcome, duration_ms, score, json.dumps(summary)),
        )
        conn.commit()

    if _full_enabled():
        record = {
            "trace_id": trace_id,
            "ts": ts,
            "session_id": session_id,
            "operator": operator,
            "working_set_id": working_set_id,
            "inputs": inputs,
            "outputs": outputs,
            "decisions": decisions or [],
            "scores": {"score": score},
            "duration_ms": duration_ms,
            "outcome": outcome,
            "reason": reason,
            "provenance": provenance or {},
        }
        day = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date().isoformat()
        path = _trace_dir() / f"operator_trace_{day}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return trace_id


def read_full(trace_id: str) -> dict | None:
    """Scan recent JSONL files for a full record by trace_id."""
    d = _trace_dir()
    files = sorted(d.glob("operator_trace_*.jsonl"), reverse=True)
    for f in files:
        with f.open("r", encoding="utf-8") as fh:
            for line in fh:
                if trace_id in line:
                    rec = json.loads(line)
                    if rec.get("trace_id") == trace_id:
                        return rec
    return None


def read_summary(trace_id: str) -> dict | None:
    _ensure_mirror_schema()
    with sqlite3.connect(_claudecode_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM operator_runs WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["summary"] = json.loads(d["summary"])
        return d


def sweep_jsonl(retention_days: int = TRACE_RETENTION_DAYS) -> dict:
    """Remove JSONL files older than retention_days. Returns count + names."""
    d = _trace_dir()
    cutoff = dt.date.today() - dt.timedelta(days=retention_days)
    removed: list[str] = []
    for f in d.glob("operator_trace_*.jsonl"):
        try:
            day_str = f.stem.replace("operator_trace_", "")
            day = dt.date.fromisoformat(day_str)
        except ValueError:
            continue
        if day < cutoff:
            f.unlink()
            removed.append(f.name)
    return {"removed_count": len(removed), "removed": removed,
            "cutoff": cutoff.isoformat()}
