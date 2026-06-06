"""
lc_db.py — language_cognition.db schema initializer and access layer.

language_cognition.db is a SEPARATE database from CMS (resonance_v11.db)
and from selyrionstory.db. It is Selyrion's language understanding substrate:
  - Conceptual definitions of language constructs
  - Speech act records with scored examples
  - Intent pattern → pragmatic act mapping
  - Pragmatic rules (structured, inspectable)
  - Realized expression examples (for naturalness learning)
  - Benchmark cases (ground truth for quality measurement)

Tables:
  lc_concepts          — definitions of language/pragmatics concepts
  lc_speech_acts       — the 14 speech acts with descriptions + examples
  lc_intent_patterns   — input_pattern → inferred_intent + pragmatic act mapping
  lc_pragmatic_rules   — structured rules (mirrors pragmatics.py _RULES)
  lc_realizations      — (speech_act, meaning_type, content) → realized_form
  lc_benchmark         — (query, expected_speech_act, expected_intent, notes)

Seed volume targets:
  lc_concepts:        ~500  (language ontology)
  lc_speech_acts:     ~120  (14 acts × ~8 examples each)
  lc_intent_patterns: ~800  (intent signal × context)
  lc_pragmatic_rules:  ~80  (rule instances)
  lc_realizations:    ~600  (realization examples)
  lc_benchmark:       ~200  (evaluation cases — 100 minimum before shipping)
"""

from __future__ import annotations
import sqlite3
import time
from pathlib import Path

_LC_DB_PATH = Path.home() / "language_cognition.db"


