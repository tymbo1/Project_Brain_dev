#!/usr/bin/env python3
"""
llm_ingest_philosophy_pass3.py — Philosophy Pass 3: sub-sub-field internals.

63 concepts spanning ethics internals, epistemology internals, metaphysics
internals, philosophy of mind internals, logic internals, political philosophy
internals, philosophy of science internals, and existentialism internals.

Same HITL pipeline: --commit → apply_philosophy_pass3_review.py → --promote
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
    # Ethics internals
    "moral luck":               "a.c0d303d0a975",
    "supererogation":           "a.7bc841fec21e",
    "moral status":             "a.e7f3a85f624b",
    "contractualism":           "a.ef4aa24a1cb3",
    "divine command theory":    "a.9c1ffd69aa38",
    "natural law":              "a.05ce2f1e6515",
    "expressivism":             "a.ecc89235942b",
    "moral objectivism":        "a.e10c89b63070",
    "principle of double effect": "a.a23e66135061",
    # Epistemology internals
    "epistemic virtue":         "a.d88342d8cba4",
    "gettier problem":          "a.4609447597b4",
    "problem of induction":     "a.6f7036060c1c",
    "underdetermination":       "a.9afd8a0f6b91",
    # Metaphysics internals
    "tropes":                   "a.f0c734bcbbb5",
    "abstract objects":         "a.abstract_objects.917f928a82",
    "concrete objects":         "a.4aa62e8f5905",
    "bundle theory":            "a.38b9a076d15e",
    "supervenience":            "a.2e4ca80ef643",
    "grounding":                "a.5817f45af73e",
    "mereology":                "a.3693d15ced5a",
    "vagueness":                "a.19cc51152027",
    "presentism":               "a.ffd0c3607946",
    "eternalism":               "a.5bebf5c85945",
    "four dimensionalism":      "a.0ab7801fe1ba",
    "haecceity":                "a.3e43c83b1e10",
    "truthmaker":               "a.3e09d7c0bb0b",
    # Philosophy of mind internals
    "type identity":            "a.type_identity.84361b0975",
    "anomalous monism":         "a.825b5dfcd760",
    "global workspace theory":  "a.afae6234066f",
    "integrated information theory": "a.ff9c78213dda",
    "biological naturalism":    "a.91b770980a54",
    "representationalism":      "a.3f1321208a83",
    "naive realism":            "a.8277b164640c",
    # Logic internals
    "modus ponens":             "a.b4a84fdc088e",
    "modus tollens":            "a.6e9232d7838c",
    "contradiction":            "a.53ad53384a7a",
    "tautology":                "a.1f5e06e6b663",
    "quantifier":               "a.c9a5eb8d391a",
    "modal logic":              "a.modal_logic.b09a9ddb9d",
    "deontic logic":            "a.52c039287c41",
    "truth table":              "a.truth_table.c93a8f556f",
    "disjunctive syllogism":    "a.23e9e5008339",
    # Political philosophy internals
    "state of nature":          "a.387bc16862fc",
    "veil of ignorance":        "a.ba5c6ca9c83c",
    "original position":        "a.original_position.6c503ce06b",
    "civil disobedience":       "a.280b8c09f49f",
    "republicanism":            "a.b2a5eab20888",
    "libertarianism":           "a.340989694765",
    "egalitarianism":           "a.9b14bb3c1f3b",
    "anarchism":                "a.d8e131db45b7",
    "civic virtue":             "a.3a22caaeedb9",
    "common good":              "a.a50ee9b9f85e",
    # Philosophy of science internals
    "demarcation problem":      "a.d2a728259cd6",
    "scientific realism":       "a.d3377334878f",
    "instrumentalism":          "a.bbb1f56a7ffc",
    "antirealism":              "a.ffea2b75756a",
    "verisimilitude":           "a.c190cdd69ffa",
    "holism":                   "a.cbfaa2533875",
    # Existentialism internals
    "thrownness":               "a.68479d409a2b",
    "facticity":                "a.1314c81489ef",
    "anguish":                  "a.93bb0bc15391",
    "perdurantism":             "a.4c2038fd5a59",
    "being in the world":       "a.85b97b866583",
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
  {{"subject": "modus ponens", "predicate": "is_a", "object": "inference rule", "confidence": 0.99}},
  {{"subject": "modus ponens", "predicate": "part_of", "object": "deductive reasoning", "confidence": 0.95}}
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
    pass3_ids = set(PASS3_ANCHORS.values())
    approved = [r for r in approved if r[1] in pass3_ids]
    if not approved:
        print("No approved Pass 3 philosophy relations to promote.")
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
    print(f"\nPhilosophy Pass 3 (narrow) — {mode}")
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
        print(f"\nNext: apply_philosophy_pass3_review.py → --promote")
    else:
        print("DRY RUN — nothing written. Re-run with --commit to write.")

if __name__ == "__main__":
    main()
