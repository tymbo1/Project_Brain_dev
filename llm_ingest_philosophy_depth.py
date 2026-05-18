#!/usr/bin/env python3
"""
llm_ingest_philosophy_depth.py — Philosophy Pass 2: sub-field depth concepts.

74 concepts spanning ethics, epistemology, metaphysics, philosophy of mind,
logic, political philosophy, philosophy of science, and existentialism.

Same HITL pipeline: --commit → apply_philosophy_depth_review.py → --promote
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

PASS2_ANCHORS = {
    # Ethics depth
    "consequentialism":         "a.13200be9eb45",
    "metaethics":               "a.7fcf7763d827",
    "normative ethics":         "a.43c6134e071d",
    "applied ethics":           "a.f06b5f67bae5",
    "bioethics":                "a.060ade24546a",
    "moral agency":             "a.bc29a29d918e",
    "moral responsibility":     "a.eef369fe44f4",
    "categorical imperative":   "a.713c036589a5",
    "moral psychology":         "a.bba6056baec0",
    "moral duty":               "a.1b7618d4006d",
    # Epistemology depth
    "justification":            "a.6d3d45533e33",
    "propositional knowledge":  "a.propositional_knowle.94e24c7494",
    "foundationalism":          "a.efa431a6c281",
    "coherentism":              "a.6e05c0604af7",
    "reliabilism":              "a.71834b064195",
    "testimony":                "a.89e4975b08f5",
    "internalism":              "a.30d4b8f80582",
    "externalism":              "a.849a67a71f46",
    # Metaphysics depth
    "substance":                "a.64518fd155c1",
    "essence":                  "a.89c525ef0f3c",
    "universals":               "a.185a050d8b3b",
    "particulars":              "a.b21411d1cec4",
    "possible worlds":          "a.1071031989f3",
    "modality":                 "a.67c703ae1e2b",
    "necessity":                "a.7818c68f30a5",
    "contingency":              "a.9f3874c32763",
    "personal identity":        "a.93118d9cdf4e",
    "mind-body problem":        "a.292cea52b307",
    "property dualism":         "a.0cb54da37ee8",
    # Philosophy of mind depth
    "qualia":                   "a.ab420badd2c4",
    "intentionality":           "a.d605f4ca1ec0",
    "mental representation":    "a.4dc9e27d1c6c",
    "functionalism":            "a.12ed9ab662b0",
    "physicalism":              "a.d31f7c4dade8",
    "eliminativism":            "a.49763336f74c",
    "epiphenomenalism":         "a.f9f93a7c89c8",
    "hard problem of consciousness": "a.c284cec875ed",
    "phenomenal consciousness": "a.8e1e10c17765",
    # Logic depth
    "deductive reasoning":      "a.deductive_reasoning.58330d3553",
    "inductive reasoning":      "a.3068a4aafb2a",
    "abductive reasoning":      "a.abductive_reasoning.3577dd1684",
    "syllogism":                "a.e4cfe6f133e0",
    "validity":                 "a.3889f81b063d",
    "soundness":                "a.4c32b4f5c357",
    "fallacy":                  "a.5c325d8e7fe1",
    "propositional logic":      "a.propositional_logic.8757bcf702",
    "predicate logic":          "a.predicate_logic.74b97bc6fe",
    "formal logic":             "a.bbf413fdc15b",
    # Political philosophy depth
    "social contract":          "a.cbb0ebe3b510",
    "liberty":                  "a.cfe93922228f",
    "equality":                 "a.8d6af6957321",
    "rights":                   "a.27b371524f44",
    "sovereignty":              "a.cd318d443356",
    "legitimacy":               "a.1a47ae5e2df1",
    "authority":                "a.873e9c0b5018",
    "distributive justice":     "a.f4bdc51a7092",
    "liberalism":               "a.cbb6fe08009c",
    "communitarianism":         "a.be4d7587b931",
    # Philosophy of science depth
    "falsifiability":           "a.1404c611b213",
    "paradigm":                 "a.8d992b8e2e7a",
    "scientific method":        "a.c06695fcbf2b",
    "reductionism":             "a.e4e59728cc85",
    "emergence":                "a.f2a7289c9f47",
    "explanation":              "a.74d08617d74a",
    "theory":                   "a.af5443b3a2c1",
    # Existentialism / phenomenology depth
    "authenticity":             "a.5c5cb2903039",
    "bad faith":                "a.3fac97831528",
    "dasein":                   "a.18f49bb1dd41",
    "lifeworld":                "a.4961cae1bba6",
    "intersubjectivity":        "a.a1698f72d533",
    "being":                    "a.3f4cede61a59",
    "nothingness":              "a.692bbd607073",
    "absurdism":                "a.9aeae093ed2b",
    "nihilism":                 "a.8e47e96b8738",
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
  {{"subject": "qualia", "predicate": "is_a", "object": "subjective experience", "confidence": 0.99}},
  {{"subject": "qualia", "predicate": "part_of", "object": "consciousness", "confidence": 0.95}}
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
    if name in PASS2_ANCHORS:
        return PASS2_ANCHORS[name]
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
    pass2_ids = set(PASS2_ANCHORS.values())
    approved = [r for r in approved if r[1] in pass2_ids]
    if not approved:
        print("No approved Pass 2 philosophy relations to promote.")
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
    print(f"\nPhilosophy Pass 2 (depth) — {mode}")
    print(f"Model: {MODEL} | Concepts: {len(PASS2_ANCHORS)}")
    print("=" * 60)

    total_proposed = total_written = 0
    for i, (concept, anchor_id) in enumerate(PASS2_ANCHORS.items()):
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
        print(f"\nNext: apply_philosophy_depth_review.py → --promote")
    else:
        print("DRY RUN — nothing written. Re-run with --commit to write.")

if __name__ == "__main__":
    main()
