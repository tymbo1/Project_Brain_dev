#!/usr/bin/env python3
"""
llm_ingest_ling_depth.py — Linguistics depth pass: sub-field concepts.

Generates structured relations for linguistics sub-field anchors (phoneme,
morpheme, syntax sub-concepts, semantics, pragmatics, etc.).
Writes to relations_llm (Tier 2 — provisional). HITL gate applies.

Usage:
    python3 llm_ingest_ling_depth.py --dry-run     # preview only
    python3 llm_ingest_ling_depth.py --commit      # write to relations_llm
    python3 llm_ingest_ling_depth.py --review      # HITL review pending
    python3 llm_ingest_ling_depth.py --promote     # promote approved → relations_aggregated
"""

import sys, os, json, sqlite3, uuid, time, argparse, requests
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

DB_PATH    = Path.home() / "resonance_v11.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "llama3:8b"
SOURCE     = "llama3:8b"
THROTTLE   = 2

parser = argparse.ArgumentParser()
parser.add_argument("--dry-run",  action="store_true", default=True)
parser.add_argument("--commit",   action="store_true")
parser.add_argument("--review",   action="store_true")
parser.add_argument("--promote",  action="store_true")
args = parser.parse_args()

if args.commit:
    args.dry_run = False

# ── Sub-field anchor map (depth pass) ─────────────────────────────────────────
# Organised by linguistic sub-domain. Lowest-maturity anchors prioritised —
# they are most underrepresented in the current knowledge field.
DEPTH_ANCHORS = {
    # Phonology
    "phoneme":          "a.9c7360bb7e64",
    "allophone":        "a.b39a36a276ce",
    "syllable":         "a.bc2d9185b78b",
    "intonation":       "a.b43cf1a9d0a3",
    "phonotactics":     "a.6f5c51c89c76",
    "prosody":          "a.dabdcd248243",
    # Morphology
    "morpheme":         "a.42d750d44830",
    "affix":            "a.d8b9bb5e6444",
    "inflection":       "a.b5ccf0b30982",
    "derivation":       "a.b600b1405a25",
    "compounding":      "a.677f63245c4f",
    "paradigm":         "a.8d992b8e2e7a",
    # Syntax
    "phrase":           "a.385aa5385e83",
    "clause":           "a.ab1565439a9d",
    "constituent":      "a.e104e8316efc",
    "word order":       "a.e8942ecff9f2",
    "dependency":       "a.e54debd65d81",
    "tense":            "a.51da34ead8ca",
    "aspect":           "a.e67e20d1ede0",
    # Semantics
    "meaning":          "a.f2f990f68492",
    "entailment":       "a.3374d84a01dc",
    "semantic role":    "a.semantic_role.cca8fdfe30",
    "presupposition":   "a.4f14cf5d7f9a",
    "reference":        "a.b8af13ea9c8f",
    "implicature":      "a.41111474fbb0",
    # Pragmatics / Discourse
    "speech act":       "a.speech_act.bbf15e0316",
    "deixis":           "a.7e79e531b6ab",
    "context":          "a.5c18ef727715",
    "cohesion":         "a.f61bc28aac42",
    "coherence":        "a.c64854b8ad94",
    "anaphora":         "a.5490078e7843",
    "genre":            "a.7f80095aea4d",
    # Historical / Typological
    "language family":  "a.939d534af2a4",
    "cognate":          "a.ee193ef67a92",
    "reconstruction":   "a.e75ffd9ae04f",
    "language change":  "a.8ddeaf002bc7",
    # Sociolinguistics
    "code-switching":   "a.fbed770e23e2",
    "pidgin":           "a.9b35f65cd0ec",
    "creole":           "a.a8a4f4d35fb4",
    "language contact": "a.84ebae9b3976",
    # Psycholinguistics
    "language acquisition": "a.3b3bb43166ff",
    "bilingualism":     "a.a85745fb8bf2",
    "aphasia":          "a.5ec8d9ef35bd",
}

PREDICATES = [
    "is_a", "part_of", "contains", "related_to", "derived_from",
    "enables", "requires", "used_for", "distinct_from", "co_occurs_with"
]

def ask_llama(concept: str) -> list[dict]:
    prompt = f"""You are a linguistics knowledge engineer. Generate accurate structured relations about the linguistics concept "{concept}".

Rules:
- Only state well-established linguistics facts.
- Use only these predicates: is_a, part_of, contains, related_to, derived_from, enables, requires, used_for, distinct_from, co_occurs_with
- Both subject and object must be real linguistics concepts (single nouns or short noun phrases).
- Return ONLY a valid JSON array, no explanation, no markdown.

Example format:
[
  {{"subject": "phoneme", "predicate": "part_of", "object": "phonology", "confidence": 0.95}},
  {{"subject": "phoneme", "predicate": "distinct_from", "object": "allophone", "confidence": 0.9}}
]

Generate 15-20 high-quality relations for: "{concept}"
Focus on: definition, what it is_a, what it is part_of, what it contains, what it requires,
what it enables, how it relates to adjacent linguistics concepts.
Prioritise specific semantic relations over generic related_to.

JSON array:"""

    try:
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 900}
        }, timeout=60)
        raw = r.json()["response"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  [LLM error: {e}]")
        return []

def resolve_anchor(name: str, conn: sqlite3.Connection) -> str | None:
    if name in DEPTH_ANCHORS:
        return DEPTH_ANCHORS[name]
    row = conn.execute(
        "SELECT id FROM anchors WHERE canonical = ? OR display_name = ?",
        (name.lower(), name)
    ).fetchone()
    return row[0] if row else None

def process_relations(concept: str, raw_rels: list[dict], conn: sqlite3.Connection) -> list[dict]:
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
            "id":           f"llm.{uuid.uuid4().hex[:12]}",
            "subject_id":   subj_id,
            "predicate":    pred,
            "object_id":    obj_id,
            "domain_tags":  "linguistics,llm_inferred",
            "edge_type":    "semantic",
            "confidence":   round(conf, 3),
            "source_model": SOURCE,
            "generation_ts":time.time(),
            "subject_name": subj,
            "object_name":  obj,
        })
    return valid

# ── HITL review ───────────────────────────────────────────────────────────────
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

# ── Promote ───────────────────────────────────────────────────────────────────
def _stamp_sem_domain(conn: sqlite3.Connection, anchor_ids: set, domain: str):
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

    batch_domain = "linguistics"
    if touched_anchors:
        _stamp_sem_domain(conn, touched_anchors, batch_domain)
        print(f"Stamped sem_domain='{batch_domain}' on {len(touched_anchors)} anchors.")

    conn.commit()
    print(f"Promoted {promoted} new relations to Tier 1.")
    print("(Duplicates skipped.)")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    conn = sqlite3.connect(DB_PATH)

    if args.review:
        run_review(conn)
        return

    if args.promote:
        run_promote(conn)
        return

    mode = "DRY RUN" if args.dry_run else "COMMIT"
    print(f"\nLinguistics Depth Pass — {mode}")
    print(f"Model: {MODEL} | Concepts: {len(DEPTH_ANCHORS)}")
    print("=" * 60)

    total_proposed = total_written = 0

    for concept, anchor_id in DEPTH_ANCHORS.items():
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
        print(f"  Review:  python3 llm_ingest_ling_depth.py --review")
        print(f"  Promote: python3 llm_ingest_ling_depth.py --promote")
    else:
        print("DRY RUN — nothing written. Re-run with --commit to write.")

if __name__ == "__main__":
    main()
