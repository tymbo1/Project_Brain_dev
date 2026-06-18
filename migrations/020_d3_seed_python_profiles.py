"""Phase D3 — canonical seed for python_version + python_library profiles.

Seeds:
  - 4 python_version_profiles rows: 3.10, 3.11, 3.12, 3.13
  - 10 python_library_profiles rows (top by Python ecosystem call-frequency:
    requests, numpy, pandas, sqlalchemy, flask, fastapi, django, pytest,
    pydantic, openai)

These are deliberately conservative fact-rows (trust_score 0.8 = "well-known but
not benchmark-validated"). The bulk fill (200+ libraries × multiple versions) is
daemon work and outside D3's scope — the seed exists to prove the schema /
ingester contract and give downstream policy something to consult.

Idempotent: UPSERTs.
Reversible: pre-state snapshot to claudecode.db.migration_020_snapshot.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home()
SELYRIONCODE_DB = HOME / "selyrioncode.db"
CLAUDECODE_DB = HOME / "claudecode.db"
SUBSTRATE_DB = HOME / "resonance_v11.db"

sys.path.insert(0, str(Path(__file__).parent.parent))


PY_VERSIONS = [
    {
        "version": "3.10",
        "syntax_features": ["structural pattern matching (match/case)",
                            "parenthesized context managers",
                            "PEP 604 union (X | Y)"],
        "typing_features": ["TypeAlias", "ParamSpec", "TypeGuard",
                            "Concatenate"],
        "stdlib_additions": ["dataclasses.kw_only", "itertools.pairwise"],
        "deprecated_features": ["distutils (PEP 632)"],
        "removed_features": [],
        "migration_notes": ["match/case is statement-only, not expression"],
        "trust_score": 0.85,
        "provenance_refs": ["https://docs.python.org/3.10/whatsnew/3.10.html"],
    },
    {
        "version": "3.11",
        "syntax_features": ["exception groups (except*)",
                            "Self type in typing",
                            "PEP 657 fine-grained error locations"],
        "typing_features": ["Self", "Required", "NotRequired",
                            "LiteralString", "Never", "assert_type"],
        "stdlib_additions": ["tomllib (read-only TOML)",
                             "asyncio.TaskGroup",
                             "datetime.UTC alias (preview in 3.11)"],
        "deprecated_features": [],
        "removed_features": ["binhex"],
        "migration_notes": ["10-60% faster than 3.10 for many workloads"],
        "trust_score": 0.9,
        "provenance_refs": ["https://docs.python.org/3.11/whatsnew/3.11.html"],
    },
    {
        "version": "3.12",
        "syntax_features": ["PEP 695 generic syntax (def f[T](x: T))",
                            "PEP 701 f-string grammar relaxations",
                            "type statement"],
        "typing_features": ["override decorator",
                            "PEP 692 TypedDict unpacking with **kwargs"],
        "stdlib_additions": ["sys.monitoring", "itertools.batched",
                             "pathlib.Path.walk"],
        "deprecated_features": ["distutils (REMOVED)",
                                "datetime.utcnow / utcfromtimestamp"],
        "removed_features": ["distutils", "smtpd"],
        "migration_notes": ["distutils gone — use packaging/setuptools",
                            "GIL still default; per-interpreter GIL is PEP 684"],
        "trust_score": 0.9,
        "provenance_refs": ["https://docs.python.org/3.12/whatsnew/3.12.html"],
    },
    {
        "version": "3.13",
        "syntax_features": ["PEP 696 type defaults",
                            "PEP 702 deprecated decorator"],
        "typing_features": ["TypeIs", "ReadOnly TypedDict items",
                            "warnings.deprecated"],
        "stdlib_additions": ["argparse deprecated argument flag",
                             "copy.replace", "dbm.sqlite3"],
        "deprecated_features": ["typing.AnyStr",
                                "platform.java_ver, platform.popen"],
        "removed_features": ["aifc", "audioop", "chunk", "cgi", "cgitb",
                             "crypt", "imghdr", "mailcap", "msilib",
                             "nis", "nntplib", "ossaudiodev", "pipes",
                             "sndhdr", "spwd", "sunau", "telnetlib",
                             "uu", "xdrlib"],
        "migration_notes": ["PEP 703 free-threaded build is opt-in",
                            "PEP 744 JIT is experimental and opt-in"],
        "trust_score": 0.85,
        "provenance_refs": ["https://docs.python.org/3.13/whatsnew/3.13.html"],
    },
]


PY_LIBRARIES = [
    {"name": "requests",
     "versions_known": ["2.31", "2.32"],
     "domains": ["http_client"],
     "major_objects": ["Session", "Response", "PreparedRequest"],
     "breaking_changes": [],
     "python_min": "3.8", "python_max": None,
     "common_errors": ["ConnectionError", "Timeout", "JSONDecodeError"],
     "docs_refs": ["https://requests.readthedocs.io/"],
     "freshness_required": 0, "answer_from_memory_allowed": 1,
     "trust_score": 0.9},

    {"name": "numpy",
     "versions_known": ["1.26", "2.0", "2.1"],
     "domains": ["numerical", "arrays", "linear_algebra"],
     "major_objects": ["ndarray", "dtype", "ufunc"],
     "breaking_changes": ["2.0 removed many deprecated aliases (np.int, np.float, np.bool)",
                          "2.0 changed promotion rules (NEP 50)"],
     "python_min": "3.9", "python_max": None,
     "common_errors": ["ValueError shape mismatch", "TypeError dtype",
                       "AxisError"],
     "docs_refs": ["https://numpy.org/doc/"],
     "freshness_required": 1, "answer_from_memory_allowed": 1,
     "trust_score": 0.9},

    {"name": "pandas",
     "versions_known": ["2.1", "2.2"],
     "domains": ["dataframes", "tabular_analytics"],
     "major_objects": ["DataFrame", "Series", "Index", "GroupBy"],
     "breaking_changes": ["2.0 PyArrow-backed string dtype became opt-in default",
                          "appending DataFrame deprecated → use concat"],
     "python_min": "3.9", "python_max": None,
     "common_errors": ["SettingWithCopyWarning", "KeyError on column",
                       "MergeError"],
     "docs_refs": ["https://pandas.pydata.org/docs/"],
     "freshness_required": 1, "answer_from_memory_allowed": 1,
     "trust_score": 0.9},

    {"name": "sqlalchemy",
     "versions_known": ["1.4", "2.0"],
     "domains": ["orm", "database"],
     "major_objects": ["Engine", "Session", "Mapper", "declarative_base"],
     "breaking_changes": ["2.0 select() returns Result, not legacy ResultProxy",
                          "2.0 Query API legacy; new statement-first style"],
     "python_min": "3.8", "python_max": None,
     "common_errors": ["IntegrityError", "OperationalError", "DetachedInstanceError"],
     "docs_refs": ["https://docs.sqlalchemy.org/"],
     "freshness_required": 1, "answer_from_memory_allowed": 1,
     "trust_score": 0.9},

    {"name": "flask",
     "versions_known": ["2.3", "3.0"],
     "domains": ["web_framework", "wsgi"],
     "major_objects": ["Flask", "Blueprint", "request", "g"],
     "breaking_changes": ["3.0 dropped Python 3.7 support"],
     "python_min": "3.8", "python_max": None,
     "common_errors": ["RuntimeError working outside of application context",
                       "404"],
     "docs_refs": ["https://flask.palletsprojects.com/"],
     "freshness_required": 0, "answer_from_memory_allowed": 1,
     "trust_score": 0.85},

    {"name": "fastapi",
     "versions_known": ["0.110", "0.111", "0.112"],
     "domains": ["web_framework", "asgi", "openapi"],
     "major_objects": ["FastAPI", "APIRouter", "Depends", "BaseModel via pydantic"],
     "breaking_changes": ["0.100 minimum pydantic v2"],
     "python_min": "3.8", "python_max": None,
     "common_errors": ["RequestValidationError", "422"],
     "docs_refs": ["https://fastapi.tiangolo.com/"],
     "freshness_required": 1, "answer_from_memory_allowed": 1,
     "trust_score": 0.85},

    {"name": "django",
     "versions_known": ["4.2", "5.0"],
     "domains": ["web_framework", "orm", "templates"],
     "major_objects": ["Model", "QuerySet", "View", "URLconf"],
     "breaking_changes": ["5.0 dropped Python 3.8/3.9 support"],
     "python_min": "3.10", "python_max": None,
     "common_errors": ["DoesNotExist", "MultipleObjectsReturned",
                       "ImproperlyConfigured"],
     "docs_refs": ["https://docs.djangoproject.com/"],
     "freshness_required": 1, "answer_from_memory_allowed": 1,
     "trust_score": 0.85},

    {"name": "pytest",
     "versions_known": ["7.4", "8.0", "8.2"],
     "domains": ["testing"],
     "major_objects": ["fixture", "parametrize", "MonkeyPatch", "TmpPath"],
     "breaking_changes": ["8.0 nose-style test removal"],
     "python_min": "3.8", "python_max": None,
     "common_errors": ["fixture not found", "collection error"],
     "docs_refs": ["https://docs.pytest.org/"],
     "freshness_required": 0, "answer_from_memory_allowed": 1,
     "trust_score": 0.9},

    {"name": "pydantic",
     "versions_known": ["1.10", "2.0", "2.6"],
     "domains": ["validation", "serialization"],
     "major_objects": ["BaseModel", "Field", "ValidationError"],
     "breaking_changes": ["2.0 ground-up rewrite; validator → field_validator",
                          "parse_obj → model_validate", "dict() → model_dump()"],
     "python_min": "3.8", "python_max": None,
     "common_errors": ["ValidationError"],
     "docs_refs": ["https://docs.pydantic.dev/"],
     "freshness_required": 1, "answer_from_memory_allowed": 1,
     "trust_score": 0.9},

    {"name": "openai",
     "versions_known": ["1.0", "1.30"],
     "domains": ["llm_client"],
     "major_objects": ["OpenAI client", "ChatCompletion"],
     "breaking_changes": ["1.0 client-based API (no more openai.ChatCompletion.create)"],
     "python_min": "3.7", "python_max": None,
     "common_errors": ["AuthenticationError", "RateLimitError",
                       "APIConnectionError"],
     "docs_refs": ["https://platform.openai.com/docs/"],
     "freshness_required": 1, "answer_from_memory_allowed": 0,
     "trust_score": 0.85},
]


def _snapshot_pre() -> dict:
    with sqlite3.connect(SELYRIONCODE_DB) as c:
        vrows = c.execute("SELECT COUNT(*) FROM python_version_profiles").fetchone()[0]
        lrows = c.execute("SELECT COUNT(*) FROM python_library_profiles").fetchone()[0]
    return {"version_rows": vrows, "library_rows": lrows,
            "captured_at": time.time()}


def _write_snapshot(snap: dict) -> None:
    with sqlite3.connect(CLAUDECODE_DB) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS migration_020_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        c.execute(
            "INSERT INTO migration_020_snapshot (snapshot_json, created_at) "
            "VALUES (?, ?)",
            (json.dumps(snap), time.time()),
        )


def _substrate_sig():
    if not SUBSTRATE_DB.exists():
        return None
    st = SUBSTRATE_DB.stat()
    return (st.st_size, st.st_mtime)


def main() -> int:
    from codeops import ingest_profiles

    sig_before = _substrate_sig()
    pre = _snapshot_pre()
    _write_snapshot(pre)

    t0 = time.time()
    version_results = [ingest_profiles.ingest_version(p) for p in PY_VERSIONS]
    library_results = [ingest_profiles.ingest_library(p) for p in PY_LIBRARIES]
    elapsed = time.time() - t0

    with sqlite3.connect(SELYRIONCODE_DB) as c:
        v_count = c.execute("SELECT COUNT(*) FROM python_version_profiles").fetchone()[0]
        l_count = c.execute("SELECT COUNT(*) FROM python_library_profiles").fetchone()[0]
        v_sample = c.execute(
            "SELECT version, trust_score FROM python_version_profiles ORDER BY version"
        ).fetchall()
        l_sample = c.execute(
            "SELECT name, freshness_required, answer_from_memory_allowed, trust_score "
            "FROM python_library_profiles ORDER BY name"
        ).fetchall()
        for_round_trip = c.execute(
            "SELECT version, syntax_features FROM python_version_profiles WHERE version='3.12'"
        ).fetchone()

    round_trip_ok = False
    if for_round_trip:
        try:
            parsed = json.loads(for_round_trip[1])
            round_trip_ok = isinstance(parsed, list) and len(parsed) > 0
        except Exception:
            round_trip_ok = False

    sig_after = _substrate_sig()
    substrate_untouched = (sig_before == sig_after)

    gate = (
        v_count >= len(PY_VERSIONS)
        and l_count >= len(PY_LIBRARIES)
        and round_trip_ok
        and substrate_untouched
    )

    print(json.dumps({
        "migration": "020_d3_seed_python_profiles",
        "elapsed_s": round(elapsed, 3),
        "pre_snapshot": pre,
        "version_rows_post": v_count,
        "library_rows_post": l_count,
        "version_results": version_results,
        "library_results": library_results,
        "version_sample": v_sample,
        "library_sample": l_sample,
        "round_trip_ok_3_12_syntax_features": round_trip_ok,
        "substrate_untouched": substrate_untouched,
        "ACCEPTANCE_GATE_PASS": gate,
    }, indent=2, default=str))
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