def get_db(path: Path | None = None) -> sqlite3.Connection:
    p = path or _LC_DB_PATH
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path | None = None) -> None:
    """Create all tables if they don't exist. Safe to run repeatedly."""
    conn = get_db(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS lc_concepts (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL UNIQUE,
            domain       TEXT DEFAULT 'pragmatics',
            definition   TEXT NOT NULL,
            example      TEXT,
            parent       TEXT,
            created_at   REAL
        );

        CREATE TABLE IF NOT EXISTS lc_speech_acts (
            id           TEXT PRIMARY KEY,
            act_name     TEXT NOT NULL,
            description  TEXT NOT NULL,
            when_to_use  TEXT,
            example_query TEXT,
            example_response TEXT,
            operator_affinity TEXT,
            confidence   REAL DEFAULT 0.9,
            created_at   REAL
        );

        CREATE TABLE IF NOT EXISTS lc_intent_patterns (
            id              TEXT PRIMARY KEY,
            input_pattern   TEXT NOT NULL,
            user_intent     TEXT NOT NULL,
            assistant_speech_act TEXT NOT NULL,
            emotional_tone  TEXT DEFAULT 'neutral',
            must_not        TEXT DEFAULT '[]',
            must_do         TEXT DEFAULT '[]',
            required_depth  TEXT DEFAULT 'standard',
            confidence      REAL DEFAULT 0.8,
            created_at      REAL
        );

        CREATE TABLE IF NOT EXISTS lc_pragmatic_rules (
            id              TEXT PRIMARY KEY,
            rule_name       TEXT NOT NULL UNIQUE,
            trigger_text    TEXT NOT NULL,
            inferred_intent TEXT NOT NULL,
            pragmatic_act   TEXT NOT NULL,
            emotional_signal TEXT DEFAULT 'neutral',
            repair_needed   INTEGER DEFAULT 0,
            must_not        TEXT DEFAULT '[]',
            must_do         TEXT DEFAULT '[]',
            depth_required  TEXT DEFAULT 'standard',
            priority        INTEGER DEFAULT 5,
            created_at      REAL
        );

        CREATE TABLE IF NOT EXISTS lc_realizations (
            id              TEXT PRIMARY KEY,
            speech_act      TEXT NOT NULL,
            meaning_type    TEXT NOT NULL,
            stance          TEXT DEFAULT 'direct',
            input_content   TEXT NOT NULL,
            realized_form   TEXT NOT NULL,
            quality_score   REAL DEFAULT 0.8,
            created_at      REAL
        );

        CREATE TABLE IF NOT EXISTS lc_benchmark (
            id              TEXT PRIMARY KEY,
            query           TEXT NOT NULL,
            expected_speech_act TEXT NOT NULL,
            expected_intent TEXT,
            expected_no_capsule INTEGER DEFAULT 1,
            notes           TEXT,
            domain          TEXT DEFAULT 'general',
            difficulty      TEXT DEFAULT 'medium',
            created_at      REAL
        );

        CREATE TABLE IF NOT EXISTS lc_benchmark_results (
            id              TEXT PRIMARY KEY,
            benchmark_id    TEXT NOT NULL,
            run_at          REAL,
            speech_act_got  TEXT,
            intent_got      TEXT,
            no_capsule_pass INTEGER DEFAULT 0,
            naturalness     REAL DEFAULT 0.0,
            notes           TEXT,
            FOREIGN KEY(benchmark_id) REFERENCES lc_benchmark(id)
        );

        CREATE INDEX IF NOT EXISTS idx_lc_intent_pattern ON lc_intent_patterns(input_pattern);
        CREATE INDEX IF NOT EXISTS idx_lc_speech_act ON lc_speech_acts(act_name);
        CREATE INDEX IF NOT EXISTS idx_lc_bench_domain ON lc_benchmark(domain);
    """)
    conn.commit()
    conn.close()


# ── Seed helpers ──────────────────────────────────────────────────────────────

def upsert_concept(conn: sqlite3.Connection, name: str, definition: str,
                   domain: str = "pragmatics", example: str = "",
                   parent: str = "") -> None:
    import hashlib
    cid = "lc_c." + hashlib.md5(name.encode()).hexdigest()[:8]
    conn.execute("""
        INSERT INTO lc_concepts (id,name,domain,definition,example,parent,created_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET definition=excluded.definition,
            example=excluded.example, parent=excluded.parent
    """, (cid, name, domain, definition, example, parent, time.time()))


def upsert_speech_act(conn: sqlite3.Connection, act_name: str, description: str,
                      when_to_use: str = "", example_query: str = "",
                      example_response: str = "", operator_affinity: str = "") -> None:
    import hashlib
    sid = "lc_sa." + hashlib.md5(act_name.encode()).hexdigest()[:8]
    conn.execute("""
        INSERT INTO lc_speech_acts
            (id,act_name,description,when_to_use,example_query,example_response,operator_affinity,created_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET description=excluded.description,
            when_to_use=excluded.when_to_use, example_query=excluded.example_query,
            example_response=excluded.example_response
    """, (sid, act_name, description, when_to_use, example_query, example_response,
          operator_affinity, time.time()))


def upsert_intent_pattern(conn: sqlite3.Connection, input_pattern: str,
                          user_intent: str, speech_act: str,
                          emotional_tone: str = "neutral",
                          must_not: list = None, must_do: list = None,
                          required_depth: str = "standard",
                          confidence: float = 0.8) -> None:
    import hashlib, json
    pid = "lc_ip." + hashlib.md5((input_pattern + user_intent).encode()).hexdigest()[:8]
    conn.execute("""
        INSERT INTO lc_intent_patterns
            (id,input_pattern,user_intent,assistant_speech_act,emotional_tone,
             must_not,must_do,required_depth,confidence,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET assistant_speech_act=excluded.assistant_speech_act,
            confidence=excluded.confidence
    """, (pid, input_pattern, user_intent, speech_act, emotional_tone,
          json.dumps(must_not or []), json.dumps(must_do or []),
          required_depth, confidence, time.time()))


def upsert_benchmark(conn: sqlite3.Connection, query: str,
                     expected_speech_act: str, expected_intent: str = "",
                     notes: str = "", domain: str = "general",
                     difficulty: str = "medium") -> None:
    import hashlib
    bid = "lc_b." + hashlib.md5(query[:60].encode()).hexdigest()[:8]
    conn.execute("""
        INSERT INTO lc_benchmark
            (id,query,expected_speech_act,expected_intent,notes,domain,difficulty,created_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET expected_speech_act=excluded.expected_speech_act,
            expected_intent=excluded.expected_intent, notes=excluded.notes
    """, (bid, query, expected_speech_act, expected_intent, notes, domain, difficulty,
          time.time()))


# ── Convenience: init on import ───────────────────────────────────────────────

def ensure_db() -> None:
    if not _LC_DB_PATH.exists():
        init_db()


if __name__ == "__main__":
    init_db()
    print(f"language_cognition.db initialized at {_LC_DB_PATH}")
    conn = get_db()
    for table in ("lc_concepts", "lc_speech_acts", "lc_intent_patterns",
                  "lc_pragmatic_rules", "lc_realizations", "lc_benchmark"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {n} rows")
    conn.close()
