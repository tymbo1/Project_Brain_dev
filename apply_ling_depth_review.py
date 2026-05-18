#!/usr/bin/env python3
"""
apply_ling_depth_review.py — Apply GPT HITL decisions to relations_llm depth batch.

Strategy (from GPT review 2026-05-08):
  FIXES:   7 direction/predicate corrections before approval
  REJECTS: 5 specific wrong relations + contradictions + confidence < 0.35
  APPROVE: everything remaining

Run AFTER llm_ingest_ling_depth.py --commit, BEFORE --promote.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "resonance_v11.db"

def anchor_id(conn, canonical: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM anchors WHERE canonical = ?", (canonical.lower(),)
    ).fetchone()
    return row[0] if row else None

def rel_id(conn, subj: str, pred: str, obj: str) -> str | None:
    sid = anchor_id(conn, subj)
    oid = anchor_id(conn, obj)
    if not sid or not oid:
        return None
    row = conn.execute(
        "SELECT id FROM relations_llm WHERE subject_id=? AND predicate=? AND object_id=? AND reviewed=0",
        (sid, pred, oid)
    ).fetchone()
    return row[0] if row else None

def reject(conn, subj: str, pred: str, obj: str, reason: str = ""):
    rid = rel_id(conn, subj, pred, obj)
    if rid:
        conn.execute("UPDATE relations_llm SET reviewed=1, approved=0 WHERE id=?", (rid,))
        print(f"  REJECT  {subj} --{pred}--> {obj}{' | ' + reason if reason else ''}")
    else:
        print(f"  SKIP    {subj} --{pred}--> {obj} (not found or already reviewed)")

def fix_predicate(conn, subj: str, old_pred: str, obj: str, new_pred: str):
    rid = rel_id(conn, subj, old_pred, obj)
    if rid:
        conn.execute("UPDATE relations_llm SET predicate=? WHERE id=?", (new_pred, rid))
        print(f"  FIX     {subj} --{old_pred}--> {obj}  →  --{new_pred}-->")
    else:
        print(f"  SKIP    {subj} --{old_pred}--> {obj} (not found or already reviewed)")

def fix_object(conn, subj: str, pred: str, old_obj: str, new_obj: str):
    rid = rel_id(conn, subj, pred, old_obj)
    new_oid = anchor_id(conn, new_obj)
    if rid and new_oid:
        conn.execute("UPDATE relations_llm SET object_id=? WHERE id=?", (new_oid, rid))
        print(f"  FIX     {subj} --{pred}--> {old_obj}  →  --{pred}--> {new_obj}")
    else:
        print(f"  SKIP    {subj} --{pred}--> {old_obj} (not found or new anchor missing)")

def fix_swap(conn, subj: str, pred: str, obj: str):
    """Swap subject and object for a reversed relation."""
    sid = anchor_id(conn, subj)
    oid = anchor_id(conn, obj)
    if not sid or not oid:
        print(f"  SKIP    {subj} --{pred}--> {obj} (anchor not found)")
        return
    row = conn.execute(
        "SELECT id FROM relations_llm WHERE subject_id=? AND predicate=? AND object_id=? AND reviewed=0",
        (sid, pred, oid)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE relations_llm SET subject_id=?, object_id=? WHERE id=?",
            (oid, sid, row[0])
        )
        print(f"  FIX     {subj} --{pred}--> {obj}  →  {obj} --{pred}--> {subj}")
    else:
        print(f"  SKIP    {subj} --{pred}--> {obj} (not found)")

def main():
    conn = sqlite3.connect(DB_PATH)

    pending = conn.execute(
        "SELECT COUNT(*) FROM relations_llm WHERE reviewed=0"
    ).fetchone()[0]
    print(f"\nPending unreviewed relations: {pending}")
    print("=" * 60)

    # ── Step 1: Apply fixes ────────────────────────────────────────────────────
    print("\n[FIXES]")

    fix_swap(conn,        "pidgin",           "derived_from",  "creole language")
    fix_predicate(conn,   "clause",           "is_a",          "sentence",        "part_of")
    fix_swap(conn,        "illocutionary force", "contains",   "speech act")
    fix_predicate(conn,   "affix",            "contains",      "root",            "requires")
    fix_object(conn,      "entailment",       "part_of",       "pragmatics",      "semantics")
    fix_object(conn,      "prosody",          "part_of",       "phonetics",       "phonology")
    fix_predicate(conn,   "aphasia",          "enables",       "speech therapy",  "requires")

    conn.commit()

    # ── Step 2: Apply specific rejects ────────────────────────────────────────
    print("\n[SPECIFIC REJECTS]")

    reject(conn, "communication",  "requires",      "creole",                "factually wrong")
    reject(conn, "prosody",        "distinct_from", "phonology",             "prosody IS phonology")
    reject(conn, "dependency",     "requires",      "phrase structure grammar", "PSG is alternative, not prereq")
    reject(conn, "presupposition", "used_for",      "predictive modeling",   "ML term, not linguistics")
    reject(conn, "cohesion",       "distinct_from", "disjunction",           "wrong pairing")

    # Circular tense ↔ aspect containment
    reject(conn, "tense",          "contains",      "aspect",                "circular — keep distinct_from")
    reject(conn, "aspect",         "contains",      "tense",                 "circular")
    reject(conn, "aspect",         "part_of",       "tense",                 "circular")

    # Bilingualism contradiction
    reject(conn, "bilingualism",   "contains",      "monolingualism",        "contradicts distinct_from")

    # Additional Claude-flagged
    reject(conn, "cognate",        "contains",      "morphology",            "morphology is a field, not content")
    reject(conn, "genre",          "enables",       "characterization",      "confidence 0.4, vague")
    reject(conn, "syllable",       "part_of",       "phrase",                "wrong level — syllables are sub-word")
    reject(conn, "phrase",         "contains",      "syllable",              "wrong level — phrases are above-word")

    conn.commit()

    # ── Step 3: Reject all confidence < 0.35 ─────────────────────────────────
    print("\n[LOW CONFIDENCE REJECTS — confidence < 0.35]")
    low_conf = conn.execute("""
        SELECT r.id, a1.canonical, r.predicate, a2.canonical, r.confidence
        FROM relations_llm r
        JOIN anchors a1 ON r.subject_id = a1.id
        JOIN anchors a2 ON r.object_id = a2.id
        WHERE r.reviewed = 0 AND r.confidence < 0.35
    """).fetchall()

    for rid, subj, pred, obj, conf in low_conf:
        print(f"  REJECT  {subj} --{pred}--> {obj} [{conf}]")
        conn.execute("UPDATE relations_llm SET reviewed=1, approved=0 WHERE id=?", (rid,))

    conn.commit()

    # ── Step 4: Approve everything remaining ──────────────────────────────────
    print("\n[BULK APPROVE — remaining unreviewed]")
    result = conn.execute(
        "UPDATE relations_llm SET reviewed=1, approved=1 WHERE reviewed=0"
    )
    approved_count = result.rowcount
    conn.commit()
    print(f"  Approved: {approved_count} relations")

    # ── Summary ───────────────────────────────────────────────────────────────
    stats = conn.execute("""
        SELECT
            SUM(CASE WHEN reviewed=1 AND approved=1 THEN 1 ELSE 0 END) as approved,
            SUM(CASE WHEN reviewed=1 AND approved=0 THEN 1 ELSE 0 END) as rejected,
            SUM(CASE WHEN reviewed=0 THEN 1 ELSE 0 END) as pending
        FROM relations_llm
    """).fetchone()

    print(f"\n{'='*60}")
    print(f"Total approved: {stats[0]}")
    print(f"Total rejected: {stats[1]}")
    print(f"Still pending:  {stats[2]}")
    print(f"\nNext: python3 llm_ingest_ling_depth.py --promote")

if __name__ == "__main__":
    main()
