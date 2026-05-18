#!/usr/bin/env python3
"""
llm_ingest_philosophy.py — Philosophy Pass 1: core branches and concepts.

46 concepts spanning core branches, foundational concepts, ethical theories,
epistemological schools, and metaphysical positions.

Same HITL pipeline: --commit → apply_philosophy_review.py → --promote
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
parser.add_argument("--promote", action="store_true")
args = parser.parse_args()
if args.commit:
    args.dry_run = False

PASS1_ANCHORS = {
    # Core branches
    "philosophy":               "a.225ab6cdf797",
    "ethics":                   "a.0fdfc5af25d6",
    "epistemology":             "a.ac35d7ef6504",
    "metaphysics":              "a.872bd6006ce4",
    "logic":                    "a.c3d3c17b4ca7",
    "ontology":                 "a.952859ec92e9",
    "aesthetics":               "a.8bade7ce72c9",
    "political philosophy":     "a.a5a3a1fac3a3",
    "philosophy of mind":       "a.e0e3307bdf4e",
    "philosophy of language":   "a.0c08a5b6f3cb",
    "philosophy of science":    "a.48e5455f90be",
    # Foundational concepts
    "consciousness":            "a.2a28941e264e",
    "free will":                "a.49ea49375ee3",
    "determinism":              "a.d9e06d702c06",
    "morality":                 "a.3a86a7b9351d",
    "justice":                  "a.9345b4e98397",
    "truth":                    "a.59d42c504c10",
    "knowledge":                "a.a542e9b744be",
    "belief":                   "a.34f1fb185a8b",
    "reasoning":                "a.4db0062a5ec0",
    "argument":                 "a.03cde060e90a",
    "inference":                "a.00b8744e1a3f",
    "causation":                "a.6da2aeeda18a",
    "identity":                 "a.ff483d1ff591",
    "existence":                "a.240caee810f9",
    "reality":                  "a.99d9a11fa5a7",
    "perception":               "a.4596647ea31b",
    "intuition":                "a.ca3f0c60adf0",
    "critical thinking":        "a.20784bf09c9f",
    # Ethical theories
    "utilitarianism":           "a.51000473129a",
    "deontology":               "a.c789a84babe8",
    "virtue ethics":            "a.d0bdeb305c74",
    "moral realism":            "a.3d42681c721f",
    "relativism":               "a.c642b16f9a7b",
    # Epistemological schools
    "rationalism":              "a.17b1d54949c2",
    "empiricism":               "a.dd2cb9dec42e",
    "pragmatism":               "a.04e4bd580208",
    "skepticism":               "a.d67e7f836fd3",
    # Metaphysical positions
    "dualism":                  "a.2b578dff6529",
    "materialism":              "a.eae4f0f716a2",
    "idealism":                 "a.09dee14c86dc",
    "naturalism":               "a.e751d450efca",
    # Movements
    "existentialism":           "a.e80f595a453c",
    "phenomenology":            "a.5c0d32b2d506",
    "analytic philosophy":      "a.cedf550c3ee6",
    "continental philosophy":   "a.e22f899fbb9c",
}

PREDICATES = [
    "is_a", "part_of", "contains", "related_to", "derived_from",
    "enables", "requires", "used_for", "distinct_from", "co_occurs_with"
]

def ask_llama(concept: str) -> list[dict]:
    prompt = f"""You are a philosophy knowledge engineer. Generate accurate structured relations about the philosophical concept "{concept}".

Rules:
- Only state well-established philosophical facts.
- Use only these predicates: is_a, part_of, contains, requires, enables, used_for, derived_from, distinct_from
- Both subject and object must be real philosophical, logical, or psychological concepts (single nouns or short noun phrases).
- Be precise about direction: if A is part of B, write A part_of B.
- Subject must stay closely related to "{concept}" — do not drift.
- Return ONLY a valid JSON array, no explanation, no markdown, no preamble.

Example format:
[
  {{"subject": "ethics", "predicate": "is_a", "object": "branch of philosophy", "confidence": 0.99}},
  {{"subject": "ethics", "predicate": "contains", "object": "moral theory", "confidence": 0.95}}
]

Generate 10-15 high-quality relations for: "{concept}"
Focus on: what it is_a, what it is part_of, what it contains, what it requires,
what it enables, what it is distinct_from.
Structural and conceptual edges only — no co_occurs_with, no related_to.

JSON array:"""

    for attempt in range(3):
        try:
            r = requests.post(OLLAMA_URL, json={
                "model": MODEL, "prompt": prompt, "stream": False,
                "options": {"temperature": 0.3, "num_predict": 800}
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
    seen  = set()
    for r in raw_rels:
        subj = str(r.get("subject", "")).strip().lower()
        pred = str(r.get("predicate", "")).strip().lower()
        obj  = str(r.get("object", "")).strip().lower()
        conf = float(r.get("confidence", 0.7))
        if not subj or not pred or not obj:
            continue
        if pred not in PREDICATES:
            continue
        if pred in ("co_occurs_with", "related_to"):
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
            "domain_tags":   "philosophy,llm_inferred",
            "edge_type":     "semantic",
            "confidence":    round(conf, 3),
            "source_model":  SOURCE,
            "generation_ts": time.time(),
            "subject_name":  subj,
            "object_name":   obj,
        })
    return valid

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
    pass1_ids = set(PASS1_ANCHORS.values())
    approved = [r for r in approved if r[1] in pass1_ids]
    if not approved:
        print("No approved Pass 1 philosophy relations to promote.")
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
        _stamp_sem_domain(conn, touched, "philosophy")
        print(f"Stamped sem_domain='philosophy' on {len(touched)} anchors.")
    conn.commit()
    print(f"Promoted {promoted} new relations to Tier 1.")
    print("(Duplicates skipped.)")

def main():
    conn = sqlite3.connect(DB_PATH)
    if args.promote:
        run_promote(conn)
        return

    mode = "DRY RUN" if args.dry_run else "COMMIT"
    print(f"\nPhilosophy Pass 1 — {mode}")
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
        print(f"\nNext: apply_philosophy_review.py → --promote")
    else:
        print("DRY RUN — nothing written. Re-run with --commit to write.")

if __name__ == "__main__":
    main()
