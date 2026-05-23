#!/usr/bin/env python3
"""
supermodel_db.py — Selyrion Recursive Parliament: sovereign fine-tuning substrate.

Separate from CMS (resonance_v11.db). Stores:
  - training_samples    : consensus-validated parliament reasoning → LoRA training pairs
  - model_generations   : LoRA checkpoint registry (Gen 0 → Gen N)
  - curriculum_tasks    : parliament-identified weaknesses → synthetic training objectives
  - contradiction_ledger: inter-model contradictions for hallucination tracking
  - breeding_artifacts  : reasoning pattern merges across model generations

Pipeline:
  parliament deliberation (GPU)
      → Stockfish adjudication (truth filter)
      → training_samples (validated reasoning pairs)
      → JSONL export
      → LoRA fine-tune (Unsloth)
      → model_generations (checkpoint registry)
      → next parliament round uses better models
      → repeat

Usage:
  python3 supermodel_db.py --init                  # create DB + schema
  python3 supermodel_db.py --harvest               # pull validated parliament rows → training_samples
  python3 supermodel_db.py --export                # write JSONL for LoRA training
  python3 supermodel_db.py --stats                 # show DB state
  python3 supermodel_db.py --curriculum            # generate curriculum tasks from weak domains
"""

import sqlite3
import json
import time
import argparse
import hashlib
from pathlib import Path
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────

SUPERMODEL_DB = Path.home() / "supermodel.db"
CMS_DB        = Path.home() / "resonance_v11.db"
SYNTH_DB      = Path.home() / "selyrion_synth.db"
EXPORT_DIR    = Path.home() / "projectbrain_dev" / "training_data"

parser = argparse.ArgumentParser(description="Selyrion Supermodel DB manager")
parser.add_argument("--db",        default=str(SUPERMODEL_DB))
parser.add_argument("--cms-db",    default=str(CMS_DB))
parser.add_argument("--synth-db",  default=str(SYNTH_DB))
parser.add_argument("--init",      action="store_true", help="Create DB and schema")
parser.add_argument("--harvest",   action="store_true", help="Harvest validated parliament rows")
parser.add_argument("--export",    action="store_true", help="Export JSONL for LoRA training")
parser.add_argument("--stats",     action="store_true", help="Show DB statistics")
parser.add_argument("--curriculum",action="store_true", help="Generate curriculum tasks")
parser.add_argument("--min-quality", type=float, default=0.75, help="Min quality score for harvest")
parser.add_argument("--domain",    default=None, help="Filter by domain")
parser.add_argument("--out",       default=None, help="Output path for JSONL export")
args = parser.parse_args()


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
-- Consensus-validated parliament reasoning traces → LoRA training pairs
CREATE TABLE IF NOT EXISTS training_samples (
    id              TEXT PRIMARY KEY,
    source          TEXT NOT NULL,          -- 'parliament_chess', 'curriculum', 'synthetic'
    domain          TEXT DEFAULT 'chess',
    prompt          TEXT NOT NULL,          -- full prompt given to parliament
    response        TEXT NOT NULL,          -- consensus reasoning (the validated output)
    quality_score   REAL DEFAULT 0.70,      -- 0-1: Stockfish adjudication + confidence
    base_model      TEXT,                   -- which model produced the lead response
    session_id      TEXT,
    game_id         TEXT,
    ply             INTEGER,
    adjudicated     INTEGER DEFAULT 0,      -- 1 = Stockfish confirmed correctness
    outcome         TEXT DEFAULT 'pending', -- 'validated', 'failed', 'pending'
    stockfish_eval  REAL,
    agrees_with_engine INTEGER DEFAULT 0,
    exported        INTEGER DEFAULT 0,      -- 1 = included in a JSONL export batch
    export_batch    TEXT,                   -- which export batch this was included in
    created_at      REAL
);

CREATE INDEX IF NOT EXISTS idx_ts_domain   ON training_samples(domain);
CREATE INDEX IF NOT EXISTS idx_ts_quality  ON training_samples(quality_score);
CREATE INDEX IF NOT EXISTS idx_ts_outcome  ON training_samples(outcome);
CREATE INDEX IF NOT EXISTS idx_ts_exported ON training_samples(exported);

