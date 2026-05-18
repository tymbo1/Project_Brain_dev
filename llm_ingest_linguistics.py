#!/usr/bin/env python3
"""
llm_ingest_linguistics.py — LLaMA-driven linguistics knowledge ingestion.

Generates structured relations for linguistics anchors, writes to
relations_llm (Tier 2 — provisional). Nothing touches relations_aggregated.

HITL gate: dry-run by default. Pass --commit to write to DB.
Pass --review to inspect pending relations and approve/reject.

Usage:
    python3 llm_ingest_linguistics.py --dry-run     # preview only
    python3 llm_ingest_linguistics.py --commit      # write to relations_llm
    python3 llm_ingest_linguistics.py --review      # HITL review pending
    python3 llm_ingest_linguistics.py --promote     # promote approved → relations_aggregated
"""

import sys, os, json, sqlite3, uuid, time, argparse, requests
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

DB_PATH    = Path.home() / "resonance_v11.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "llama3:8b"
SOURCE     = "llama3:8b"

THROTTLE   = 2   # seconds between LLM calls

parser = argparse.ArgumentParser()
parser.add_argument("--dry-run",  action="store_true", default=True)
parser.add_argument("--commit",   action="store_true")
parser.add_argument("--review",   action="store_true")
parser.add_argument("--promote",  action="store_true")
args = parser.parse_args()

if args.commit:
    args.dry_run = False

# ── Known anchor map ──────────────────────────────────────────────────────────
LINGUISTICS_ANCHORS = {
    "language":                   "a.8512ae7d57b1",
    "linguistics":                "a.416ddd003fb4",
    "grammar":                    "a.b3d1bd6a249a",
    "syntax":                     "a.55152fd428af",
    "semantics":                  "a.b19245958140",
    "phonology":                  "a.e8a169b75bf0",
    "morphology":                 "a.577a14659683",
    "pragmatics":                 "a.cbd2b5a9d63b",
    "discourse":                  "a.0ba1d42e6a6c",
    "vocabulary":                 "a.09f06963f502",
    "dialect":                    "a.6c7bac2f4e41",
    "writing system":             "a.22182a4aa6ee",
    "sign language":              "a.sign_language.56b8ada0e7",
    "natural language processing":"a.natural_language_pro.dc453b2d95",
}

# ── Relation schema to request from LLaMA ─────────────────────────────────────
PREDICATES = [
    "is_a", "part_of", "contains", "related_to", "derived_from",
    "enables", "requires", "used_for", "distinct_from", "co_occurs_with"
]

SYSTEM_PROMPT = """You are a linguistics knowledge engineer. Your job is to produce accurate,
structured factual relations about linguistics concepts.

Rules:
1. Only state well-established facts from linguistics.
2. Use only these predicates: is_a, part_of, contains, related_to, derived_from, enables, requires, used_for, distinct_from, co_occurs_with
3. Both subject and object must be real, specific linguistics concepts (single nouns or short noun phrases).
4. Return ONLY valid JSON — a list of objects with keys: subject, predicate, object, confidence (0.0-1.0)
5. No explanation, no markdown, no extra text. JSON only.
6. Aim for 15-25 high-quality relations."""

def ask_llama(concept: str) -> list[dict]:
    prompt = f"""You are a linguistics knowledge engineer. Generate accurate structured relations about "{concept}".

Rules:
- Only state well-established linguistics facts.
- Use only these predicates: is_a, part_of, contains, related_to, derived_from, enables, requires, used_for, distinct_from, co_occurs_with
- Both subject and object must be real linguistics concepts (single nouns or short noun phrases).
- Return ONLY a valid JSON array, no explanation, no markdown.

Example format:
[
  {{"subject": "syntax", "predicate": "part_of", "object": "grammar", "confidence": 0.95}},
  {{"subject": "syntax", "predicate": "related_to", "object": "semantics", "confidence": 0.9}}
]

Generate 15-20 high-quality relations for: "{concept}"
Focus on: definition, sub-fields, related concepts, what it enables, what it requires, how it relates to other linguistics branches.

JSON array:"""

    try:
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 800}
        }, timeout=60)
        raw = r.json()["response"].strip()

        # strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        print(f"  [LLM error: {e}]")
        return []

def resolve_anchor(name: str, conn: sqlite3.Connection) -> str | None:
    """Try to find anchor id by canonical name."""
    if name in LINGUISTICS_ANCHORS:
        return LINGUISTICS_ANCHORS[name]
    row = conn.execute(
        "SELECT id FROM anchors WHERE canonical = ? OR display_name = ?",
        (name.lower(), name)
    ).fetchone()
    return row[0] if row else None

