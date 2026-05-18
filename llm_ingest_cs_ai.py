#!/usr/bin/env python3
"""
llm_ingest_cs_ai.py — CS/AI Pass 1: core concepts.

67 concepts spanning core AI/ML, CS fundamentals, AI sub-fields,
knowledge representation, and theoretical CS.

Same HITL pipeline: --commit → apply_cs_ai_review.py → --promote
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
    # Core CS/AI
    "artificial intelligence":      "a.2dd0692ccbad",
    "machine learning":             "a.e04d1bcee667",
    "deep learning":                "a.de0fe23816b1",
    "neural network":               "a.neural_network.4460d5ad79",
    "algorithm":                    "a.ed469618898d",
    "data structure":               "a.data_structure.e6a8fc96e5",
    "computer science":             "a.computer_science.13a5670cc7",
    "software engineering":         "a.software_engineering.0c7d337a15",
    "programming":                  "a.7e73d06707f5",
    "computation":                  "a.b57dcd1ef514",
    "complexity theory":            "a.complexity_theory.b8381345a1",
    "information theory":           "a.b0f01c409733",
    "computer vision":              "a.computer_vision.c1c77e5bab",
    "natural language processing":  "a.natural_language_pro.dc453b2d95",
    "robotics":                     "a.9cafaa453dfe",
    "automation":                   "a.205d64ff9b58",
    # ML sub-fields
    "supervised learning":          "a.09b8339c6541",
    "unsupervised learning":        "a.f6db6e62bd4e",
    "reinforcement learning":       "a.4ae35cd73aaa",
    "generative model":             "a.generative_model.69f7c7e48e",
    "transformer":                  "a.5fe05153b777",
    "attention mechanism":          "a.4e099738fb66",
    "backpropagation":              "a.backpropagation.2bd063e3ec",
    "gradient descent":             "a.gradient_descent.4c36741a4c",
    "overfitting":                  "a.50d701f9016d",
    "regularization":               "a.04ef847b5e35",
    "feature extraction":           "a.14c22c352c5b",
    "dimensionality reduction":     "a.f6e62b21876e",
    "classification":               "a.63f69c7f9587",
    "regression":                   "a.c735ca28f98e",
    "clustering":                   "a.8a7bdba9c932",
    "decision tree":                "a.decision_tree.4f3bae063b",
    "random forest":                "a.random_forest.dc25bdcdac",
    "support vector machine":       "a.support_vector_machi.9ea1cd15b7",
    "convolutional neural network": "a.convolutional_neural.24f7d2384f",
    "recurrent neural network":     "a.recurrent_neural_net.5ba8e23f06",
    "large language models":        "a.6f7a8e153d77",
    # CS infrastructure
    "data science":                 "a.data_science.aa90a6f241",
    "database":                     "a.11e0eed8d369",
    "operating system":             "a.operating_system.f93f800502",
    "distributed system":           "a.distributed_system.6eb40ef5ad",
    "computer network":             "a.computer_network.0fb594f890",
    "cryptography":                 "a.e0d00b9f337d",
    "cybersecurity":                "a.b03a894e1017",
    "compiler":                     "a.87f75ce3f908",
    "virtual machine":              "a.virtual_machine.47831c0db3",
    "cloud computing":              "a.cloud_computing.81121f330a",
    "parallel computing":           "a.parallel_computing.d05e059eae",
    # AI reasoning/knowledge
    "knowledge representation":     "a.knowledge_representa.815bcc346c",
    "inference engine":             "a.inference_engine.fe50d40b5d",
    "expert system":                "a.expert_system.cfe72cc013",
    "planning":                     "a.98ded198ba3a",
    "search algorithm":             "a.search_algorithm.68716f76a1",
    "heuristic":                    "a.4163cf922db5",
    "optimization":                 "a.333aba968c38",
    "bayesian network":             "a.bayesian_network.0e0ca6fd0a",
    "markov model":                 "a.26e25222a631",
    "probabilistic model":          "a.probabilistic_model.1305c6493e",
    # AI concepts
    "artificial general intelligence": "a.artificial_general_i.fb4f3badf2",
    "turing test":                  "a.f87d41925c56",
    "embodied cognition":           "a.ca04e24cc869",
    "swarm intelligence":           "a.swarm_intelligence.3bdcb19bc2",
    "evolutionary algorithm":       "a.evolutionary_algorit.cbd81949c9",
    "semantic network":             "a.semantic_network.7233d7fe69",
    "ontology":                     "a.952859ec92e9",
    "knowledge graph":              "a.knowledge_graph.0071e48609",
    "weak ai thesis":               "a.448deb7158c8",
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
  {{"subject": "deep learning", "predicate": "is_a", "object": "machine learning", "confidence": 0.99}},
  {{"subject": "deep learning", "predicate": "requires", "object": "neural network", "confidence": 0.97}}
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
    pass1_ids = set(PASS1_ANCHORS.values())
    approved = [r for r in approved if r[1] in pass1_ids]
    if not approved:
        print("No approved CS/AI Pass 1 relations to promote.")
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
    print(f"\nCS/AI Pass 1 — {mode}")
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
        print(f"\nNext: apply_cs_ai_review.py → --promote")
    else:
        print("DRY RUN — nothing written. Re-run with --commit to write.")

if __name__ == "__main__":
    main()
