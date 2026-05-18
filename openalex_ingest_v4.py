#!/usr/bin/env python3
"""
openalex_ingest_v4.py — Stream OpenAlex .gz files into relations_aggregated.

Protocol: stream decompress → parse → aggregate → no temp files.
Fully resumable via ingest_progress.json in DUMP_DIR parent.

Usage:
    OPENALEX_DUMP=/mnt/openalex/works python3 openalex_ingest_v4.py
"""
import os
import sys
import gzip
import json
import hashlib
import sqlite3
from itertools import combinations
from pathlib import Path

DB_PATH    = Path.home() / "resonance_v11.db"
DUMP_DIR   = Path(os.environ.get("OPENALEX_DUMP", "/mnt/openalex/works"))
PROGRESS_F = DUMP_DIR.parent / "ingest_progress.json"
BATCH_SIZE = 5_000       # commit every N works
CONCEPT_MIN = 0.5        # minimum concept score to include
CONCEPT_MAX = 8          # max concepts per work to pair (caps pairwise at 28)


def anchor_id(canonical: str) -> str:
    return f"a.{hashlib.md5(canonical.encode()).hexdigest()[:12]}"


_STOP = {"a", "an", "the", "of", "and", "or", "in", "on", "to", "for", "with",
         "by", "as", "at", "from", "its", "it", "is", "are", "was", "be"}


def is_valid(name: str) -> bool:
    if not name or len(name) < 3 or len(name) > 80:
        return False
    words = name.split()
    if len(words) > 6:
        return False
    if words[0].lower() in _STOP or words[-1].lower() in _STOP:
        return False
    return True


def load_anchor_map(cur) -> dict:
    print("Loading anchor map...", end=" ", flush=True)
    cur.execute("SELECT id, canonical FROM anchors WHERE canonical IS NOT NULL")
    m = {row[1]: row[0] for row in cur.fetchall()}
    print(f"{len(m):,} anchors loaded")
    return m


def get_or_create(name: str, anchor_map: dict, cur, domain: str) -> str | None:
    key = name.lower().strip()
    if not is_valid(key):
        return None
    if key in anchor_map:
        return anchor_map[key]
    aid = anchor_id(key)
    cur.execute("""
        INSERT OR IGNORE INTO anchors
            (id, canonical, domain_tags, node_type, node_layer, maturity)
        VALUES (?, ?, ?, 'concept', 3, 0)
    """, (aid, key, domain))
    anchor_map[key] = aid
    return aid


def upsert(cur, subj: str, pred: str, obj: str, domain: str, edge_type: str):
    cur.execute("""
        INSERT INTO relations_aggregated
            (subject_id, predicate, object_id, domain_tags, edge_type,
             seen_count, evidence_count, confidence)
        VALUES (?, ?, ?, ?, ?, 1, 1, 1.0)
        ON CONFLICT(subject_id, predicate, object_id, domain_tags, edge_type)
        DO UPDATE SET
            seen_count     = seen_count + 1,
            evidence_count = evidence_count + 1
    """, (subj, pred, obj, domain, edge_type))


def process_work(work: dict, anchor_map: dict, cur) -> tuple[int, int]:
    concepts = work.get('concepts', [])
    topics   = work.get('topics', [])
    rels = 0

    # ── Concepts — co-occurrence pairs ───────────────────────────────────────
    scored = sorted(
        [c for c in concepts if c.get('score', 0) >= CONCEPT_MIN and c.get('display_name')],
        key=lambda c: c['score'], reverse=True
    )[:CONCEPT_MAX]

    # Domain = highest-level (level 0) concept name
    domain = 'openalex'
    top = [c for c in scored if c.get('level', 99) == 0]
    if top:
        domain = top[0]['display_name'].lower().replace(' ', '_')

    ids = [get_or_create(c['display_name'], anchor_map, cur, domain) for c in scored]
    ids = [i for i in ids if i]

    for a, b in combinations(ids, 2):
        upsert(cur, a, 'related_to', b, domain, 'associative')
        rels += 1

    # ── Topics — hierarchy relations ─────────────────────────────────────────
    for topic in topics[:3]:
        t_name   = topic.get('display_name', '')
        subfield = (topic.get('subfield') or {}).get('display_name', '')
        field    = (topic.get('field')    or {}).get('display_name', '')
        t_domain = (topic.get('domain')   or {}).get('display_name', 'openalex')
        t_domain = t_domain.lower().replace(' ', '_')

        t_id  = get_or_create(t_name,   anchor_map, cur, t_domain) if t_name   else None
        sf_id = get_or_create(subfield, anchor_map, cur, t_domain) if subfield else None
        f_id  = get_or_create(field,    anchor_map, cur, t_domain) if field    else None

        if t_id and sf_id:
            upsert(cur, t_id, 'part_of', sf_id, t_domain, 'taxonomic')
            rels += 1
        if sf_id and f_id:
            upsert(cur, sf_id, 'part_of', f_id, t_domain, 'taxonomic')
            rels += 1

    return rels


def process_gz(path: Path, anchor_map: dict, conn, cur) -> dict:
    stats = {'works': 0, 'rels': 0, 'errors': 0}
    with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                work = json.loads(line)
                stats['rels']  += process_work(work, anchor_map, cur)
                stats['works'] += 1
                if stats['works'] % BATCH_SIZE == 0:
                    conn.commit()
                    print(f"    {stats['works']:,} works | +{stats['rels']:,} rels", flush=True)
            except Exception:
                stats['errors'] += 1
    conn.commit()
    return stats


def load_progress() -> dict:
    if PROGRESS_F.exists():
        return json.loads(PROGRESS_F.read_text())
    return {"ingested": [], "failed": []}


def save_progress(p: dict):
    PROGRESS_F.write_text(json.dumps(p, indent=2))


def main():
    gz_files = sorted(DUMP_DIR.glob("*.gz"))
    if not gz_files:
        print(f"No .gz files in {DUMP_DIR}")
        sys.exit(1)

    progress = load_progress()
    done_set = set(progress["ingested"])
    todo     = [f for f in gz_files if f.name not in done_set]

    print("OpenAlex Stream Ingest v4")
    print(f"DB     : {DB_PATH}")
    print(f"Dump   : {DUMP_DIR}")
    print(f"Files  : {len(todo)} to process  ({len(done_set)} already done)\n")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-524288")   # 512 MB page cache
    conn.execute("PRAGMA temp_store=MEMORY")
    cur = conn.cursor()

    anchor_map = load_anchor_map(cur)
    total = {'works': 0, 'rels': 0}

    for i, gz_path in enumerate(todo, 1):
        print(f"\n[{i}/{len(todo)}] {gz_path.name}")
        try:
            stats = process_gz(gz_path, anchor_map, conn, cur)
            for k in total:
                total[k] += stats[k]
            progress["ingested"].append(gz_path.name)
            save_progress(progress)
            print(f"  ✓  {stats['works']:,} works | +{stats['rels']:,} rels"
                  f" | {stats['errors']} errors")
            print(f"  Running: {total['works']:,} works | {total['rels']:,} rels")
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            progress["failed"].append(gz_path.name)
            save_progress(progress)

    conn.close()
    print(f"\nDone. {total['works']:,} works processed | {total['rels']:,} relations upserted.")


if __name__ == "__main__":
    main()
