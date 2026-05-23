#!/usr/bin/env python3
"""
cognitive_terrain.py — Map Selyrion parliament cognitive terrain.

Builds 8 terrain regions from parliament_move_deliberations + supermodel.db:

  1. calibration_defects     — high confidence + wrong outcome (overconfidence)
  2. reliable_zones          — high confidence + correct outcome
  3. weakness_domains        — failure clusters by opening / game phase / eval band
  4. uncertainty_topology    — positions where parliament consistently splits
  5. refinement_benefit      — debate-improved positions (requires Round 2 data)
  6. instability_zones       — debate-worsened positions (requires Round 2 data)
  7. psychometric_profiles   — per-model accuracy by phase, eval band, opening
  8. routing_rules           — derived trust weights: "trust phi4-mini when |eval|>2"

Writes terrain maps to supermodel.db (terrain_* tables).
Writes significant discoveries to claudecode.db (discoveries, invariants, failures).

Usage:
  python3 cognitive_terrain.py               # full map
  python3 cognitive_terrain.py --region 1    # single region
  python3 cognitive_terrain.py --stats       # show current terrain state
  python3 cognitive_terrain.py --watch       # rerun every N minutes (ongoing batch)
"""

import sqlite3
import json
import time
import argparse
import hashlib
from pathlib import Path
from datetime import datetime

CMS_DB        = Path.home() / "resonance_v11.db"
SUPERMODEL_DB = Path.home() / "supermodel.db"
CLAUDECODE_DB = Path.home() / "claudecode.db"
SYNTH_DB      = Path.home() / "selyrion_synth.db"

parser = argparse.ArgumentParser()
parser.add_argument("--db",            default=str(CMS_DB))
parser.add_argument("--supermodel-db", default=str(SUPERMODEL_DB))
parser.add_argument("--claudecode-db", default=str(CLAUDECODE_DB))
parser.add_argument("--synth-db",      default=str(SYNTH_DB))
parser.add_argument("--region",        type=int, default=None, help="Run single region (1-8)")
parser.add_argument("--stats",         action="store_true")
parser.add_argument("--watch",         type=int, default=0, metavar="MINUTES",
                    help="Re-run every N minutes (0 = once)")
parser.add_argument("--min-samples",   type=int, default=5,
                    help="Min samples to derive a routing rule (default 5)")
args = parser.parse_args()

BOLD = "\033[1m"; DIM = "\033[2m"; R = "\033[0m"
OK = "\033[32m"; WARN = "\033[33m"; ERR = "\033[31m"

TERRAIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS terrain_calibration_defects (
    id              TEXT PRIMARY KEY,
    model           TEXT,
    fen             TEXT,
    session_id      TEXT,
    ply             INTEGER,
    confidence      REAL,
    stockfish_eval  REAL,
    outcome         TEXT,
    game_phase      TEXT,
    eval_band       TEXT,
    overconfidence  REAL,   -- confidence - expected_accuracy_for_band
    created_at      REAL
);

CREATE TABLE IF NOT EXISTS terrain_reliable_zones (
    id              TEXT PRIMARY KEY,
    game_phase      TEXT,
    eval_band       TEXT,
    opening_family  TEXT,
    model           TEXT,
    sample_count    INTEGER,
    accuracy_rate   REAL,
    avg_confidence  REAL,
    calibration_gap REAL,   -- accuracy_rate - avg_confidence (positive = underconfident)
    updated_at      REAL,
    UNIQUE(game_phase, eval_band, opening_family, model)
);

CREATE TABLE IF NOT EXISTS terrain_weakness_domains (
    id              TEXT PRIMARY KEY,
    model           TEXT,
    domain_type     TEXT,   -- 'game_phase', 'eval_band', 'opening'
    domain_value    TEXT,
    total           INTEGER,
    failures        INTEGER,
    failure_rate    REAL,
    avg_confidence  REAL,   -- avg confidence when failing (calibration)
    sample_count    INTEGER,
    updated_at      REAL,
    UNIQUE(model, domain_type, domain_value)
);