-- LoRA checkpoint registry: Gen 0 (base) → Gen N (parliament-trained)
CREATE TABLE IF NOT EXISTS model_generations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    base_model              TEXT NOT NULL,      -- e.g. 'llama3.1:8b'
    lora_path               TEXT,               -- path to LoRA adapter weights
    generation              INTEGER DEFAULT 0,  -- 0=base, 1=first LoRA, 2=second, etc.
    domain                  TEXT DEFAULT 'chess',
    trained_on_count        INTEGER DEFAULT 0,  -- number of training samples used
    eval_engine_agreement   REAL,               -- % agreement with Stockfish post-train
    eval_hist_agreement     REAL,               -- % agreement with historical moves post-train
    eval_quality_delta      REAL,               -- quality score improvement vs prev generation
    training_config         TEXT,               -- JSON: epochs, lr, batch_size, etc.
    notes                   TEXT,
    created_at              REAL
);

-- Parliament-identified weak domains → synthetic training objectives
CREATE TABLE IF NOT EXISTS curriculum_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    domain          TEXT NOT NULL,
    weakness        TEXT NOT NULL,          -- what weakness was detected
    task_prompt     TEXT NOT NULL,          -- synthetic task to address the weakness
    expected_themes TEXT,                   -- JSON: expected reasoning themes
    difficulty      REAL DEFAULT 0.5,       -- 0-1
    status          TEXT DEFAULT 'pending', -- 'pending', 'assigned', 'completed', 'failed'
    assigned_to     TEXT,                   -- which model was tasked
    result          TEXT,                   -- parliament response
    quality_score   REAL,
    created_at      REAL,
    completed_at    REAL
);

-- Inter-model contradictions for hallucination tracking and resolution
CREATE TABLE IF NOT EXISTS contradiction_ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    game_id     TEXT,
    ply         INTEGER,
    domain      TEXT DEFAULT 'chess',
    model_a     TEXT NOT NULL,
    claim_a     TEXT NOT NULL,
    conf_a      REAL,
    model_b     TEXT NOT NULL,
    claim_b     TEXT NOT NULL,
    conf_b      REAL,
    topic       TEXT,                       -- what they disagreed about
    resolved    INTEGER DEFAULT 0,
    resolution  TEXT,                       -- how it was resolved (Stockfish, consensus, etc.)
    winner      TEXT,                       -- which model was right
    created_at  REAL
);

CREATE INDEX IF NOT EXISTS idx_cl_session ON contradiction_ledger(session_id);
CREATE INDEX IF NOT EXISTS idx_cl_model   ON contradiction_ledger(model_a, model_b);

-- Reasoning pattern merges across model generations (model breeding)
CREATE TABLE IF NOT EXISTS breeding_artifacts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_models       TEXT NOT NULL,      -- JSON array of parent model names
    output_model        TEXT,               -- resulting model identifier
    domain              TEXT DEFAULT 'chess',
    reasoning_patterns  TEXT,               -- JSON: patterns merged
    sample_count        INTEGER DEFAULT 0,  -- training samples used in breeding
    generation          INTEGER DEFAULT 1,
    notes               TEXT,
    created_at          REAL
);

-- Export batch registry
CREATE TABLE IF NOT EXISTS export_batches (
    id          TEXT PRIMARY KEY,           -- e.g. 'batch_20260523_chess_500'
    domain      TEXT,
    sample_count INTEGER,
    file_path   TEXT,
    format      TEXT DEFAULT 'jsonl',       -- 'jsonl', 'alpaca', 'sharegpt'
    used_for    TEXT,                       -- which model_generation trained on this
    created_at  REAL
);

-- Model psychometrics (imported/synced from parliament_move_deliberations)
CREATE TABLE IF NOT EXISTS model_psychometrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model           TEXT NOT NULL,
    domain          TEXT DEFAULT 'chess',
    total_positions INTEGER DEFAULT 0,
    engine_agreements INTEGER DEFAULT 0,
    hist_agreements INTEGER DEFAULT 0,
    avg_confidence  REAL DEFAULT 0.0,
    contradiction_rate REAL DEFAULT 0.0,
    last_updated    REAL,
    UNIQUE(model, domain)
);
"""


# ── DB init ───────────────────────────────────────────────────────────────────

def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    conn.commit()
    print(f"  Schema created: {args.db}")


# ── Harvester ─────────────────────────────────────────────────────────────────

HARVEST_PROMPT_TEMPLATE = """You are analyzing a chess position as a parliament member.

