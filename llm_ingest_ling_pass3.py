#!/usr/bin/env python3
"""
llm_ingest_ling_pass3.py — Linguistics Pass 3: sub-sub-field internals.

Narrows into the 4 core systems:
  Phonology internals:  vowel, consonant, place/manner of articulation,
                        syllable structure components
  Morphology internals: bound/free morpheme, root, stem, lexeme
  Syntax internals:     phrase types, grammatical functions, word classes
  Semantics internals:  sense relations, prototype, compositionality, scope

Usage:
    python3 llm_ingest_ling_pass3.py --dry-run
    python3 llm_ingest_ling_pass3.py --commit
    python3 llm_ingest_ling_pass3.py --review
    python3 llm_ingest_ling_pass3.py --promote
"""

import sys, os, json, sqlite3, uuid, time, argparse, requests
from pathlib import Path

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

# ── Pass 3 anchor map ─────────────────────────────────────────────────────────
PASS3_ANCHORS = {
    # Phonology — place/manner of articulation
    "vowel":        "a.fb4ee69c5a35",
    "consonant":    "a.dd93c6336f82",
    "fricative":    "a.3a7812ac3f43",
    "stop":         "a.ef399b2d446b",
    "nasal":        "a.9f8bf5d0591c",
    "approximant":  "a.b1e136bd0404",
    "affricate":    "a.213cbc644e0f",
    "voiced":       "a.5739616927a8",
    "voiceless":    "a.8f986047a27e",
    "bilabial":     "a.e588331fb5a5",
    "alveolar":     "a.a1c0057d5483",
    "velar":        "a.5153ab61c92d",
    "glottal":      "a.a43943d4ef3b",
    # Phonology — syllable structure
    "onset":        "a.af835d86318f",
    "nucleus":      "a.91c7e4ea85be",
    "coda":         "a.3eb1a078a5d1",
    "rime":         "a.06e8ac704409",
    "foot":         "a.d8735f7489c9",
    "mora":         "a.c427ed4dc3e6",
    # Morphology internals
    "bound morpheme": "a.d5d64e98afc7",
    "free morpheme":  "a.14d69df95c9a",
    "root":           "a.63a9f0ea7bb9",
    "stem":           "a.e730db5c29b7",
    "lexeme":         "a.310feb4824cd",
    # Syntax — phrase types & grammatical functions
    "noun phrase":        "a.3152a7a0f53c",
    "verb phrase":        "a.4a23b4fcbb5a",
    "prepositional phrase": "a.6faaf77a2fb2",
    "adjective phrase":   "a.ec359083fcda",
    "subject":            "a.b5e3374e43f6",
    "predicate":          "a.f670ef68565f",
    "object":             "a.a8cfde6331bd",
    "complement":         "a.e6c27da91972",
    "modifier":           "a.3ad7320fa61b",
    "head":               "a.96e89a298e0a",
    # Syntax — word classes
    "noun":        "a.f2faf2f1113e",
    "verb":        "a.b512ddf18cfe",
    "adjective":   "a.d807ae6a5209",
    "adverb":      "a.1a6072781ed3",
    "preposition": "a.4b7be2c3460e",
    "determiner":  "a.e64800081c30",
    "pronoun":     "a.02de720d30a7",
    # Semantics internals — sense relations
    "synonym":      "a.9f2d9601b324",
    "antonym":      "a.2e0dd0bef137",
    "homonym":      "a.ed09e56f681c",
    "polysemy":     "a.8020916aa543",
    "hypernym":     "a.4e8db3c92d79",
    "hyponym":      "a.5a259864d3ff",
    "semantic field": "a.e219d34fdca9",
    # Semantics internals — formal concepts
    "prototype":         "a.c18462a35a7a",
    "connotation":       "a.2bc63a3ecee5",
    "denotation":        "a.5d9cbac84aee",
    "compositionality":  "a.3f502470855d",
    "scope":             "a.31a1fd140be4",
    "quantifier":        "a.c9a5eb8d391a",
    "predicate logic":   "a.predicate_logic.74b97bc6fe",
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
- Be precise about direction: if A is part of B, write A part_of B (not B contains A unless B actually contains A).
- Return ONLY a valid JSON array, no explanation, no markdown.

Example format:
[
  {{"subject": "vowel", "predicate": "is_a", "object": "phoneme", "confidence": 0.98}},
  {{"subject": "vowel", "predicate": "distinct_from", "object": "consonant", "confidence": 0.97}}
]

Generate 15-20 high-quality relations for: "{concept}"
Focus on: what it is_a, what it is part_of, what it contains, what it requires,
what it is distinct_from, how it relates to closely adjacent concepts.
Prioritise specific semantic edges over generic related_to.

JSON array:"""

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
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  [LLM error: {e}]")
        return []

def resolve_anchor(name: str, conn: sqlite3.Connection) -> str | None:
    if name in PASS3_ANCHORS:
        return PASS3_ANCHORS[name]
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
            "domain_tags":   "linguistics,llm_inferred",
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
        _stamp_sem_domain(conn, touched, "linguistics")
        print(f"Stamped sem_domain='linguistics' on {len(touched)} anchors.")
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
    print(f"\nLinguistics Pass 3 — {mode}")
    print(f"Model: {MODEL} | Concepts: {len(PASS3_ANCHORS)}")
    print("=" * 60)

    total_proposed = total_written = 0
    for concept, anchor_id in PASS3_ANCHORS.items():
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
        print(f"\nNext: python3 llm_ingest_ling_pass3.py --review")
        print(f"      python3 llm_ingest_ling_pass3.py --promote")
    else:
        print("DRY RUN — nothing written. Re-run with --commit to write.")

if __name__ == "__main__":
    main()
