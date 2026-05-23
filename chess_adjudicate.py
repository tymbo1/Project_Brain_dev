#!/usr/bin/env python3
"""
chess_adjudicate.py — Post-game Stockfish adjudication for parliament reasoning.

Flips parliament_move_deliberations.outcome from 'pending' to:
  'validated' — parliament reasoning was correct (engine + game outcome agree)
  'failed'    — parliament reasoning was wrong
  'neutral'   — position too equal to adjudicate (|eval| < threshold)
  'draw'      — game ended in draw, weak signal

Also updates supermodel.db training_samples with adjudicated=1 + quality boost.

Truth filter logic:
  For each consensus row at ply N:
    - Get Stockfish eval at that position (stored in stockfish_eval)
    - Get game result from chess_games (via session → replay_sessions → game_id)
    - Determine if eval-predicted winner matches actual winner
    - If parliament agreed with engine:
        engine correct  → validated (quality boost)
        engine wrong    → failed   (penalise)
    - If parliament disagreed with engine:
        engine correct  → failed   (parliament was wrong)
        engine wrong    → validated (parliament had human intuition right — rare, valuable)

Eval strength bands:
  |eval| > 2.0  → decisive, high-confidence adjudication
  |eval| > 0.5  → clear advantage, medium-confidence
  |eval| ≤ 0.5  → neutral/equal → outcome = 'neutral'

Usage:
  python3 chess_adjudicate.py                        # adjudicate all pending
  python3 chess_adjudicate.py --session replay.xxx   # single session
  python3 chess_adjudicate.py --dry-run              # show what would change
  python3 chess_adjudicate.py --stats                # show adjudication state
"""

import sqlite3
import argparse
import time
from pathlib import Path

CMS_DB        = Path.home() / "resonance_v11.db"
SYNTH_DB      = Path.home() / "selyrion_synth.db"
SUPERMODEL_DB = Path.home() / "supermodel.db"

parser = argparse.ArgumentParser()
parser.add_argument("--db",           default=str(CMS_DB))
parser.add_argument("--synth-db",     default=str(SYNTH_DB))
parser.add_argument("--supermodel-db",default=str(SUPERMODEL_DB))
parser.add_argument("--session",      default=None, help="Adjudicate single session_id")
parser.add_argument("--dry-run",      action="store_true")
parser.add_argument("--stats",        action="store_true")
parser.add_argument("--neutral-threshold", type=float, default=0.5,
                    help="Eval below this = neutral (default 0.5)")
parser.add_argument("--min-eval",     type=float, default=0.3,
                    help="Minimum |eval| to attempt adjudication (default 0.3)")
args = parser.parse_args()

# ── ANSI ──────────────────────────────────────────────────────────────────────
OK   = "\033[32m"
WARN = "\033[33m"
ERR  = "\033[31m"
DIM  = "\033[2m"
BOLD = "\033[1m"
R    = "\033[0m"


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def result_winner(result: str) -> str:
    """Return 'white', 'black', 'draw', or None."""
    if result == "1-0":   return "white"
    if result == "0-1":   return "black"
    if result == "1/2-1/2": return "draw"
    return None


def eval_favors(stockfish_eval: float, threshold: float) -> str:
    """Return 'white', 'black', or 'neutral' based on eval."""
    if stockfish_eval is None:
        return "neutral"
    if stockfish_eval > threshold:
        return "white"
    if stockfish_eval < -threshold:
        return "black"
    return "neutral"


