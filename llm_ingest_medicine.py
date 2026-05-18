#!/usr/bin/env python3
"""
llm_ingest_medicine.py — Medicine domain ingestion, Pass 1 (top-level concepts).

20 core medicine anchors. Same HITL pipeline as linguistics:
    --commit → apply_medicine_review.py → --promote

Pass 2 (sub-fields): llm_ingest_medicine_depth.py
"""

import sys, json, sqlite3, uuid, time, argparse, requests
from pathlib import Path

from ollama_guard import ingest_checkpoint
sys.path.insert(0, str(Path(__file__).parent))

DB_PATH    = Path.home() / "resonance_v11.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "llama3:8b"
SOURCE     = "llama3:8b"
THROTTLE   = 2

parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true", default=True)
parser.add_argument("--commit",  action="store_true")
parser.add_argument("--review",  action="store_true")
parser.add_argument("--promote", action="store_true")
args = parser.parse_args()
if args.commit:
    args.dry_run = False

PASS1_ANCHORS = {
    "medicine":     "a.d9e5d212320e",
    "disease":      "a.9e19f8857441",
    "diagnosis":    "a.c70a904daa78",
    "treatment":    "a.6292fea48cc1",
    "anatomy":      "a.c751d56f7227",
    "physiology":   "a.e8c5fe53b780",
    "pathology":    "a.3bf7a03174f7",
    "pharmacology": "a.b574e56bef43",
    "immunology":   "a.bcf822414f4b",
    "surgery":      "a.c6b746b77cb2",
    "genetics":     "a.1edae102638a",
    "epidemiology": "a.5e0d9fac064a",
    "neurology":    "a.ffa95af52c0d",
    "cardiology":   "a.86483bfca823",
    "oncology":     "a.b31537781a63",
    "microbiology": "a.617c126cabc4",
    "biochemistry": "a.8a1c5ca400b7",
    "psychiatry":   "a.14bbdf32228c",
    "pediatrics":   "a.8df8c2f3d25a",
    "radiology":    "a.a3cf66963cec",
}

PREDICATES = [
    "is_a", "part_of", "contains", "related_to", "derived_from",
    "enables", "requires", "used_for", "distinct_from", "co_occurs_with"
]

def ask_llama(concept: str) -> list[dict]:
    prompt = f"""You are a medical knowledge engineer. Generate accurate structured relations about the medical concept "{concept}".

Rules:
- Only state well-established medical/biological facts.
- Use only these predicates: is_a, part_of, contains, related_to, derived_from, enables, requires, used_for, distinct_from, co_occurs_with
- Both subject and object must be real medical or biological concepts (single nouns or short noun phrases).
- Be precise about direction: if A is part of B, write A part_of B (not B contains A unless B actually contains A).
- Return ONLY a valid JSON array, no explanation, no markdown, no preamble.

Example format:
[
  {{"subject": "diagnosis", "predicate": "is_a", "object": "medical process", "confidence": 0.97}},
  {{"subject": "diagnosis", "predicate": "requires", "object": "symptom", "confidence": 0.95}}
]

Generate 15-20 high-quality relations for: "{concept}"
Focus on: what it is_a, what it is part_of, what it contains, what it requires,
what it enables, what it is distinct_from, how it relates to adjacent medical concepts.
Prioritise specific semantic edges over generic related_to.

JSON array:"""

    for attempt in range(3):
        try:
            r = requests.post(OLLAMA_URL, json={
                "model": MODEL, "prompt": prompt, "stream": False,
                "options": {"temperature": 0.3, "num_predict": 900}
            }, timeout=60)
            raw = r.json()["response"].strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()
            start = raw.find("[")
            end   = raw.rfind("]")
            if start == -1 or end == -1:
                raise ValueError("no JSON array found")
            data = json.loads(raw[start:end+1])
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"  [attempt {attempt+1} failed: {e}]")
            time.sleep(2)
    return []

def resolve_anchor(name: str, conn: sqlite3.Connection) -> str | None:
    if name in PASS1_ANCHORS:
        return PASS1_ANCHORS[name]
    row = conn.execute(
        "SELECT id FROM anchors WHERE canonical = ? OR display_name = ?",
        (name.lower(), name)
    ).fetchone()
    return row[0] if row else None

def process_relations(concept: str, raw_rels: list, conn: sqlite3.Connection) -> list[dict]:
    valid = []
    seen = set()
    for r in raw_rels:
        subj = str(r.get("subject", "")).strip().lower()
        pred = str(r.get("predicate", "")).strip().lower()
        obj  = str(r.get("object", "")).strip().lower()
        conf = float(r.get("confidence", 0.7))
        if not subj or not pred or not obj:
            continue
        if pred not in PREDICATES:
            continue
        if subj == obj:
            continue
        subj_id = resolve_anchor(subj, conn)
        obj_id  = resolve_anchor(obj, conn)
        if not subj_id or not obj_id:
            continue
        key = (subj_id, pred, obj_id)
        if key in seen:
            continue
        seen.add(key)
        exists = conn.execute(
            "SELECT 1 FROM relations_aggregated WHERE subject_id=? AND predicate=? AND object_id=? LIMIT 1",
            (subj_id, pred, obj_id)
        ).fetchone()
        if exists:
            continue
        valid.append({
            "id":            f"llm.{uuid.uuid4().hex[:12]}",
            "subject_id":    subj_id,
            "predicate":     pred,
            "object_id":     obj_id,
            "domain_tags":   "medicine,llm_inferred",
            "edge_type":     "semantic",
            "confidence":    round(conf, 3),
            "source_model":  SOURCE,
            "generation_ts": time.time(),
            "subject_name":  subj,
            "object_name":   obj,
        })
    return valid

