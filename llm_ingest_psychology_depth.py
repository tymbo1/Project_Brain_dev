#!/usr/bin/env python3
"""
llm_ingest_psychology_depth.py — Psychology Pass 2: sub-field depth concepts.

51 concepts across cognitive, developmental, social, clinical, emotion,
personality, learning/memory, and neuropsychology sub-domains.

Same HITL pipeline: --commit → apply_psychology_depth_review.py → --promote
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
    # Cognitive depth
    "working memory":              "a.95a85ec472ca",
    "executive function":          "a.01886592de7a",
    "metacognition":               "a.1e54eb95db8d",
    "decision making":             "a.dc6867b53718",
    "problem solving":             "a.problem_solving.682c9ffb34",
    "cognitive load":              "a.cognitive_load.56f027a85b",
    "mental imagery":              "a.2bbe0b7cba74",
    "attention span":              "a.26266831cbb9",
    # Developmental
    "attachment":                  "a.44290cefe429",
    "identity":                    "a.ff483d1ff591",
    "adolescence":                 "a.303e029e7c45",
    "attachment theory":           "a.15d2db04087b",
    "object permanence":           "a.object_permanence.c15aa68203",
    "zone of proximal development":"a.52f25072f45b",
    "ego":                         "a.37349f07c958",
    # Social
    "conformity":                  "a.511b56193573",
    "obedience":                   "a.412030ff8772",
    "prejudice":                   "a.2bc5f6de5b84",
    "attribution":                 "a.ed90ee36016a",
    "group dynamics":              "a.group_dynamics.c3dfbfb64b",
    "persuasion":                  "a.d75035dd6127",
    "social norm":                 "a.4270f00a60bd",
    "cognitive dissonance":        "a.b493705968c0",
    # Clinical
    "exposure therapy":            "a.be955e5ab194",
    "psychoanalysis":              "a.537c729416a1",
    "dialectical behavior therapy":"a.086f3d95a3a3",
    "mindfulness":                 "a.2d5e4349b75d",
    "resilience":                  "a.c3262ebffab7",
    "dissociation":                "a.a25211728896",
    "emotional regulation":        "a.e7b1645c1de4",
    # Emotion
    "fear":                        "a.eb88d7636980",
    "anger":                       "a.9d0702e9cfd2",
    "joy":                         "a.c2c8e798aecb",
    "sadness":                     "a.b8ad21285e57",
    "self-efficacy":               "a.face0e918767",
    "intrinsic motivation":        "a.intrinsic_motivation.658475035c",
    # Personality
    "introversion":                "a.a9d310b3c33e",
    "extraversion":                "a.340e554e918a",
    "neuroticism":                 "a.b70abc6b6bf1",
    "temperament":                 "a.239552e7c96d",
    # Learning/memory
    "classical conditioning":      "a.cde095d647db",
    "operant conditioning":        "a.0652c75b69d7",
    "long-term potentiation":      "a.40a481cfb7f4",
    "memory consolidation":        "a.03425707b2c8",
    "extinction":                  "a.be242ab658e6",
    "reinforcement":               "a.e2da6dcd7a6e",
    # Neuropsych
    "prefrontal cortex":           "a.134bf62216b9",
    "limbic system":               "a.618ee417cdbc",
    "amygdala":                    "a.094e8a87f06c",
    "frontal lobe":                "a.639d455f3fca",
}

PREDICATES = [
    "is_a", "part_of", "contains", "related_to", "derived_from",
    "enables", "requires", "used_for", "distinct_from", "co_occurs_with"
]

def ask_llama(concept: str) -> list[dict]:
    prompt = f"""You are a psychology knowledge engineer. Generate accurate structured relations about the psychological concept "{concept}".

Rules:
- Only state well-established psychological or neuroscientific facts.
- Use only these predicates: is_a, part_of, contains, requires, enables, used_for, derived_from, distinct_from
- Both subject and object must be real psychological or biological concepts (single nouns or short noun phrases).
- Be precise about direction: if A is part of B, write A part_of B.
- Subject must stay closely related to "{concept}" — do not drift.
- Return ONLY a valid JSON array, no explanation, no markdown, no preamble.

Example format:
[
  {{"subject": "working memory", "predicate": "is_a", "object": "cognitive process", "confidence": 0.99}},
  {{"subject": "working memory", "predicate": "requires", "object": "prefrontal cortex", "confidence": 0.95}}
]

Generate 10-15 high-quality relations for: "{concept}"
Focus on: what it is_a, what it is part_of, what it contains, what it requires,
what it enables, what it is distinct_from.
Structural and causal edges only — no co_occurs_with, no related_to.

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
            "domain_tags":   "psychology,llm_inferred",
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
        print("No approved Pass 2 psychology relations to promote.")
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
        _stamp_sem_domain(conn, touched, "psychology")
        print(f"Stamped sem_domain='psychology' on {len(touched)} anchors.")
    conn.commit()
    print(f"Promoted {promoted} new relations to Tier 1.")
    print("(Duplicates skipped.)")

def main():
    conn = sqlite3.connect(DB_PATH)
    if args.promote:
        run_promote(conn)
        return

    mode = "DRY RUN" if args.dry_run else "COMMIT"
    print(f"\nPsychology Pass 2 (depth) — {mode}")
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
        print(f"\nNext: export to psychology_depth_review.md → GPT HITL → apply_psychology_depth_review.py → --promote")
    else:
        print("DRY RUN — nothing written. Re-run with --commit to write.")

if __name__ == "__main__":
    main()
