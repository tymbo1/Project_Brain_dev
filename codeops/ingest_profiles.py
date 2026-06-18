"""codeops/ingest_profiles.py — Phase D3 profile ingester.

Validates + upserts rows into selyrioncode.python_version_profiles and
python_library_profiles. Daemon-friendly: idempotent on (version) / (library_id),
accepts dict payloads, single source of truth for the JSON-list contract.

Designed for offload: archaeologist / local LLM workers can call ingest_version()
or ingest_library() with structured payloads. Opus is not in the hot path.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SELYRIONCODE_DB = Path.home() / "selyrioncode.db"

_VERSION_LIST_COLS = (
    "syntax_features", "typing_features", "stdlib_additions",
    "deprecated_features", "removed_features", "migration_notes",
    "provenance_refs",
)
_LIBRARY_LIST_COLS = (
    "versions_known", "domains", "major_objects", "breaking_changes",
    "common_errors", "docs_refs",
)


def _norm_lists(payload: dict, list_cols: tuple) -> dict:
    out = {}
    for c in list_cols:
        v = payload.get(c)
        if v is None:
            out[c] = None
        elif isinstance(v, str):
            out[c] = v  # already JSON
        elif isinstance(v, list):
            out[c] = json.dumps(v)
        else:
            raise TypeError(f"{c}: expected list or JSON string, got {type(v).__name__}")
    return out


def ingest_version(payload: dict) -> dict:
    """Upsert one python_version_profiles row.

    Required keys: version, trust_score.
    Optional: syntax_features / typing_features / stdlib_additions /
              deprecated_features / removed_features / migration_notes /
              provenance_refs (each: list[str] or JSON string).
    """
    version = payload["version"]
    trust = float(payload.get("trust_score", 0.0))
    if not (0.0 <= trust <= 1.0):
        raise ValueError(f"trust_score out of [0,1]: {trust}")
    lists = _norm_lists(payload, _VERSION_LIST_COLS)
    now = time.time()

    with sqlite3.connect(SELYRIONCODE_DB) as c:
        existing = c.execute(
            "SELECT version FROM python_version_profiles WHERE version=?",
            (version,),
        ).fetchone()
        if existing:
            sets = ", ".join([f"{k}=?" for k in _VERSION_LIST_COLS]) + \
                   ", trust_score=?, updated_at=?"
            c.execute(
                f"UPDATE python_version_profiles SET {sets} WHERE version=?",
                tuple(lists[k] for k in _VERSION_LIST_COLS) + (trust, now, version),
            )
            action = "updated"
        else:
            cols = ("version",) + _VERSION_LIST_COLS + ("trust_score", "created_at", "updated_at")
            vals = ((version,)
                    + tuple(lists[k] for k in _VERSION_LIST_COLS)
                    + (trust, now, now))
            c.execute(
                f"INSERT INTO python_version_profiles ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                vals,
            )
            action = "inserted"
        c.commit()
    return {"action": action, "version": version}


def ingest_library(payload: dict) -> dict:
    """Upsert one python_library_profiles row.

    Required keys: name, trust_score.
    Optional: versions_known / domains / major_objects / breaking_changes /
              common_errors / docs_refs (lists);
              python_min / python_max (str);
              freshness_required / answer_from_memory_allowed (0|1).
    """
    name = payload["name"]
    library_id = payload.get("library_id") or f"py_lib::{name}"
    trust = float(payload.get("trust_score", 0.0))
    if not (0.0 <= trust <= 1.0):
        raise ValueError(f"trust_score out of [0,1]: {trust}")
    fresh = int(payload.get("freshness_required", 0))
    afm = int(payload.get("answer_from_memory_allowed", 1))
    if fresh not in (0, 1) or afm not in (0, 1):
        raise ValueError("freshness_required / answer_from_memory_allowed must be 0|1")
    lists = _norm_lists(payload, _LIBRARY_LIST_COLS)
    py_min = payload.get("python_min")
    py_max = payload.get("python_max")
    now = time.time()

    cols = (
        ("library_id", "name") + _LIBRARY_LIST_COLS
        + ("python_min", "python_max", "freshness_required",
           "answer_from_memory_allowed", "trust_score", "created_at", "updated_at")
    )

    with sqlite3.connect(SELYRIONCODE_DB) as c:
        existing = c.execute(
            "SELECT library_id FROM python_library_profiles WHERE library_id=?",
            (library_id,),
        ).fetchone()
        if existing:
            update_cols = cols[1:-2]  # skip library_id PK and created_at; keep updated_at
            sets = ", ".join(f"{k}=?" for k in update_cols) + ", updated_at=?"
            vals = (
                (name,)
                + tuple(lists[k] for k in _LIBRARY_LIST_COLS)
                + (py_min, py_max, fresh, afm, trust, now)
            )
            c.execute(
                f"UPDATE python_library_profiles SET {sets} WHERE library_id=?",
                vals + (library_id,),
            )
            action = "updated"
        else:
            vals = (
                (library_id, name)
                + tuple(lists[k] for k in _LIBRARY_LIST_COLS)
                + (py_min, py_max, fresh, afm, trust, now, now)
            )
            c.execute(
                f"INSERT INTO python_library_profiles ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                vals,
            )
            action = "inserted"
        c.commit()
    return {"action": action, "library_id": library_id}