Position (FEN): {fen}
Stockfish evaluation: {sf_eval}

Chess knowledge context:
{cms_ctx}

Based on parliament deliberation across multiple models, provide:
- Your recommended move
- The key strategic reasoning
- Why this move is correct in this position
"""


def harvest(conn: sqlite3.Connection, cms_conn: sqlite3.Connection,
            min_quality: float = 0.75):
    """Pull validated consensus rows from parliament_move_deliberations → training_samples."""

    # Check parliament table exists
    tables = {r[0] for r in cms_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "parliament_move_deliberations" not in tables:
        print("  No parliament_move_deliberations table found in CMS DB.")
        return 0

    # Get consensus rows not yet harvested
    existing = {r[0] for r in conn.execute(
        "SELECT id FROM training_samples WHERE source='parliament_chess'").fetchall()}

    rows = cms_conn.execute("""
        SELECT p.id, p.session_id, p.ply, p.fen, p.conclusion, p.reasoning,
               p.confidence, p.key_insight, p.stockfish_eval,
               p.agrees_with_engine, p.outcome, p.model
        FROM parliament_move_deliberations p
        WHERE p.is_consensus = 1
          AND p.confidence >= ?
        ORDER BY p.rowid
    """, (min_quality,)).fetchall()

    added = 0
    skipped_existing = 0
    skipped_quality = 0

    for row in rows:
        sid = f"ts.chess.{row['id']}"
        if sid in existing:
            skipped_existing += 1
            continue

        # Quality score: blend confidence + engine agreement
        base_quality = float(row["confidence"] or 0.70)
        engine_bonus = 0.05 if row["agrees_with_engine"] else 0.0
        validated_bonus = 0.10 if row["outcome"] == "validated" else 0.0
        quality = min(1.0, base_quality + engine_bonus + validated_bonus)

        if quality < min_quality:
            skipped_quality += 1
            continue

        # Build prompt from FEN + eval
        fen = row["fen"] or ""
        sf_eval = row["stockfish_eval"]
        sf_str = f"{sf_eval:+.2f}" if sf_eval is not None else "unknown"

        prompt = HARVEST_PROMPT_TEMPLATE.format(
            fen=fen,
            sf_eval=sf_str,
            cms_ctx="(chess knowledge substrate)"
        )

        # Response: key_insight first, then full conclusion + reasoning
        key = row["key_insight"] or ""
        conclusion = row["conclusion"] or ""
        reasoning = row["reasoning"] or ""
        response = f"{key}\n\n{conclusion}"
        if reasoning and reasoning not in conclusion:
            response += f"\n\nReasoning: {reasoning[:500]}"

        conn.execute("""
            INSERT OR IGNORE INTO training_samples
                (id, source, domain, prompt, response, quality_score,
                 base_model, session_id, ply, adjudicated, outcome,
                 stockfish_eval, agrees_with_engine, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (sid, "parliament_chess", "chess",
              prompt, response, quality,
              row["model"], row["session_id"], row["ply"],
              1 if row["outcome"] == "validated" else 0,
              row["outcome"] or "pending",
              sf_eval,
              row["agrees_with_engine"] or 0,
              time.time()))
        added += 1

    conn.commit()
    print(f"  Harvested: {added} new training samples")
    print(f"  Skipped (existing): {skipped_existing}  |  Skipped (quality): {skipped_quality}")
    print(f"  Total training_samples: {conn.execute('SELECT COUNT(*) FROM training_samples').fetchone()[0]}")
    return added


# ── Psychometrics sync ─────────────────────────────────────────────────────────