def run_review(conn):
    pending = conn.execute("""
        SELECT r.id, a1.canonical, r.predicate, a2.canonical, r.confidence
        FROM relations_llm r
        JOIN anchors a1 ON r.subject_id = a1.id
        JOIN anchors a2 ON r.object_id = a2.id
        WHERE r.reviewed = 0 ORDER BY r.confidence DESC
    """).fetchall()
    if not pending:
        print("No pending relations.")
        return
    print(f"\n{len(pending)} pending. y=approve  n=reject  s=skip  q=quit\n")
    approved = rejected = skipped = 0
    for rid, subj, pred, obj, conf in pending:
        print(f"  [{conf:.2f}] {subj} --{pred}--> {obj}")
        choice = input("  > ").strip().lower()
        if choice == "q":
            break
        elif choice == "y":
            conn.execute("UPDATE relations_llm SET reviewed=1, approved=1 WHERE id=?", (rid,))
            approved += 1
        elif choice == "n":
            conn.execute("UPDATE relations_llm SET reviewed=1, approved=0 WHERE id=?", (rid,))
            rejected += 1
        else:
            skipped += 1
        conn.commit()
    print(f"\napproved: {approved}  rejected: {rejected}  skipped: {skipped}")

def _stamp_sem_domain(conn, anchor_ids: set, domain: str):
    if not anchor_ids:
        return
    conn.execute(
        f"DELETE FROM ssre_top_semantic WHERE anchor_id IN ({','.join('?'*len(anchor_ids))})",
        list(anchor_ids)
    )
    conn.executemany(
        "INSERT INTO ssre_top_semantic (anchor_id, sem_domain) VALUES (?,?)",
        [(aid, domain) for aid in anchor_ids]
    )

def run_promote(conn):
    approved = conn.execute("""
        SELECT id, subject_id, predicate, object_id, domain_tags, edge_type, confidence
        FROM relations_llm WHERE reviewed=1 AND approved=1
    """).fetchall()
    if not approved:
        print("No approved relations.")
        return
    print(f"Promoting {len(approved)} approved relations...")
    promoted = 0
    touched: set = set()
    for row in approved:
        rid, sid, pred, oid, dtags, etype, conf = row
        exists = conn.execute(
            "SELECT 1 FROM relations_aggregated WHERE subject_id=? AND predicate=? AND object_id=? LIMIT 1",
            (sid, pred, oid)
        ).fetchone()
        if not exists:
            conn.execute("""
                INSERT INTO relations_aggregated
                (subject_id, predicate, object_id, domain_tags, edge_type, confidence, seen_count, evidence_count)
                VALUES (?,?,?,?,?,?,1,1)
            """, (sid, pred, oid, dtags, etype, conf))
            promoted += 1
        touched.add(sid)
        touched.add(oid)
    if touched:
        _stamp_sem_domain(conn, touched, "medicine")
        print(f"Stamped sem_domain='medicine' on {len(touched)} anchors.")
    conn.commit()
    print(f"Promoted {promoted} new relations to Tier 1.")
    print("(Duplicates skipped.)")

def main():
    conn = sqlite3.connect(DB_PATH)
    if args.review:
        run_review(conn)
        return
    if args.promote:
        run_promote(conn)
        return

    mode = "DRY RUN" if args.dry_run else "COMMIT"
    print(f"\nMedicine Pass 1 — {mode}")
    print(f"Model: {MODEL} | Concepts: {len(PASS1_ANCHORS)}")
    print("=" * 60)

    total_proposed = total_written = 0
    for i, (concept, anchor_id) in enumerate(PASS1_ANCHORS.items()):
        ingest_checkpoint(i, interval=30, label='ingest')
        print(f"\n[{concept}]")
        raw = ask_llama(concept)
        print(f"  LLM returned {len(raw)} raw relations")
        valid = process_relations(concept, raw, conn)
        print(f"  Valid/new after filtering: {len(valid)}")
        total_proposed += len(valid)
        for rel in valid:
            print(f"    {rel['subject_name']} --{rel['predicate']}--> {rel['object_name']} [{rel['confidence']}]")
            if not args.dry_run:
                conn.execute("""
                    INSERT OR IGNORE INTO relations_llm
                    (id, subject_id, predicate, object_id, domain_tags, edge_type,
                     confidence, source_model, generation_ts)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    rel["id"], rel["subject_id"], rel["predicate"], rel["object_id"],
                    rel["domain_tags"], rel["edge_type"], rel["confidence"],
                    rel["source_model"], rel["generation_ts"]
                ))
        if not args.dry_run:
            conn.commit()
            total_written += len(valid)
        time.sleep(THROTTLE)

    print(f"\n{'='*60}")
    print(f"Total proposed: {total_proposed}")
    if not args.dry_run:
        print(f"Written to relations_llm: {total_written}")
        print(f"\nNext: export to medicine_pass1_review.md → GPT HITL → apply_medicine_review.py → --promote")
    else:
        print("DRY RUN — nothing written. Re-run with --commit to write.")

if __name__ == "__main__":
    main()