def adjudicate_row(row, game_result: str, neutral_threshold: float,
                   min_eval: float) -> tuple[str, float]:
    """
    Returns (outcome, quality_delta).
    outcome: 'validated', 'failed', 'neutral', 'draw'
    quality_delta: adjustment to apply to training_samples.quality_score
    """
    sf_eval   = row["stockfish_eval"]
    eng_agree = row["agrees_with_engine"]
    winner    = result_winner(game_result)

    if winner == "draw":
        return "draw", 0.0

    if winner is None:
        return "pending", 0.0

    if sf_eval is None or abs(sf_eval) < min_eval:
        return "neutral", 0.0

    favored = eval_favors(sf_eval, neutral_threshold)
    if favored == "neutral":
        return "neutral", 0.0

    # Did the engine-favored side actually win?
    engine_was_right = (favored == winner)

    # Confidence band for quality delta
    abs_eval = abs(sf_eval)
    if abs_eval > 2.0:
        conf = 1.0    # decisive
    elif abs_eval > 1.0:
        conf = 0.75   # clear advantage
    else:
        conf = 0.50   # slight advantage

    if eng_agree:
        # Parliament agreed with engine
        if engine_was_right:
            return "validated", +0.05 * conf
        else:
            # Engine was wrong, but parliament trusted it — mild failure
            return "failed", -0.03 * conf
    else:
        # Parliament disagreed with engine
        if engine_was_right:
            # Parliament was wrong — penalise
            return "failed", -0.05 * conf
        else:
            # Parliament had human intuition — engine was wrong, parliament right
            # Rare and very valuable
            return "validated", +0.10 * conf


def build_session_map(synth_conn: sqlite3.Connection) -> dict:
    """Map session_id → (game_id, white, black, result_placeholder)."""
    rows = synth_conn.execute(
        "SELECT id, game_id, white, black FROM replay_sessions"
    ).fetchall()
    return {r["id"]: {"game_id": r["game_id"], "white": r["white"], "black": r["black"]}
            for r in rows}


def load_game_results(cms_conn: sqlite3.Connection) -> dict:
    """Map game_id → result string."""
    rows = cms_conn.execute(
        "SELECT id, result FROM chess_games WHERE result IS NOT NULL"
    ).fetchall()
    return {r["id"]: r["result"] for r in rows}


def show_stats(cms_conn: sqlite3.Connection, sm_conn: sqlite3.Connection | None):
    print(f"\n  {BOLD}Parliament adjudication state{R}")
    print(f"  {'─'*52}")

    rows = cms_conn.execute("""
        SELECT outcome, COUNT(*) c, AVG(confidence) avg_conf,
               AVG(stockfish_eval) avg_eval
        FROM parliament_move_deliberations
        WHERE is_consensus=1
        GROUP BY outcome ORDER BY c DESC
    """).fetchall()
    for r in rows:
        print(f"  {r['outcome']:12} {r['c']:5} rows  "
              f"avg_conf={r['avg_conf']:.2f}  avg_eval={r['avg_eval'] or 0:+.2f}")

    total = cms_conn.execute(
        "SELECT COUNT(*) FROM parliament_move_deliberations WHERE is_consensus=1"
    ).fetchone()[0]
    pending = cms_conn.execute(
        "SELECT COUNT(*) FROM parliament_move_deliberations WHERE is_consensus=1 AND outcome='pending'"
    ).fetchone()[0]
    print(f"\n  Total consensus rows : {total}")
    print(f"  Still pending        : {pending}")

    if sm_conn:
        ts = sm_conn.execute(
            "SELECT outcome, COUNT(*) FROM training_samples GROUP BY outcome"
        ).fetchall()
        print(f"\n  Training samples:")
        for r in ts:
            print(f"    {r[0]:12} {r[1]}")

    print(f"  {'─'*52}\n")