def sync_psychometrics(conn: sqlite3.Connection, cms_conn: sqlite3.Connection):
    """Import per-model stats from parliament_move_deliberations."""
    tables = {r[0] for r in cms_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "parliament_move_deliberations" not in tables:
        return

    rows = cms_conn.execute("""
        SELECT model,
               COUNT(*) total,
               SUM(agrees_with_engine) eng,
               SUM(agrees_with_historical) hist,
               AVG(confidence) avg_conf
        FROM parliament_move_deliberations
        WHERE model NOT IN ('consensus','stockfish') AND is_consensus=0
        GROUP BY model
    """).fetchall()

    for r in rows:
        conn.execute("""
            INSERT INTO model_psychometrics
                (model, domain, total_positions, engine_agreements,
                 hist_agreements, avg_confidence, last_updated)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(model, domain) DO UPDATE SET
                total_positions   = excluded.total_positions,
                engine_agreements = excluded.engine_agreements,
                hist_agreements   = excluded.hist_agreements,
                avg_confidence    = excluded.avg_confidence,
                last_updated      = excluded.last_updated
        """, (r["model"], "chess", r["total"], r["eng"] or 0,
              r["hist"] or 0, r["avg_conf"] or 0.0, time.time()))
    conn.commit()


# ── JSONL export ──────────────────────────────────────────────────────────────

def export_jsonl(conn: sqlite3.Connection, domain: str = None,
                 out_path: str = None) -> Path:
    """Export unexported training samples to JSONL (Alpaca format for Unsloth)."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    domain_tag = domain or "all"
    batch_id = f"batch_{ts}_{domain_tag}"
    out_file = Path(out_path) if out_path else EXPORT_DIR / f"{batch_id}.jsonl"

    query = """
        SELECT id, prompt, response, quality_score, domain, base_model
        FROM training_samples
        WHERE exported = 0
    """
    params = []
    if domain:
        query += " AND domain = ?"
        params.append(domain)
    query += " ORDER BY quality_score DESC"

    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("  No unexported training samples found.")
        return None

    written = 0
    ids = []
    with open(out_file, "w") as f:
        for row in rows:
            # Alpaca format — works with Unsloth, axolotl, LLaMA-Factory
            sample = {
                "instruction": row["prompt"],
                "input": "",
                "output": row["response"],
                "quality": row["quality_score"],
                "domain": row["domain"],
                "model": row["base_model"] or "parliament_consensus",
            }
            f.write(json.dumps(sample) + "\n")
            written += 1
            ids.append(row["id"])

    # Mark as exported
    conn.executemany(
        "UPDATE training_samples SET exported=1, export_batch=? WHERE id=?",
        [(batch_id, sid) for sid in ids]
    )
    conn.execute("""
        INSERT INTO export_batches (id, domain, sample_count, file_path, created_at)
        VALUES (?,?,?,?,?)
    """, (batch_id, domain_tag, written, str(out_file), time.time()))
    conn.commit()

    print(f"  Exported: {written} samples → {out_file}")
    print(f"  Batch ID: {batch_id}")
    print(f"\n  LoRA training command (Unsloth):")
    print(f"  python3 unsloth_train.py --data {out_file} --model llama3.1:8b --output lora_gen1")
    return out_file


# ── Curriculum generation ─────────────────────────────────────────────────────

def generate_curriculum(conn: sqlite3.Connection, cms_conn: sqlite3.Connection):
    """Detect parliament weaknesses → generate targeted training tasks."""

    # Find positions where all models disagreed with engine (hard positions)
    tables = {r[0] for r in cms_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "parliament_move_deliberations" not in tables:
        print("  No parliament data yet.")
        return

    # Positions where consensus was wrong (0 engine agreement)
    hard = cms_conn.execute("""
        SELECT p.fen, p.stockfish_eval, p.conclusion, p.session_id, p.ply
        FROM parliament_move_deliberations p
        WHERE p.is_consensus = 1
          AND p.agrees_with_engine = 0
          AND p.confidence < 0.75
        ORDER BY p.rowid DESC LIMIT 50
    """).fetchall()

    added = 0
    for row in hard:
        task_id = hashlib.md5(f"{row['fen']}{row['ply']}".encode()).hexdigest()[:12]
        weakness = "parliament disagreed with engine on tactical position"
        task_prompt = (
            f"Analyze this chess position carefully.\n"
            f"FEN: {row['fen']}\n"
            f"Stockfish evaluation: {row['stockfish_eval']:+.2f}\n"
            f"Previous parliament consensus was: {(row['conclusion'] or '')[:200]}\n\n"
            f"The engine disagreed. What did the parliament miss? "
            f"Name the correct move and explain the tactical/strategic justification."
        )
        conn.execute("""
            INSERT OR IGNORE INTO curriculum_tasks
                (domain, weakness, task_prompt, difficulty, status, created_at)
            VALUES (?,?,?,?,?,?)
        """, ("chess", weakness, task_prompt,
              min(1.0, abs(float(row["stockfish_eval"] or 0)) / 3.0),
              "pending", time.time()))
        added += 1

    conn.commit()
    print(f"  Generated {added} curriculum tasks from {len(hard)} weak positions")
    print(f"  Total curriculum_tasks: {conn.execute('SELECT COUNT(*) FROM curriculum_tasks').fetchone()[0]}")


# ── Stats ─────────────────────────────────────────────────────────────────────

def show_stats(conn: sqlite3.Connection):
    print(f"\n  {'─'*56}")
    print(f"  Supermodel DB: {args.db}")
    print(f"  {'─'*56}")

    ts = conn.execute("SELECT COUNT(*), AVG(quality_score), SUM(exported) FROM training_samples").fetchone()
    print(f"\n  Training Samples : {ts[0]}  |  avg quality: {(ts[1] or 0):.3f}  |  exported: {ts[2] or 0}")

    domain_rows = conn.execute(
        "SELECT domain, COUNT(*) FROM training_samples GROUP BY domain").fetchall()
    for r in domain_rows:
        print(f"    {r[0]:20} {r[1]} samples")

    outcome_rows = conn.execute(
        "SELECT outcome, COUNT(*) FROM training_samples GROUP BY outcome").fetchall()
    print(f"\n  Outcomes:")
    for r in outcome_rows:
        print(f"    {r[0]:15} {r[1]}")

    gen_rows = conn.execute(
        "SELECT base_model, generation, trained_on_count, eval_engine_agreement FROM model_generations ORDER BY generation").fetchall()
    if gen_rows:
        print(f"\n  Model Generations:")
        for r in gen_rows:
            ea = f"{r[3]:.1%}" if r[3] else "—"
            print(f"    Gen {r[1]}: {r[0]:25} trained_on={r[2]}  engine_align={ea}")

    psych = conn.execute(
        "SELECT model, total_positions, engine_agreements, hist_agreements, avg_confidence FROM model_psychometrics ORDER BY engine_agreements DESC").fetchall()
    if psych:
        print(f"\n  Model Psychometrics:")
        for r in psych:
            ep = f"{r[2]/r[1]:.0%}" if r[1] else "—"
            hp = f"{r[3]/r[1]:.0%}" if r[1] else "—"
            print(f"    {r[0]:25} positions={r[1]}  eng={ep}  hist={hp}  conf={r[4]:.2f}")

    ct = conn.execute("SELECT COUNT(*) FROM curriculum_tasks WHERE status='pending'").fetchone()[0]
    ct_total = conn.execute("SELECT COUNT(*) FROM curriculum_tasks").fetchone()[0]
    print(f"\n  Curriculum Tasks : {ct_total} total  |  {ct} pending")

    cl = conn.execute("SELECT COUNT(*) FROM contradiction_ledger WHERE resolved=0").fetchone()[0]
    cl_total = conn.execute("SELECT COUNT(*) FROM contradiction_ledger").fetchone()[0]
    print(f"  Contradictions   : {cl_total} total  |  {cl} unresolved")

    eb = conn.execute("SELECT COUNT(*), SUM(sample_count) FROM export_batches").fetchone()
    print(f"  Export Batches   : {eb[0]}  |  {eb[1] or 0} total samples exported")
    print(f"  {'─'*56}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    conn = open_db(args.db)

    if args.init or not Path(args.db).exists():
        init_db(conn)

    # Always ensure schema is current
    init_db(conn)

    cms_conn = None
    if args.harvest or args.curriculum or not any([args.init, args.harvest, args.export, args.stats, args.curriculum]):
        if Path(args.cms_db).exists():
            cms_conn = open_db(args.cms_db)
        else:
            print(f"  CMS DB not found: {args.cms_db}")

    if args.harvest or not any([args.init, args.harvest, args.export, args.stats, args.curriculum]):
        if cms_conn:
            print(f"\n  Harvesting parliament_move_deliberations → training_samples...")
            harvest(conn, cms_conn, args.min_quality)
            sync_psychometrics(conn, cms_conn)

    if args.export:
        print(f"\n  Exporting JSONL training data...")
        export_jsonl(conn, args.domain, args.out)

    if args.curriculum:
        if cms_conn:
            print(f"\n  Generating curriculum tasks...")
            generate_curriculum(conn, cms_conn)

    if cms_conn:
        cms_conn.close()

    if args.stats or not any([args.init, args.harvest, args.export, args.stats, args.curriculum]):
        show_stats(conn)

    conn.close()


if __name__ == "__main__":
    main()