CREATE TABLE IF NOT EXISTS terrain_uncertainty_topology (
    id              TEXT PRIMARY KEY,
    session_id      TEXT,
    ply             INTEGER,
    fen             TEXT,
    game_phase      TEXT,
    eval_band       TEXT,
    split_type      TEXT,   -- 'engine_split', 'hist_split', 'both'
    models_agree    INTEGER,
    models_disagree INTEGER,
    stockfish_eval  REAL,
    truth_outcome   TEXT,
    created_at      REAL
);

CREATE TABLE IF NOT EXISTS terrain_psychometric_profiles (
    id              TEXT PRIMARY KEY,
    model           TEXT NOT NULL,
    game_phase      TEXT NOT NULL,   -- 'opening'(ply<20), 'middlegame'(20-40), 'endgame'(40+)
    eval_band       TEXT NOT NULL,   -- 'winning'(>2), 'advantage'(0.5-2), 'equal'(<0.5), 'losing'(<-0.5)
    total           INTEGER DEFAULT 0,
    engine_correct  INTEGER DEFAULT 0,
    hist_correct    INTEGER DEFAULT 0,
    avg_confidence  REAL DEFAULT 0.0,
    accuracy_rate   REAL DEFAULT 0.0,
    updated_at      REAL,
    UNIQUE(model, game_phase, eval_band)
);

