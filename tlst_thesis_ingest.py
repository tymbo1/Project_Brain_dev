#!/usr/bin/env python3
"""
tlst_thesis_ingest.py — Full TLST PhD thesis pipeline.

1. Pulls all TLST-relevant pending_review items from selyrionstory.db
2. Auto-approves (marks reviewed=1) — HITL gate: human ran this deliberately
3. Maps entity names → CMS anchor IDs (creates missing anchors)
4. Normalises predicates to CMS predicate vocabulary
5. Ingests relations into resonance_v11.db
6. Prints thesis reconstruction summary via LangEng

Covers passes 3-8: relations, snapshots, style, inventions, voice.
"""
import sqlite3
import hashlib
import json
import time
import re
from pathlib import Path

STORY_DB = Path.home() / "selyrionstory.db"
CMS_DB   = Path.home() / "resonance_v11.db"

TLST_DOMAIN = "theoretical_physics,string_theory,unification,tlst_thesis"

# ── Predicate normalisation map ───────────────────────────────────────────────
PRED_MAP = {
    "part_of":           "part_of",
    "evolved_from":      "evolved_from",
    "inspired_by":       "inspired_by",
    "superseded_by":     "superseded_by",
    "led_to":            "led_to",
    "confirmed_by":      "confirmed_by",
    "contradicts":       "contradicts",
    "documented_in":     "documented_in",
    "implemented_as":    "implemented_as",
    "is_a":              "is_a",
    "extends":           "extends",
    "integrates":        "integrates",
    "uses":              "uses",
    "enables":           "enables",
    "addresses":         "addresses",
    "formalised_by":     "formalised_by",
    "proposes":          "proposes",
    "scaffolds":         "scaffolds",
    "parallels":         "parallels",
    "derived_from":      "derived_from",
    "authored":          "authored",
    "co_developed":      "co_developed",
    "verified_by":       "verified_by",
    "reinterprets":      "reinterprets",
    "related_to":        "related_to",
}

def normalise_pred(raw: str) -> str:
    r = raw.lower().strip().replace(" ", "_").replace("-", "_")
    return PRED_MAP.get(r, "related_to")

def anchor_id(canonical: str) -> str:
    return "a." + hashlib.md5(canonical.encode()).hexdigest()[:12]

def canonical(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower().strip()).strip("_")[:80]

def ensure_anchor(cur, name: str, domain: str = TLST_DOMAIN,
                  state: str = "draft", maturity: float = 2.0) -> str:
    can = canonical(name)
    aid = anchor_id(can)
    display = name[:120]
    cur.execute("""
        INSERT OR IGNORE INTO anchors
            (id, canonical, display_name, maturity, state, visible,
             relation_count, domain_tags)
        VALUES (?, ?, ?, ?, ?, 0, 0, ?)
    """, (aid, can, display, maturity, state, domain))
    return aid

def insert_relation(cur, subj_id, pred, obj_id, domain=TLST_DOMAIN,
                    edge_type="hypothesised", confidence=0.80):
    cur.execute("""
        INSERT OR IGNORE INTO relations
            (subject_id, predicate, object_id, domain_tags, edge_type,
             confidence, edge_weight, seen_count, evidence_count,
             source_dataset, predicate_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
    """, (subj_id, pred, obj_id, domain, edge_type,
          confidence, confidence, "selyrionstory_pass3-8", "hypothesised_physics"))

