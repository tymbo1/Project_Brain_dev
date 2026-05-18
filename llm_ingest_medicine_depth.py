#!/usr/bin/env python3
"""
llm_ingest_medicine_depth.py — Medicine Pass 2: sub-field concepts.

67 concepts across 13 sub-domains. Same HITL pipeline:
    --commit → apply_medicine_review.py → --promote
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

PASS2_ANCHORS = {
    # Anatomy
    "organ":                "a.6892cf3e7696",
    "tissue":               "a.93aefb91c3e9",
    "cell":                 "a.8d27600b0cae",
    "nervous system":       "a.5c4aabf50162",
    "cardiovascular system":"a.cf06ced41f87",
    "respiratory system":   "a.21744d0b938b",
    "skeletal system":      "a.skeletal_system.90263071e6",
    "muscular system":      "a.ef13f703bd76",
    # Physiology
    "homeostasis":          "a.a0107b2d987d",
    "metabolism":           "a.6abb58b1b74c",
    "hormone":              "a.071122295bba",
    "respiration":          "a.eafb5011f1c9",
    "circulation":          "a.ab756884ead5",
    # Pathology
    "inflammation":         "a.4b91e35fe0c9",
    "tumor":                "a.4d3e3c81f138",
    "lesion":               "a.18e531ad4266",
    "necrosis":             "a.96987a060f10",
    "fibrosis":             "a.3b299a7aa8bf",
    # Pharmacology
    "drug":                 "a.05b2815ccf65",
    "receptor":             "a.3387e2e42b36",
    "pharmacokinetics":     "a.802a5f80f94c",
    "toxicity":             "a.5f219c361b93",
    "dosage":               "a.92860e8e7268",
    # Immunology
    "antibody":             "a.bd7fbdd6d4b0",
    "antigen":              "a.ad8b11766e0f",
    "vaccine":              "a.d4aac1a7c59a",
    "cytokine":             "a.c0fbf565f600",
    "lymphocyte":           "a.48e3560d9a25",
    "pathogen":             "a.c8a3bb61d4d2",
    # Surgery
    "anesthesia":           "a.4a4af971c8ac",
    "wound healing":        "a.cb936666d0f4",
    "transplant":           "a.2ce20861cdd6",
    "incision":             "a.fb566e7157c8",
    # Genetics
    "gene":                 "a.5971264e9294",
    "mutation":             "a.32a228ed0a19",
    "chromosome":           "a.ccd31bb0909e",
    "genetic disorder":     "a.2375e2de9d02",
    "genomics":             "a.e7d5b92c4f08",
    "epigenetics":          "a.132f58ced9ae",
    # Epidemiology
    "incidence":            "a.07fa41ea0d04",
    "prevalence":           "a.b1e40cef79f8",
    "risk factor":          "a.471bdbb48e9b",
    "mortality":            "a.c8aae83e10ed",
    "morbidity":            "a.82327ec0526a",
    # Neurology
    "neuron":               "a.a4d4b8da5f27",
    "synapse":              "a.e537bfa04fef",
    "neurotransmitter":     "a.b0c8b23b2cb7",
    "stroke":               "a.9624e8706573",
    "dementia":             "a.8b174e2c4b00",
    # Cardiology
    "heart":                "a.3189934774aa",
    "artery":               "a.94a60fd57525",
    "blood pressure":       "a.9d6fcd973c5f",
    "arrhythmia":           "a.b68dca981c86",
    "heart failure":        "a.741b7021508b",
    # Oncology
    "metastasis":           "a.bcd8845e2a26",
    "chemotherapy":         "a.03b87963df9a",
    "carcinoma":            "a.2a88f5b0e8db",
    "radiation therapy":    "a.c44855b096c8",
    "remission":            "a.3d0d17cfc507",
    # Microbiology
    "bacteria":             "a.c49441ae4992",
    "virus":                "a.326577fbe6d7",
    "antibiotic":           "a.d781f18930a8",
    "infection":            "a.75a4125cbf37",
    # Psychiatry
    "anxiety":              "a.d3af37c0435a",
    "depression":           "a.28c5f9ffd175",
    "psychosis":            "a.026b588a5ca8",
    "schizophrenia":        "a.182a1e726ac1",
}

PREDICATES = [
    "is_a", "part_of", "contains", "related_to", "derived_from",
    "enables", "requires", "used_for", "distinct_from", "co_occurs_with"
]

def ask_llama(concept: str) -> list[dict]:
    prompt = f"""You are a medical knowledge engineer. Generate accurate structured relations about the medical/biological concept "{concept}".

Rules:
- Only state well-established medical or biological facts.
- Use only these predicates: is_a, part_of, contains, related_to, derived_from, enables, requires, used_for, distinct_from, co_occurs_with
- Both subject and object must be real medical or biological concepts (single nouns or short noun phrases).
- Be precise about direction: if A is part of B, write A part_of B.
- Subject must stay closely related to "{concept}" — do not drift to distant concepts.
- Return ONLY a valid JSON array, no explanation, no markdown, no preamble.

Example format:
[
  {{"subject": "inflammation", "predicate": "is_a", "object": "immune response", "confidence": 0.97}},
  {{"subject": "inflammation", "predicate": "requires", "object": "cytokine", "confidence": 0.95}}
]

Generate 12-18 high-quality relations for: "{concept}"
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
    if name in PASS2_ANCHORS:
        return PASS2_ANCHORS[name]
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
    print(f"\nMedicine Pass 2 (depth) — {mode}")
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
        print(f"\nNext: export to medicine_depth_review.md → GPT HITL → apply_medicine_review.py → --promote")
    else:
        print("DRY RUN — nothing written. Re-run with --commit to write.")

if __name__ == "__main__":
    main()
