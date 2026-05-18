#!/usr/bin/env python3
"""
SSRE Precompute — one-time attractor cache builder.

Computes attractor scores entirely via SQL (no 57M-row Python load).
Stores results in ssre_attractor_cache table inside the CMS.

Attractor score formula (from ssre_concept_attractors.py):
    score = incoming_degree × outgoing_degree

Run once, then activation_engine.py uses the cache via JOIN.
Usage:
    python ssre_precompute.py [--db ~/cmsp0/resonance_v11.db] [--limit 5000]
"""

import sqlite3
import os
import sys
import time
import argparse

DEFAULT_DB  = os.path.expanduser("~/resonance_v11.db")
DEFAULT_TOP = 5000


def log(msg: str):
    print(f"[ssre_precompute] {msg}", flush=True)


def build_cache(db_path: str, top_n: int):
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")

    # ── Step 1: incoming degree per anchor ───────────────────────────────────
    log("Computing incoming degree... (may take a few minutes)")
    t0 = time.time()
    con.execute("DROP TABLE IF EXISTS _tmp_inc")
    con.execute("""
        CREATE TEMP TABLE _tmp_inc AS
        SELECT object_id AS anchor_id, COUNT(*) AS inc_cnt
        FROM relations_aggregated
        GROUP BY object_id
    """)
    log(f"  done in {time.time()-t0:.1f}s")

    # ── Step 2: outgoing degree per anchor ───────────────────────────────────
    log("Computing outgoing degree...")
    t0 = time.time()
    con.execute("DROP TABLE IF EXISTS _tmp_out")
    con.execute("""
        CREATE TEMP TABLE _tmp_out AS
        SELECT subject_id AS anchor_id, COUNT(*) AS out_cnt
        FROM relations_aggregated
        GROUP BY subject_id
    """)
    log(f"  done in {time.time()-t0:.1f}s")

    # ── Step 3: join and compute attractor score ──────────────────────────────
    log(f"Joining and ranking top {top_n} attractors...")
    t0 = time.time()
    con.execute("DROP TABLE IF EXISTS ssre_attractor_cache")
    con.execute(f"""
        CREATE TABLE ssre_attractor_cache AS
        SELECT
            a.id           AS anchor_id,
            a.canonical    AS label,
            COALESCE(i.inc_cnt, 0) AS incoming,
            COALESCE(o.out_cnt, 0) AS outgoing,
            COALESCE(i.inc_cnt, 0) * COALESCE(o.out_cnt, 0) AS attractor_score
        FROM anchors a
        JOIN _tmp_inc i ON a.id = i.anchor_id
        JOIN _tmp_out o ON a.id = o.anchor_id
        ORDER BY attractor_score DESC
        LIMIT {top_n}
    """)
    log(f"  done in {time.time()-t0:.1f}s")

    # ── Step 4: index for fast JOIN in activation_engine ─────────────────────
    log("Creating index on ssre_attractor_cache.anchor_id ...")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ssre_att_anchor ON ssre_attractor_cache(anchor_id)")
    con.commit()

    # ── Step 5: quick summary ─────────────────────────────────────────────────
    row = con.execute("SELECT COUNT(*) FROM ssre_attractor_cache").fetchone()
    top = con.execute("""
        SELECT label, incoming, outgoing, attractor_score
        FROM ssre_attractor_cache
        ORDER BY attractor_score DESC
        LIMIT 10
    """).fetchall()
    log(f"Cache built: {row[0]} attractor nodes stored.")
    log("Top 10 attractors:")
    for r in top:
        print(f"   {r[0]:<30}  in={r[1]:>6}  out={r[2]:>6}  score={r[3]:>10}")

    # ── Step 6: store metadata ────────────────────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS ssre_precompute_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    import datetime
    con.execute("INSERT OR REPLACE INTO ssre_precompute_meta VALUES (?, ?)",
                ("attractor_cache_built", datetime.datetime.now().isoformat()))
    con.execute("INSERT OR REPLACE INTO ssre_precompute_meta VALUES (?, ?)",
                ("attractor_cache_top_n", str(top_n)))
    con.commit()
    con.close()
    log("Done.")


def build_semantic_cache(db_path: str):
    """
    Step 7 (separate): build ssre_top_semantic from domain_confidence.
    Maps each anchor to its highest-confidence semantic domain.
    Used by activation_engine for cross-domain noise suppression.
    """
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")

    # Check domain_confidence exists
    if not con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='domain_confidence'"
    ).fetchone():
        log("domain_confidence table not found — skipping semantic cache.")
        con.close()
        return

    log("Building top semantic domain lookup from domain_confidence...")
    t0 = time.time()
    con.execute("DROP TABLE IF EXISTS ssre_top_semantic")
    con.execute("""
        CREATE TABLE ssre_top_semantic AS
        SELECT dc.target_id AS anchor_id, dc.domain AS sem_domain
        FROM domain_confidence dc
        INNER JOIN (
            SELECT target_id, MAX(confidence) AS max_conf
            FROM domain_confidence
            GROUP BY target_id
        ) best ON dc.target_id = best.target_id
               AND dc.confidence = best.max_conf
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_ssre_sem_anchor ON ssre_top_semantic(anchor_id)")
    con.commit()
    log(f"  done in {time.time()-t0:.1f}s")

    row = con.execute("SELECT COUNT(*) FROM ssre_top_semantic").fetchone()
    sample = con.execute("""
        SELECT s.sem_domain, COUNT(*) as cnt
        FROM ssre_top_semantic s
        GROUP BY s.sem_domain ORDER BY cnt DESC LIMIT 8
    """).fetchall()
    log(f"Semantic cache: {row[0]} anchors mapped.")
    for r in sample:
        print(f"   {r[0]:<30}  {r[1]:>8}")

    con.execute("""
        CREATE TABLE IF NOT EXISTS ssre_precompute_meta (
            key TEXT PRIMARY KEY, value TEXT
        )
    """)
    import datetime
    con.execute("INSERT OR REPLACE INTO ssre_precompute_meta VALUES (?, ?)",
                ("semantic_cache_built", datetime.datetime.now().isoformat()))
    con.commit()
    con.close()
    log("Semantic cache done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",           default=DEFAULT_DB,  help="Path to CMS SQLite DB")
    parser.add_argument("--limit",        default=DEFAULT_TOP, type=int, help="Top-N attractors to cache")
    parser.add_argument("--semantic-only", action="store_true", help="Only build semantic domain cache (skip attractor recompute)")
    args = parser.parse_args()

    db = os.path.expanduser(args.db)
    if not os.path.exists(db):
        print(f"ERROR: DB not found: {db}", file=sys.stderr)
        sys.exit(1)

    log(f"DB: {db}")
    if args.semantic_only:
        build_semantic_cache(db)
    else:
        log(f"Top N: {args.limit}")
        build_cache(db, args.limit)
        build_semantic_cache(db)