CREATE TABLE IF NOT EXISTS terrain_routing_rules (
    id              TEXT PRIMARY KEY,
    condition_type  TEXT NOT NULL,   -- 'eval_band', 'game_phase', 'opening'
    condition_value TEXT NOT NULL,
    preferred_model TEXT NOT NULL,
    trust_weight    REAL NOT NULL,
    evidence_count  INTEGER DEFAULT 0,
    accuracy_rate   REAL,
    notes           TEXT,
    updated_at      REAL,
    UNIQUE(condition_type, condition_value, preferred_model)
);
"""


# ── DB helpers ────────────────────────────────────────────────────────────────

def open_db(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def game_phase(ply: int) -> str:
    if ply < 20:  return "opening"
    if ply < 40:  return "middlegame"
    return "endgame"


def eval_band(sf_eval) -> str:
    if sf_eval is None: return "unknown"
    e = float(sf_eval)
    if e >  2.0: return "winning"
    if e >  0.5: return "slight_advantage"
    if e > -0.5: return "equal"
    if e > -2.0: return "slight_disadvantage"
    return "losing"


def opening_family(opening: str) -> str:
    if not opening: return "unknown"
    o = opening.lower()
    for fam in ["sicilian", "ruy lopez", "french", "caro-kann", "queens gambit",
                "kings indian", "nimzo", "english", "italian", "slav",
                "grunfeld", "dutch", "pirc", "alekhine"]:
        if fam in o: return fam
    return "other"


def make_id(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


# ── claudecode.db writers ─────────────────────────────────────────────────────

def write_discovery(cc_conn: sqlite3.Connection, body: str,
                    tags: str = "terrain", importance: int = 2):
    did = make_id(body, time.time())
    cc_conn.execute("""
        INSERT OR IGNORE INTO discoveries (id, session_id, body, tags, importance, created_at)
        VALUES (?,?,?,?,?,?)
    """, (did, "cognitive_terrain", body, tags, importance, time.time()))
    cc_conn.commit()


def write_invariant(cc_conn: sqlite3.Connection, body: str, domain: str = "chess"):
    iid = make_id(body)
    cc_conn.execute("""
        INSERT OR IGNORE INTO invariants (id, body, domain, created_at, reaffirmed_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(body) DO UPDATE SET reaffirmed_at=excluded.reaffirmed_at
    """, (iid, body, domain, time.time(), time.time()))
    cc_conn.commit()


def write_failure(cc_conn: sqlite3.Connection, body: str, tags: str = "parliament"):
    fid = make_id(body, time.time())
    cc_conn.execute("""
        INSERT OR IGNORE INTO failures (id, body, tags, created_at) VALUES (?,?,?,?)
    """, (fid, body, tags, time.time()))
    cc_conn.commit()


# ── Region 1: Calibration defects ─────────────────────────────────────────────

def map_calibration_defects(cms_conn, sm_conn, cc_conn):
    print(f"\n  {BOLD}Region 1 — Calibration Defects{R}")

    rows = cms_conn.execute("""
        SELECT p.id, p.session_id, p.ply, p.fen, p.confidence,
               p.stockfish_eval, p.outcome, p.model
        FROM parliament_move_deliberations p
        WHERE p.outcome IN ('failed')
          AND p.confidence >= 0.80
          AND p.is_consensus = 1
    """).fetchall()

    added = 0
    worst = []
    for r in rows:
        rid = make_id(r["id"])
        phase = game_phase(r["ply"] or 0)
        band  = eval_band(r["stockfish_eval"])
        # Overconfidence: how much more confident than the failure warranted
        overconf = float(r["confidence"]) - 0.50
        sm_conn.execute("""
            INSERT OR REPLACE INTO terrain_calibration_defects
                (id, model, fen, session_id, ply, confidence, stockfish_eval,
                 outcome, game_phase, eval_band, overconfidence, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (rid, r["model"], r["fen"], r["session_id"], r["ply"],
              float(r["confidence"]), r["stockfish_eval"],
              r["outcome"], phase, band, overconf, time.time()))
        added += 1
        worst.append((overconf, float(r["confidence"]), phase, band))

    sm_conn.commit()
    print(f"    Defects mapped: {added}")

    if worst:
        worst.sort(reverse=True)
        top = worst[0]
        msg = (f"Parliament calibration defect: {added} high-confidence failures. "
               f"Worst: conf={top[1]:.2f} in {top[2]}/{top[3]} "
               f"(overconfidence={top[0]:.2f})")
        write_discovery(cc_conn, msg, tags="calibration,chess", importance=3)
        print(f"    {WARN}Worst: conf={top[1]:.2f} in {top[2]}/{top[3]}{R}")

    return added


# ── Region 2: Reliable zones ──────────────────────────────────────────────────

def map_reliable_zones(cms_conn, sm_conn, cc_conn):
    print(f"\n  {BOLD}Region 2 — Reliable Cognition Zones{R}")

    rows = cms_conn.execute("""
        SELECT p.model, p.ply, p.stockfish_eval, p.confidence,
               p.outcome, p.agrees_with_engine
        FROM parliament_move_deliberations p
        WHERE p.outcome IN ('validated','failed')
          AND p.is_consensus = 1
    """).fetchall()

    # Aggregate by (model, game_phase, eval_band)
    buckets = {}
    for r in rows:
        phase = game_phase(r["ply"] or 0)
        band  = eval_band(r["stockfish_eval"])
        key   = (r["model"], phase, band)
        if key not in buckets:
            buckets[key] = {"total": 0, "correct": 0, "conf_sum": 0.0}
        buckets[key]["total"] += 1
        if r["outcome"] == "validated":
            buckets[key]["correct"] += 1
        buckets[key]["conf_sum"] += float(r["confidence"] or 0.5)

    added = 0
    best_zones = []
    for (model, phase, band), b in buckets.items():
        if b["total"] < 2:
            continue
        acc  = b["correct"] / b["total"]
        conf = b["conf_sum"] / b["total"]
        gap  = acc - conf  # positive = underconfident, negative = overconfident
        rid  = make_id(model, phase, band)
        sm_conn.execute("""
            INSERT OR REPLACE INTO terrain_reliable_zones
                (id, game_phase, eval_band, opening_family, model,
                 sample_count, accuracy_rate, avg_confidence, calibration_gap, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (rid, phase, band, "all", model,
              b["total"], acc, conf, gap, time.time()))
        added += 1
        if acc >= 0.70:
            best_zones.append((acc, model, phase, band, b["total"]))

    sm_conn.commit()
    best_zones.sort(reverse=True)
    print(f"    Zone buckets: {added}")
    for acc, model, phase, band, n in best_zones[:3]:
        print(f"    {OK}Reliable:{R} {model} in {phase}/{band}  acc={acc:.0%} (n={n})")
        write_invariant(cc_conn,
            f"{model} reliable in {phase}/{band}: {acc:.0%} accuracy (n={n})",
            domain="chess_terrain")

    return added


# ── Region 3: Weakness domains ────────────────────────────────────────────────

def map_weakness_domains(cms_conn, sm_conn, cc_conn):
    print(f"\n  {BOLD}Region 3 — Weakness Domains{R}")

    rows = cms_conn.execute("""
        SELECT p.model, p.ply, p.stockfish_eval, p.confidence, p.outcome
        FROM parliament_move_deliberations p
        WHERE p.outcome IN ('validated','failed') AND p.is_consensus=0
    """).fetchall()

    buckets = {}
    for r in rows:
        phase = game_phase(r["ply"] or 0)
        band  = eval_band(r["stockfish_eval"])
        for dtype, dval in [("game_phase", phase), ("eval_band", band)]:
            key = (r["model"], dtype, dval)
            if key not in buckets:
                buckets[key] = {"total": 0, "fail": 0, "conf": 0.0}
            buckets[key]["total"] += 1
            if r["outcome"] == "failed":
                buckets[key]["fail"] += 1
            buckets[key]["conf"] += float(r["confidence"] or 0.5)

    added = 0
    worst_weaknesses = []
    for (model, dtype, dval), b in buckets.items():
        if b["total"] < 3:
            continue
        frate = b["fail"] / b["total"]
        conf  = b["conf"] / b["total"]
        rid   = make_id(model, dtype, dval)
        sm_conn.execute("""
            INSERT OR REPLACE INTO terrain_weakness_domains
                (id, model, domain_type, domain_value, total, failures,
                 failure_rate, avg_confidence, sample_count, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (rid, model, dtype, dval,
              b["total"], b["fail"], frate, conf, b["total"], time.time()))
        added += 1
        if frate >= 0.60:
            worst_weaknesses.append((frate, model, dtype, dval, b["total"]))

    sm_conn.commit()
    worst_weaknesses.sort(reverse=True)
    print(f"    Weakness buckets: {added}")
    for frate, model, dtype, dval, n in worst_weaknesses[:5]:
        print(f"    {ERR}Weak:{R} {model} in {dval}  failure={frate:.0%} (n={n})")
        write_failure(cc_conn,
            f"{model} weak in {dtype}={dval}: {frate:.0%} failure rate (n={n})",
            tags="weakness,chess")

    return added


# ── Region 4: Uncertainty topology ───────────────────────────────────────────

def map_uncertainty_topology(cms_conn, sm_conn, cc_conn):
    print(f"\n  {BOLD}Region 4 — Uncertainty Topology{R}")

    # Find consensus positions and their per-model agreement patterns
    sessions = cms_conn.execute("""
        SELECT DISTINCT session_id FROM parliament_move_deliberations
        WHERE is_consensus=1
    """).fetchall()

    added = 0
    for sess_row in sessions:
        sid = sess_row["session_id"]
        plies = cms_conn.execute("""
            SELECT DISTINCT ply FROM parliament_move_deliberations
            WHERE session_id=? AND is_consensus=0 AND model != 'stockfish'
        """, (sid,)).fetchall()

        for ply_row in plies:
            ply = ply_row["ply"]
            models = cms_conn.execute("""
                SELECT model, agrees_with_engine, agrees_with_historical,
                       confidence, stockfish_eval, fen
                FROM parliament_move_deliberations
                WHERE session_id=? AND ply=? AND is_consensus=0 AND model!='stockfish'
            """, (sid, ply)).fetchall()

            if len(models) < 2:
                continue

            eng_vals  = [r["agrees_with_engine"] for r in models if r["agrees_with_engine"] is not None]
            hist_vals = [r["agrees_with_historical"] for r in models if r["agrees_with_historical"] is not None]

            eng_split  = len(set(eng_vals)) > 1 if eng_vals else False
            hist_split = len(set(hist_vals)) > 1 if hist_vals else False

            if not eng_split and not hist_split:
                continue

            split_type = "both" if (eng_split and hist_split) else \
                         "engine_split" if eng_split else "hist_split"

            r0 = models[0]
            phase = game_phase(ply)
            band  = eval_band(r0["stockfish_eval"])

            consensus = cms_conn.execute("""
                SELECT outcome FROM parliament_move_deliberations
                WHERE session_id=? AND ply=? AND is_consensus=1
            """, (sid, ply)).fetchone()
            truth = consensus["outcome"] if consensus else "pending"

            rid = make_id(sid, ply)
            sm_conn.execute("""
                INSERT OR REPLACE INTO terrain_uncertainty_topology
                    (id, session_id, ply, fen, game_phase, eval_band,
                     split_type, models_agree, models_disagree,
                     stockfish_eval, truth_outcome, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (rid, sid, ply, r0["fen"], phase, band, split_type,
                  len(eng_vals) - sum(1 for v in eng_vals if v != eng_vals[0]),
                  sum(1 for v in eng_vals if v != eng_vals[0]),
                  r0["stockfish_eval"], truth, time.time()))
            added += 1

    sm_conn.commit()
    print(f"    Uncertain positions: {added}")

    by_phase = sm_conn.execute("""
        SELECT game_phase, split_type, COUNT(*) c
        FROM terrain_uncertainty_topology GROUP BY game_phase, split_type
    """).fetchall()
    for r in by_phase:
        print(f"    {WARN}{r['game_phase']}/{r['split_type']}{R}: {r['c']} positions")

    if added > 0:
        write_discovery(cc_conn,
            f"Uncertainty topology: {added} split-opinion positions mapped. "
            f"These are the highest-value curriculum targets.",
            tags="uncertainty,terrain", importance=3)
    return added


# ── Region 7: Psychometric profiles ──────────────────────────────────────────

def map_psychometric_profiles(cms_conn, sm_conn, cc_conn):
    print(f"\n  {BOLD}Region 7 — Psychometric Profiles{R}")

    rows = cms_conn.execute("""
        SELECT model, ply, stockfish_eval, agrees_with_engine,
               agrees_with_historical, confidence, outcome
        FROM parliament_move_deliberations
        WHERE is_consensus=0 AND model NOT IN ('stockfish','consensus')
          AND outcome IN ('validated','failed')
    """).fetchall()

    buckets = {}
    for r in rows:
        phase = game_phase(r["ply"] or 0)
        band  = eval_band(r["stockfish_eval"])
        key   = (r["model"], phase, band)
        if key not in buckets:
            buckets[key] = {"total": 0, "eng": 0, "hist": 0, "conf": 0.0, "correct": 0}
        b = buckets[key]
        b["total"] += 1
        if r["agrees_with_engine"]:  b["eng"] += 1
        if r["agrees_with_historical"]: b["hist"] += 1
        b["conf"] += float(r["confidence"] or 0.5)
        if r["outcome"] == "validated": b["correct"] += 1

    added = 0
    profiles = []
    for (model, phase, band), b in buckets.items():
        if b["total"] < 2:
            continue
        acc  = b["correct"] / b["total"]
        conf = b["conf"] / b["total"]
        rid  = make_id(model, phase, band)
        sm_conn.execute("""
            INSERT OR REPLACE INTO terrain_psychometric_profiles
                (id, model, game_phase, eval_band, total,
                 engine_correct, hist_correct, avg_confidence, accuracy_rate, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (rid, model, phase, band,
              b["total"], b["eng"], b["hist"],
              conf, acc, time.time()))
        added += 1
        profiles.append((model, phase, band, acc, b["eng"]/b["total"], b["total"]))

    sm_conn.commit()
    profiles.sort(key=lambda x: -x[3])
    print(f"    Profile buckets: {added}")
    for model, phase, band, acc, eng, n in profiles[:6]:
        marker = OK if acc >= 0.6 else (WARN if acc >= 0.4 else ERR)
        print(f"    {marker}{model}{R} {phase}/{band}: acc={acc:.0%} eng={eng:.0%} (n={n})")

    return added


# ── Region 8: Routing rules ───────────────────────────────────────────────────

def derive_routing_rules(sm_conn, cc_conn, min_samples: int = 5):
    print(f"\n  {BOLD}Region 8 — Routing Rules{R}")

    profiles = sm_conn.execute("""
        SELECT model, game_phase, eval_band, total, accuracy_rate, engine_correct
        FROM terrain_psychometric_profiles
        WHERE total >= ?
        ORDER BY accuracy_rate DESC
    """, (min_samples,)).fetchall()

    # For each (game_phase, eval_band) combo find the best model
    best = {}
    for r in profiles:
        for ctype, cval in [("game_phase", r["game_phase"]),
                             ("eval_band",  r["eval_band"])]:
            key = (ctype, cval)
            if key not in best or r["accuracy_rate"] > best[key]["acc"]:
                best[key] = {
                    "model": r["model"], "acc": r["accuracy_rate"],
                    "n": r["total"]
                }

    added = 0
    for (ctype, cval), b in best.items():
        if b["n"] < min_samples:
            continue
        rid = make_id(ctype, cval, b["model"])
        note = f"Best accuracy {b['acc']:.0%} from {b['n']} samples"
        sm_conn.execute("""
            INSERT OR REPLACE INTO terrain_routing_rules
                (id, condition_type, condition_value, preferred_model,
                 trust_weight, evidence_count, accuracy_rate, notes, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (rid, ctype, cval, b["model"],
              b["acc"], b["n"], b["acc"], note, time.time()))
        added += 1
        print(f"    {OK}Route:{R} {ctype}={cval} → {b['model']} "
              f"(acc={b['acc']:.0%}, n={b['n']})")
        write_invariant(cc_conn,
            f"Route {ctype}={cval} to {b['model']}: highest accuracy at {b['acc']:.0%}",
            domain="routing")

    sm_conn.commit()
    print(f"    Routing rules derived: {added}")
    return added


# ── Stats ─────────────────────────────────────────────────────────────────────

def show_stats(sm_conn):
    print(f"\n  {BOLD}Cognitive Terrain State{R}")
    print(f"  {'─'*56}")
    tables = [
        ("terrain_calibration_defects", "Calibration defects"),
        ("terrain_reliable_zones",      "Reliable zones"),
        ("terrain_weakness_domains",    "Weakness domains"),
        ("terrain_uncertainty_topology","Uncertainty positions"),
        ("terrain_psychometric_profiles","Psychometric profiles"),
        ("terrain_routing_rules",       "Routing rules"),
    ]
    for tbl, label in tables:
        try:
            n = sm_conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            print(f"  {label:30} {n}")
        except Exception:
            print(f"  {label:30} (table missing)")

    rules = sm_conn.execute("""
        SELECT condition_type, condition_value, preferred_model, accuracy_rate, evidence_count
        FROM terrain_routing_rules ORDER BY accuracy_rate DESC
    """).fetchall()
    if rules:
        print(f"\n  Active routing rules:")
        for r in rules:
            print(f"    {r['condition_type']}={r['condition_value']:15} "
                  f"→ {r['preferred_model']:20} acc={r['accuracy_rate']:.0%} (n={r['evidence_count']})")
    print(f"  {'─'*56}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_all():
    cms_conn = open_db(args.db)
    sm_conn  = open_db(args.supermodel_db)
    cc_conn  = open_db(args.claudecode_db)

    sm_conn.executescript(TERRAIN_SCHEMA)
    sm_conn.commit()

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n  {BOLD}Selyrion Cognitive Terrain Map{R}  {DIM}{ts}{R}")

    region = args.region
    if region is None or region == 1: map_calibration_defects(cms_conn, sm_conn, cc_conn)
    if region is None or region == 2: map_reliable_zones(cms_conn, sm_conn, cc_conn)
    if region is None or region == 3: map_weakness_domains(cms_conn, sm_conn, cc_conn)
    if region is None or region == 4: map_uncertainty_topology(cms_conn, sm_conn, cc_conn)
    if region is None or region == 7: map_psychometric_profiles(cms_conn, sm_conn, cc_conn)
    if region is None or region == 8: derive_routing_rules(sm_conn, cc_conn, args.min_samples)

    show_stats(sm_conn)

    # Write session summary to claudecode.db
    rules = sm_conn.execute(
        "SELECT COUNT(*) FROM terrain_routing_rules"
    ).fetchone()[0]
    defects = sm_conn.execute(
        "SELECT COUNT(*) FROM terrain_calibration_defects"
    ).fetchone()[0]
    write_discovery(cc_conn,
        f"Cognitive terrain mapped: {defects} calibration defects, "
        f"{rules} routing rules derived. Regions 1-4,7,8 active.",
        tags="terrain,session", importance=2)

    cms_conn.close(); sm_conn.close(); cc_conn.close()


def main():
    if args.stats:
        sm_conn = open_db(args.supermodel_db)
        sm_conn.executescript(TERRAIN_SCHEMA)
        show_stats(sm_conn)
        sm_conn.close()
        return

    if args.watch:
        print(f"  Watching — rerunning every {args.watch} minutes. Ctrl-C to stop.")
        while True:
            run_all()
            time.sleep(args.watch * 60)
    else:
        run_all()


if __name__ == "__main__":
    main()