def main():
    cms_conn = open_db(args.db)
    sm_conn  = open_db(args.supermodel_db) if Path(args.supermodel_db).exists() else None

    if args.stats:
        show_stats(cms_conn, sm_conn)
        return

    if not Path(args.synth_db).exists():
        print(f"{ERR}Synth DB not found: {args.synth_db}{R}")
        return

    synth_conn = open_db(args.synth_db)

    session_map  = build_session_map(synth_conn)
    game_results = load_game_results(cms_conn)

    print(f"\n  {BOLD}Chess Parliament Adjudication{R}")
    print(f"  Sessions mapped : {len(session_map)}")
    print(f"  Games with result: {len(game_results)}")
    print(f"  Neutral threshold: ±{args.neutral_threshold}")
    print(f"  {'─'*52}")

    # Load pending consensus rows
    query = """
        SELECT id, session_id, ply, fen, stockfish_eval,
               agrees_with_engine, confidence, outcome
        FROM parliament_move_deliberations
        WHERE is_consensus=1 AND outcome='pending'
    """
    params = []
    if args.session:
        query += " AND session_id=?"
        params.append(args.session)

    rows = cms_conn.execute(query, params).fetchall()
    print(f"  Pending rows    : {len(rows)}\n")

    counts = {"validated": 0, "failed": 0, "neutral": 0,
              "draw": 0, "no_session": 0, "no_result": 0}
    updates = []  # (outcome, quality_delta, row_id)

    for row in rows:
        sess = session_map.get(row["session_id"])
        if not sess:
            counts["no_session"] += 1
            continue

        game_id = sess["game_id"]
        result  = game_results.get(game_id)
        if not result or result == "*":
            counts["no_result"] += 1
            continue

        outcome, qdelta = adjudicate_row(
            row, result,
            args.neutral_threshold,
            args.min_eval
        )
        counts[outcome] = counts.get(outcome, 0) + 1
        updates.append((outcome, qdelta, row["id"]))

    # Report
    total_adj = sum(v for k, v in counts.items()
                    if k not in ("no_session", "no_result"))
    val_rate = counts["validated"] / total_adj if total_adj else 0

    print(f"  Results:")
    print(f"    {OK}validated{R}  : {counts['validated']}  ({val_rate:.0%} of adjudicated)")
    print(f"    {ERR}failed{R}     : {counts['failed']}")
    print(f"    {DIM}neutral{R}    : {counts['neutral']}")
    print(f"    {DIM}draw{R}       : {counts['draw']}")
    print(f"    {WARN}no_session{R} : {counts['no_session']}")
    print(f"    {WARN}no_result{R}  : {counts['no_result']}")
    print(f"  {'─'*52}")

    if args.dry_run:
        print(f"\n  {WARN}Dry run — no changes written.{R}")
        # Show sample
        for outcome, qdelta, row_id in updates[:5]:
            print(f"  {DIM}{row_id}{R}  →  {outcome}  (Δq={qdelta:+.3f})")
        return

    if not updates:
        print(f"\n  Nothing to update.")
        return

    # Write to parliament_move_deliberations
    for outcome, qdelta, row_id in updates:
        cms_conn.execute(
            "UPDATE parliament_move_deliberations SET outcome=? WHERE id=?",
            (outcome, row_id)
        )
    cms_conn.commit()
    print(f"\n  {OK}Updated {len(updates)} parliament rows.{R}")

    # Write to supermodel.db training_samples
    if sm_conn:
        sm_updated = 0
        for outcome, qdelta, row_id in updates:
            ts_id = f"ts.chess.{row_id}"
            result = sm_conn.execute(
                "SELECT id, quality_score FROM training_samples WHERE id=?",
                (ts_id,)
            ).fetchone()
            if result:
                new_quality = min(1.0, max(0.0,
                    float(result["quality_score"]) + qdelta))
                sm_conn.execute("""
                    UPDATE training_samples
                    SET outcome=?, adjudicated=1, quality_score=?
                    WHERE id=?
                """, (outcome, new_quality, ts_id))
                sm_updated += 1
        sm_conn.commit()
        print(f"  {OK}Updated {sm_updated} training_samples in supermodel.db.{R}")

    # Final state
    show_stats(cms_conn, sm_conn)
    synth_conn.close()
    cms_conn.close()
    if sm_conn:
        sm_conn.close()


if __name__ == "__main__":
    main()