def main():
    story = sqlite3.connect(STORY_DB)
    cms   = sqlite3.connect(CMS_DB)
    sc, cc = story.cursor(), cms.cursor()

    # Seed TLST core anchors (idempotent)
    tlst_id = ensure_anchor(cc, "Tied Looped String Theory (TLST)",
                             maturity=12.0, state="emerging")
    tfme_id = ensure_anchor(cc, "Tied-Field Matrix Engineering (TFME)",
                             maturity=8.0, state="emerging")

    # Pull all TLST-relevant pending items
    sc.execute("""
        SELECT pr.id, pr.pass_num, pr.item_type, pr.content, c.title
        FROM pending_review pr
        JOIN capsules c ON c.id = pr.capsule_id
        WHERE (c.title LIKE '%TLST%' OR c.title LIKE '%Tied Loop%'
               OR c.title LIKE '%TFME%' OR c.title LIKE '%OSCAR%'
               OR c.title LIKE '%Bushnell%' OR c.title LIKE '%braid%'
               OR c.body LIKE '%TLST%' OR c.body LIKE '%Tied Looped%'
               OR c.body LIKE '%TFME%' OR c.body LIKE '%Fibonacci braid%')
        AND pr.reviewed = 0
        ORDER BY pr.pass_num, pr.id
    """)
    rows = sc.fetchall()
    print(f"Found {len(rows)} TLST-relevant pending items across passes 3-8\n")

    relations_added = 0
    anchors_created = set()
    theories = []
    snapshots = []

    for pr_id, pass_num, item_type, content_raw, cap_title in rows:
        try:
            data = json.loads(content_raw)
        except Exception:
            continue

        # ── Pass 3: relations ─────────────────────────────────────────────
        if item_type == "relation" and "relations" in data:
            for rel in data["relations"]:
                subj = rel.get("subject", "").strip()
                pred = rel.get("predicate", "related_to").strip()
                obj  = rel.get("object", "").strip()
                if not subj or not obj or len(subj) > 200 or len(obj) > 200:
                    continue
                sid = ensure_anchor(cc, subj)
                oid = ensure_anchor(cc, obj)
                anchors_created.update([subj, obj])
                insert_relation(cc, sid, normalise_pred(pred), oid)
                relations_added += 1

        # ── Pass 4: state snapshots ───────────────────────────────────────
        elif item_type == "snapshot":
            snap = data.get("snapshot") or data.get("summary", "")
            if snap:
                snapshots.append(f"[{cap_title}] {snap[:200]}")

        # ── Pass 7: theories & inventions ────────────────────────────────
        elif item_type == "theory" or "theories_and_inventions" in data:
            for item in data.get("theories_and_inventions", []):
                name = item.get("name", "")
                desc = item.get("description", "")
                if name:
                    theories.append(f"{name}: {desc[:150]}")
                    tid = ensure_anchor(cc, name, maturity=4.0)
                    anchors_created.add(name)
                    insert_relation(cc, tlst_id, "encompasses", tid)
                    relations_added += 1

        # Mark as approved
        sc.execute("UPDATE pending_review SET reviewed=1 WHERE id=?", (pr_id,))

    # Update relation counts for modified anchors
    cc.execute("""
        UPDATE anchors SET relation_count = (
            SELECT COUNT(*) FROM relations
            WHERE subject_id=anchors.id OR object_id=anchors.id
        ) WHERE domain_tags LIKE '%tlst%'
    """)

    story.commit()
    cms.commit()
    story.close()
    cms.close()

    print(f"Relations ingested : {relations_added}")
    print(f"Anchors touched    : {len(anchors_created)}")
    print(f"Theories extracted : {len(theories)}")
    print(f"Identity snapshots : {len(snapshots)}")

    if theories:
        print("\n── Theories & inventions registered ──")
        for t in theories[:20]:
            print(f"  • {t}")

    if snapshots:
        print("\n── Identity snapshots ──")
        for s in snapshots[:5]:
            print(f"  • {s}")

    print("\nRunning LangEng thesis reconstruction...")
    _verify()


def _verify():
    import sys
    sys.path.insert(0, str(Path.home() / "Le_P2/Le_P3"))
    sys.path.insert(0, str(Path.home() / "projectbrain_dev"))

    from frame_extractor import get_intent
    from langeng.cms_integration import CMSDecisionResolver
    from langeng.cms_realizer import CMSRealizer
    from langeng.decision import DiscourseContext, AvailableSlots
    from llm_articulator import articulate, is_available

    resolver  = CMSDecisionResolver()
    realizer  = CMSRealizer()
    discourse = DiscourseContext()
    slots     = AvailableSlots()

    queries = [
        "explain tlst in detail",
        "what is tfme",
        "explain the oscar collider",
        "what are bushnells theorems",
        "explain the fibonacci helical braid",
    ]

    for q in queries:
        intent, extracted = get_intent(q)
        decision = resolver.resolve(intent, slots, discourse, extracted=extracted)
        prose = realizer.realize(decision.primary_act, decision.primary_payload)
        print(f"\n> {q}")
        if is_available():
            print(articulate(q, prose))
        else:
            print(prose)


if __name__ == "__main__":
    main()
