"""
Chunked, resumable aggregation of relations → relations_aggregated.

Identity key: (subject_id, predicate, object_id, domain_tags, edge_type)
This preserves cross-domain and semantic separation while collapsing true
ingestion duplicates (same row inserted multiple times).

114M rows → ~12.7M unique by (s,p,o) — but many are legitimately distinct
by domain/edge_type context. The richer key keeps that signal intact.

Safe: never modifies or deletes the original relations table.
Checkpoint: aggregate_checkpoint.txt — re-run anytime to resume.
"""

import sqlite3
import os
import time

DB_PATH = os.path.expanduser("~/resonance_v11.db")
CHECKPOINT_FILE = os.path.join(os.path.dirname(__file__), "aggregate_checkpoint.txt")

ROWID_MIN = 2603
ROWID_MAX = 115_314_651
CHUNK_SIZE = 1_000_000  # ~114 chunks total

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS relations_aggregated (
    subject_id     TEXT NOT NULL,
    predicate      TEXT NOT NULL,
    object_id      TEXT NOT NULL,
    domain_tags    TEXT NOT NULL DEFAULT '',
    edge_type      TEXT NOT NULL DEFAULT '',
    seen_count     INTEGER NOT NULL DEFAULT 1,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    confidence     REAL DEFAULT 1.0,
    edge_weight    REAL,
    polarity       TEXT,
    PRIMARY KEY (subject_id, predicate, object_id, domain_tags, edge_type)
)
"""

# Upsert: richer identity key preserves cross-domain and semantic variants.
# Collapses only true ingestion duplicates (identical on all 5 key columns).
UPSERT_SQL = """
INSERT INTO relations_aggregated
    (subject_id, predicate, object_id, domain_tags, edge_type,
     seen_count, evidence_count, confidence, edge_weight, polarity)
SELECT
    subject_id,
    predicate,
    object_id,
    COALESCE(NULLIF(TRIM(domain_tags), ''), '') AS domain_tags,
    COALESCE(NULLIF(TRIM(edge_type),   ''), '') AS edge_type,
    SUM(COALESCE(seen_count, 1))               AS seen_count,
    SUM(COALESCE(evidence_count, 1))           AS evidence_count,
    MAX(COALESCE(confidence, 1.0))             AS confidence,
    MAX(edge_weight)                           AS edge_weight,
    MAX(polarity)                              AS polarity
FROM relations
WHERE rowid BETWEEN ? AND ?
  AND subject_id IS NOT NULL
  AND predicate  IS NOT NULL
  AND object_id  IS NOT NULL
GROUP BY subject_id, predicate, object_id, domain_tags, edge_type
ON CONFLICT(subject_id, predicate, object_id, domain_tags, edge_type) DO UPDATE SET
    seen_count     = seen_count     + excluded.seen_count,
    evidence_count = evidence_count + excluded.evidence_count,
    confidence     = MAX(confidence,  excluded.confidence),
    edge_weight    = MAX(COALESCE(edge_weight, 0.0), COALESCE(excluded.edge_weight, 0.0))
"""


def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            val = f.read().strip()
            return int(val) if val else ROWID_MIN - 1
    return ROWID_MIN - 1


def save_checkpoint(max_rowid):
    with open(CHECKPOINT_FILE, "w") as f:
        f.write(str(max_rowid))


def run():
    con = sqlite3.connect(DB_PATH, timeout=120)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-256000")  # 256MB cache
    con.execute(CREATE_TABLE)
    con.commit()

    start_rowid = load_checkpoint() + 1
    if start_rowid > ROWID_MAX:
        print("Already complete — nothing to do.")
        return

    total_chunks = (ROWID_MAX - start_rowid) // CHUNK_SIZE + 1
    done_chunks = (start_rowid - ROWID_MIN) // CHUNK_SIZE
    print(f"Resuming from rowid {start_rowid:,}  ({done_chunks} chunks already done)")
    print(f"Remaining: ~{total_chunks} chunks  ×  {CHUNK_SIZE:,} rows = ~{total_chunks * CHUNK_SIZE:,} rows")

    chunk_num = 0
    lo = start_rowid

    while lo <= ROWID_MAX:
        hi = min(lo + CHUNK_SIZE - 1, ROWID_MAX)
        t0 = time.time()

        con.execute(UPSERT_SQL, (lo, hi))
        con.commit()
        save_checkpoint(hi)

        elapsed = time.time() - t0
        chunk_num += 1
        pct = (hi - ROWID_MIN) / (ROWID_MAX - ROWID_MIN) * 100
        print(f"  chunk {chunk_num:>4}  rowid {lo:>12,}–{hi:>12,}  {elapsed:.1f}s  ({pct:.1f}%)")

        lo = hi + 1

    # Final stats
    cur = con.execute("SELECT COUNT(*) FROM relations_aggregated")
    agg_count = cur.fetchone()[0]
    cur2 = con.execute("SELECT COUNT(*) FROM relations")
    raw_count = cur2.fetchone()[0]
    print(f"\nDone. relations_aggregated: {agg_count:,} rows")
    print(f"Original relations: {raw_count:,} rows")
    print(f"Compression: {agg_count/raw_count:.2%} of raw")
    con.close()


if __name__ == "__main__":
    run()