def process_relations(concept: str, raw_rels: list[dict], conn: sqlite3.Connection) -> list[dict]:
    """Resolve anchor IDs, filter invalid, return insertable rows."""
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

        # skip if already in relations_aggregated
        exists = conn.execute(
            "SELECT 1 FROM relations_aggregated WHERE subject_id=? AND predicate=? AND object_id=? LIMIT 1",
            (subj_id, pred, obj_id)
        ).fetchone()
        if exists:
            continue

        valid.append({
            "id":              f"llm.{uuid.uuid4().hex[:12]}",
            "subject_id":      subj_id,
            "predicate":       pred,
            "object_id":       obj_id,
            "domain_tags":     "linguistics,llm_inferred",
            "edge_type":       "semantic",
            "confidence":      round(conf, 3),
            "source_model":    SOURCE,
            "generation_ts":   time.time(),
            "subject_name":    subj,
            "object_name":     obj,
        })
    return valid

# ── HITL review mode ──────────────────────────────────────────────────────────
def run_review(conn: sqlite3.Connection):
    pending = conn.execute("""
        SELECT r.id, a1.canonical, r.predicate, a2.canonical, r.confidence, r.source_model
        FROM relations_llm r
        JOIN anchors a1 ON r.subject_id = a1.id
        JOIN anchors a2 ON r.object_id = a2.id
        WHERE r.reviewed = 0
        ORDER BY r.confidence DESC
    """).fetchall()

    if not pending:
        print("No pending relations to review.")
        return

    print(f"\n{len(pending)} relations pending review.")
    print("Commands: y=approve  n=reject  s=skip  q=quit\n")

    approved = rejected = skipped = 0
    for row in pending:
        rid, subj, pred, obj, conf, model = row
        print(f"  [{conf:.2f}] {subj} --{pred}--> {obj}  (model: {model})")
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

    print(f"\nReview done — approved: {approved}, rejected: {rejected}, skipped: {skipped}")

# ── Promote approved → relations_aggregated ───────────────────────────────────
def _stamp_sem_domain(conn: sqlite3.Connection, anchor_ids: set, domain: str):
    """Overwrite ssre_top_semantic for every anchor in the set with a single domain entry.

    Called at promote time so LLM-ingested anchors are never misclassified by
    the auto-computed ssre scores (e.g. linguistics anchors classified as 'music').
    """
    if not anchor_ids:
        return
    conn.execute(
        f"DELETE FROM ssre_top_semantic WHERE anchor_id IN ({','.join('?'*len(anchor_ids))})",
        list(anchor_ids)
    )
    conn.executemany(
        "INSERT INTO ssre_top_semantic (anchor_id, sem_domain) VALUES (?, ?)",
        [(aid, domain) for aid in anchor_ids]
    )


def run_promote(conn: sqlite3.Connection):
    approved = conn.execute("""
        SELECT id, subject_id, predicate, object_id, domain_tags, edge_type, confidence
        FROM relations_llm WHERE reviewed=1 AND approved=1
    """).fetchall()

    if not approved:
        print("No approved relations to promote.")
        return

    print(f"Promoting {len(approved)} approved relations to relations_aggregated...")
    promoted = 0
    touched_anchors: set = set()

    for row in approved:
        rid, subj_id, pred, obj_id, dtags, etype, conf = row
        exists = conn.execute(
            "SELECT 1 FROM relations_aggregated WHERE subject_id=? AND predicate=? AND object_id=? LIMIT 1",
            (subj_id, pred, obj_id)
        ).fetchone()
        if not exists:
            conn.execute("""
                INSERT INTO relations_aggregated
                (subject_id, predicate, object_id, domain_tags, edge_type, confidence, seen_count, evidence_count)
                VALUES (?,?,?,?,?,?,1,1)
            """, (subj_id, pred, obj_id, dtags, etype, conf))
            promoted += 1
        touched_anchors.add(subj_id)
        touched_anchors.add(obj_id)

    # Stamp sem_domain for every anchor that participated in this ingestion batch.
    # Derives the target domain from domain_tags of the batch (e.g. 'linguistics').
    # Prevents auto-computed ssre scores from misclassifying domain-specific anchors.
    batch_domain = "linguistics"   # matches domain_tags written by this script
    if touched_anchors:
        _stamp_sem_domain(conn, touched_anchors, batch_domain)
        print(f"Stamped sem_domain='{batch_domain}' on {len(touched_anchors)} anchors.")

    conn.commit()
    print(f"Promoted {promoted} new relations to Tier 1.")
    print("(Duplicates skipped.)")

# ── Main ingestion ─────────────────────────────────────────────────────────────
def main():
    conn = sqlite3.connect(DB_PATH)

    if args.review:
        run_review(conn)
        return

    if args.promote:
        run_promote(conn)
        return

    mode = "DRY RUN" if args.dry_run else "COMMIT"
    print(f"\nLinguistics LLM Ingestion — {mode}")
    print(f"Model: {MODEL} | Concepts: {len(LINGUISTICS_ANCHORS)}")
    print("=" * 60)

    total_proposed = total_written = 0

    for concept, anchor_id in LINGUISTICS_ANCHORS.items():
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
        print(f"\nNext steps:")
        print(f"  Review:  python3 llm_ingest_linguistics.py --review")
        print(f"  Promote: python3 llm_ingest_linguistics.py --promote")
    else:
        print("DRY RUN — nothing written. Re-run with --commit to write.")

if __name__ == "__main__":
    main()
