#!/usr/bin/env python3
"""
llm_ingest_psychology_pass3.py — Psychology Pass 3: sub-sub-field internals.

53 concepts spanning cognitive internals, emotion mechanisms, social phenomena,
clinical constructs, psychoanalytic concepts, and neuropsychological internals.

Same HITL pipeline: --commit → apply_psychology_pass3_review.py → --promote
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

PASS3_ANCHORS = {
    # Cognitive internals
    "encoding":               "a.84bea1f0fd2c",
    "retrieval":              "a.0868818ce8ef",
    "recall":                 "a.a05550b785fe",
    "recognition":            "a.0c6a50fbc0c4",
    "inhibitory control":     "a.568f7e121816",
    "cognitive flexibility":  "a.8fbf5e98b105",
    "planning":               "a.98ded198ba3a",
    # Emotion internals
    "emotional memory":       "a.12b30e715795",
    "emotional arousal":      "a.acc97a254da0",
    "affect":                 "a.2a9302012902",
    "mood":                   "a.dc27c0e30cac",
    "alexithymia":            "a.2f318ee67532",
    "emotional intelligence": "a.91d6ec14ecd1",
    "fight or flight":        "a.c251e37feaa6",
    "autonomic nervous system":"a.9ccff76e617d",
    "cortisol":               "a.f375dc40398d",
    "cognitive appraisal":    "a.fdf8bb6e4302",
    "reappraisal":            "a.fbd324a0dc3a",
    "suppression":            "a.584fbb07a4cd",
    "emotion regulation":     "a.2edfe6813227",
    # Social internals
    "stereotype":             "a.a31b2934b959",
    "implicit bias":          "a.implicit_bias.8d98f5e98d",
    "social comparison":      "a.facc23643682",
    "bystander effect":       "a.42b836176f4d",
    "groupthink":             "a.8b4145709310",
    "social facilitation":    "a.25ac7e0120fc",
    "locus of control":       "a.afdca2443aad",
    "attribution theory":     "a.80764baef8b2",
    # Clinical internals
    "rumination":             "a.24f6e2f9ef91",
    "avoidance":              "a.d59276839b5f",
    "hypervigilance":         "a.9211df6d5d20",
    "learned helplessness":   "a.b8ce64c2a43f",
    "cognitive restructuring":"a.0f97e88620ad",
    "exposure":               "a.b1b57b87d3b0",
    "self-awareness":         "a.9724aed14cdd",
    "self-regulation":        "a.a8fd562a5ccd",
    "self-control":           "a.2e6e3562d9db",
    # Psychoanalytic
    "repression":             "a.51437305e3c4",
    "projection":             "a.dfa555786d19",
    "defence mechanism":      "a.ca41bc00ff40",
    "unconscious":            "a.0812a83eaba9",
    "free association":       "a.e8166aff874e",
    "transference":           "a.01f9e18986f1",
    "insight":                "a.a463c4285602",
    "catharsis":              "a.1f132a059b5c",
    # Developmental internals
    "insecure attachment":    "a.c54ef15fe219",
    "theory of mind":         "a.7bf27cd504dc",
    "moral development":      "a.40652904ca2d",
    # Learning internals
    "reinforcement schedule": "a.6b2a8855151b",
    # Neuropsych internals
    "amygdala hijack":        "a.67edaede23ec",
    "neurogenesis":           "a.44a98f57f366",
    "synaptic pruning":       "a.6a487af0ecee",
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
  {{"subject": "rumination", "predicate": "is_a", "object": "cognitive process", "confidence": 0.99}},
  {{"subject": "rumination", "predicate": "enables", "object": "depression", "confidence": 0.92}}
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
    if name in PASS3_ANCHORS:
        return PASS3_ANCHORS[name]
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
    pass3_ids = set(PASS3_ANCHORS.values())
    approved = [r for r in approved if r[1] in pass3_ids]
    if not approved:
        print("No approved Pass 3 psychology relations to promote.")
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
    print(f"\nPsychology Pass 3 (narrow) — {mode}")
    print(f"Model: {MODEL} | Concepts: {len(PASS3_ANCHORS)}")
    print("=" * 60)

    total_proposed = total_written = 0
    for i, (concept, anchor_id) in enumerate(PASS3_ANCHORS.items()):
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
        print(f"\nNext: apply_psychology_pass3_review.py → --promote")
    else:
        print("DRY RUN — nothing written. Re-run with --commit to write.")

if __name__ == "__main__":
    main()
