#!/usr/bin/env python3
"""
llm_ingest_cs_ai_depth.py — CS/AI Pass 2: sub-field depth concepts.

75 concepts spanning ML training mechanics, deep learning architectures,
NLP, CS algorithms, systems, AI safety/ethics, and theoretical CS.

Same HITL pipeline: --commit → apply_cs_ai_depth_review.py → --promote
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
    # ML training mechanics
    "training data":            "a.training_data.56eabe5e6f",
    "test data":                "a.test_data.916f0027a5",
    "loss function":            "a.loss_function.2f0b4053ab",
    "activation function":      "a.activation_function.82e0526f8b",
    "weight":                   "a.7edabf994b76",
    "bias":                     "a.1603f79f250b",
    "epoch":                    "a.3388c74e6308",
    "batch normalization":      "a.batch_normalization.91ed52d0a5",
    "dropout":                  "a.dropout.1b4c0a293b",
    "embedding":                "a.embedding.aa580156f3",
    "transfer learning":        "a.fdfb376a7709",
    "fine-tuning":              "a.945093eb10cc",
    "data augmentation":        "a.data_augmentation.31ba57f382",
    "hyperparameter":           "a.74e0a8e1e367",
    "cross-validation":         "a.caa71766e30e",
    "precision":                "a.e2794d8f1271",
    "recall":                   "a.a05550b785fe",
    "f1 score":                 "a.f1_score.632d7febde",
    "confusion matrix":         "a.confusion_matrix.5279a9ff3f",
    # Deep learning architectures
    "autoencoder":              "a.c40dd22f752b",
    "variational autoencoder":  "a.variational_autoenco.703cd28ba7",
    "generative adversarial network": "a.309f981b8f46",
    "encoder":                  "a.encoder.9ff94c3eb0",
    "decoder":                  "a.decoder.c9fbd9152b",
    "latent space":             "a.latent_space.ae8f9598ba",
    "pooling":                  "a.4abcef116566",
    "convolution":              "a.a9595c1c24c3",
    "long short-term memory":   "a.2b6026548f8b",
    "self-attention":           "a.b5dc07c4089f",
    "feedforward network":      "a.feedforward_network.ae8265d442",
    # NLP depth
    "tokenization":             "a.ab5666a35077",
    "word embedding":           "a.word_embedding.e358e7080e",
    "language model":           "a.language_model.5eff592cc4",
    "named entity recognition": "a.named_entity_recogni.12f294ad9d",
    "sentiment analysis":       "a.sentiment_analysis.56acd9373b",
    "text classification":      "a.text_classification.744271657b",
    "machine translation":      "a.machine_translation.e9a83351be",
    "question answering":       "a.question_answering.70647950c4",
    "summarization":            "a.a4157ff94f0e",
    "parsing":                  "a.dbc77665f51d",
    # CS algorithms
    "sorting algorithm":        "a.sorting_algorithm.70a561284c",
    "graph algorithm":          "a.graph_algorithm.9f576571b8",
    "dynamic programming":      "a.dynamic_programming.4f17182543",
    "binary search":            "a.binary_search.090ae27620",
    "breadth-first search":     "a.3fac0cca2582",
    "depth-first search":       "a.04b07d4cd90b",
    "hash function":            "a.hash_function.04ce4af53a",
    "tree":                     "a.c0af77cf8294",
    "graph":                    "a.f8b0b924ebd7",
    "linked list":              "a.4800b23fa1dc",
    "stack":                    "a.fac2a47adace",
    "queue":                    "a.a9d1cbf71942",
    # Systems depth
    "concurrency":              "a.172452990956",
    "thread":                   "a.dc127f5d2483",
    "process":                  "a.5075140835d0",
    "memory management":        "a.memory_management.f6bb6a6d43",
    "cache":                    "a.0fea6a13c52b",
    "api":                      "a.8a5da52ed126",
    "microservice":             "a.c38d2554a470",
    "containerization":         "a.a8910baa93be",
    "version control":          "a.version_control.c87a5566f5",
    # AI safety/ethics
    "ai alignment":             "a.ai_alignment.4d2da7c67b",
    "fairness":                 "a.b9bf62510659",
    "explainability":           "a.3695b7b7b31f",
    "interpretability":         "a.a45d53a18a6a",
    "adversarial attack":       "a.adversarial_attack.8dd267ad57",
    "robustness":               "a.d234403d7859",
    "generalization":           "a.f70cf526b32d",
    # Theoretical CS
    "turing machine":           "a.turing_machine.b1372eb0d0",
    "halting problem":          "a.halting_problem.afbd22fcfe",
    "np-complete":              "a.6b5da11fc20a",
    "np hard":                  "a.9cb6c44a6351",
    "formal language":          "a.formal_language.eb1b1556d7",
    "automaton":                "a.e0aed486f3d9",
    "lambda calculus":          "a.7360b9ee1fc2",
}

PREDICATES = [
    "is_a", "part_of", "contains", "related_to", "derived_from",
    "enables", "requires", "used_for", "distinct_from", "co_occurs_with"
]

def ask_llama(concept: str) -> list[dict]:
    prompt = f"""You are a computer science and AI knowledge engineer. Generate accurate structured relations about the concept "{concept}".

Rules:
- Only state well-established facts in computer science, AI, or machine learning.
- Use only these predicates: is_a, part_of, contains, requires, enables, used_for, derived_from, distinct_from
- Both subject and object must be real CS, AI, or mathematical concepts (single nouns or short noun phrases).
- Be precise about direction: if A is part of B, write A part_of B.
- Subject must stay closely related to "{concept}" — do not drift.
- Return ONLY a valid JSON array, no explanation, no markdown, no preamble.

Example format:
[
  {{"subject": "dropout", "predicate": "is_a", "object": "regularization", "confidence": 0.99}},
  {{"subject": "dropout", "predicate": "used_for", "object": "overfitting", "confidence": 0.95}}
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
            "domain_tags":   "cs,ai,llm_inferred",
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
        print("No approved CS/AI Pass 2 relations to promote.")
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
        _stamp_sem_domain(conn, touched, "computer science")
        print(f"Stamped sem_domain='computer science' on {len(touched)} anchors.")
    conn.commit()
    print(f"Promoted {promoted} new relations to Tier 1.")
    print("(Duplicates skipped.)")

def main():
    conn = sqlite3.connect(DB_PATH)
    if args.promote:
        run_promote(conn)
        return

    mode = "DRY RUN" if args.dry_run else "COMMIT"
    print(f"\nCS/AI Pass 2 (depth) — {mode}")
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
        print(f"\nNext: apply_cs_ai_depth_review.py → --promote")
    else:
        print("DRY RUN — nothing written. Re-run with --commit to write.")

if __name__ == "__main__":
    main()
