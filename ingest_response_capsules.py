#!/usr/bin/env python3
"""
ingest_response_capsules.py

Seeds the CMS field with response capsules — structural attractors
that co-activate with content to guide Selyrion's expression.

Capsules are first-class field citizens:
  - anchors table   → capsule as concept (domain_tags='response_pattern')
  - relations_aggregated → bidirectional links to concept types
  - fragments table → attractor body (guidance, not template)
  - fragment_links  → connects capsule anchor to its attractor body

HITL gate: read-only dry-run by default. Pass --commit to write.

Usage:
    python3 ingest_response_capsules.py           # dry-run
    python3 ingest_response_capsules.py --commit  # write to DB
"""

import sqlite3
import argparse
import uuid
from datetime import datetime

DB_PATH = "/home/timbushnell/resonance_v11.db"

# ─── Capsule definitions ───────────────────────────────────────────────────────

CAPSULES = [
    {
        "id":           "biographical_response",
        "display_name": "Biographical Response",
        "attractor":    (
            "Bias toward: establish identity first (who/what they are), "
            "then key contribution (what they did or discovered), "
            "then broader significance (why it matters). "
            "Let field density determine weight — do not fabricate if chains are sparse. "
            "Tone: direct, authoritative, not cold. "
            "Do not override field content. Structure must adapt to available density."
        ),
        "applies_to":   ["person", "historical_figure", "scientist", "philosopher", "artist"],
    },
    {
        "id":           "mechanistic_response",
        "display_name": "Mechanistic Response",
        "attractor":    (
            "Bias toward: lead with what it does, not what it is. "
            "Then the key mechanism or process. "
            "Then conditions, dependencies, or enabling factors. "
            "Avoid listing — connect causally. "
            "Tone: precise, active, process-oriented. "
            "Do not override field content. Structure must adapt to available density."
        ),
        "applies_to":   ["process", "mechanism", "reaction", "system", "function"],
    },
    {
        "id":           "relational_response",
        "display_name": "Relational Response",
        "attractor":    (
            "Bias toward: establish that the relation exists, "
            "then describe the nature of the connection (causal, taxonomic, co-occurring), "
            "then the direction or asymmetry if relevant. "
            "Do not assert relations the field does not support. "
            "Tone: analytical, qualified where appropriate. "
            "Do not override field content. Structure must adapt to available density."
        ),
        "applies_to":   ["concept", "theory", "phenomenon", "organism", "molecule"],
    },
    {
        "id":           "definitional_response",
        "display_name": "Definitional Response",
        "attractor":    (
            "Bias toward: anchor the concept in its category first (is_a), "
            "then its distinguishing properties, "
            "then its primary relations. "
            "One strong definition beats a list of facts. "
            "Tone: clear, grounded, encyclopaedic without being dry. "
            "Do not override field content. Structure must adapt to available density."
        ),
        "applies_to":   ["concept", "theory", "substance", "field", "property"],
    },
    {
        "id":           "comparative_response",
        "display_name": "Comparative Response",
        "attractor":    (
            "Bias toward: shared category first (what they both are), "
            "then the axis of difference (what distinguishes them), "
            "then relative significance if field supports it. "
            "Avoid false equivalence — weight by field density. "
            "Tone: balanced, structurally clear. "
            "Do not override field content. Structure must adapt to available density."
        ),
        "applies_to":   ["concept", "process", "molecule", "organism", "theory"],
    },
    {
        "id":           "sparse_response",
        "display_name": "Sparse Response",
        "attractor":    (
            "Field density is low. "
            "Bias toward: acknowledge what is known without fabricating. "
            "Name the concept, state the limited connections honestly, "
            "do not fill gaps with speculation. "
            "One honest sentence beats three uncertain ones. "
            "Tone: measured, honest, open. "
            "Do not override field content. Structure must adapt to available density."
        ),
        "applies_to":   [],  # activated by edge_count threshold, not concept type
    },
]

# Concept types that link to capsules — maps type → capsule id
TYPE_CAPSULE_MAP = {
    "person":           "biographical_response",
    "historical_figure":"biographical_response",
    "scientist":        "biographical_response",
    "philosopher":      "biographical_response",
    "artist":           "biographical_response",
    "process":          "mechanistic_response",
    "mechanism":        "mechanistic_response",
    "reaction":         "mechanistic_response",
    "system":           "mechanistic_response",
    "function":         "mechanistic_response",
    "concept":          "definitional_response",
    "theory":           "definitional_response",
    "field":            "definitional_response",
    "substance":        "definitional_response",
    "molecule":         "definitional_response",
}

EDGE_TYPE    = "meta"
DOMAIN_TAGS  = "response_pattern"


def _anchor_row(capsule_id: str, display: str) -> dict:
    return {
        "id":           capsule_id,
        "canonical":    capsule_id,
        "display_name": display,
        "anchor_type":  "response_capsule",
        "maturity":     1.0,
        "state":        "stable",
        "visible":      0,           # not surfaced in normal queries
        "domain_tags":  DOMAIN_TAGS,
        "node_type":    "capsule",
        "node_layer":   2,
    }


def _relation_row(subj: str, pred: str, obj: str) -> dict:
    return {
        "subject_id":     subj,
        "predicate":      pred,
        "object_id":      obj,
        "domain_tags":    DOMAIN_TAGS,
        "edge_type":      EDGE_TYPE,
        "seen_count":     1,
        "evidence_count": 1,
        "confidence":     1.0,
        "edge_weight":    1.0,
        "polarity":       "positive",
    }


def _fragment_row(capsule_id: str, text: str) -> tuple[str, dict]:
    frag_id = f"capsule_frag_{capsule_id}"
    return frag_id, {
        "id":         frag_id,
        "text":       text,
        "source":     "HITL_seed_20260418",
        "state":      "stable",
        "confidence": 1.0,
    }


def run(commit: bool) -> None:
    mode = "COMMIT" if commit else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"Response Capsule Ingestion — {mode}")
    print(f"{'='*60}\n")

    db = sqlite3.connect(DB_PATH)

    anchor_inserts   = []
    relation_inserts = []
    fragment_inserts = []
    link_inserts     = []

    for cap in CAPSULES:
        cid     = cap["id"]
        display = cap["display_name"]

        # Anchor
        anchor_inserts.append(_anchor_row(cid, display))

        # Fragment + link
        frag_id, frag_row = _fragment_row(cid, cap["attractor"])
        fragment_inserts.append(frag_row)
        link_inserts.append({
            "fragment_id": frag_id,
            "anchor_id":   cid,
            "relation":    "attractor_body",
        })

        # Bidirectional relations: type ↔ capsule
        for concept_type in cap["applies_to"]:
            relation_inserts.append(_relation_row(concept_type, "has_response_pattern", cid))
            relation_inserts.append(_relation_row(cid, "applies_to", concept_type))

        # capsule → is_a → response_pattern (field identity)
        relation_inserts.append(_relation_row(cid, "is_a", "response_pattern"))
        relation_inserts.append(_relation_row("response_pattern", "contains", cid))

        print(f"  Capsule: {cid}")
        print(f"    Fragment: {frag_id}")
        print(f"    Relations: {len(cap['applies_to']) * 2 + 2}")
        print()

    print(f"Total anchors   : {len(anchor_inserts)}")
    print(f"Total relations : {len(relation_inserts)}")
    print(f"Total fragments : {len(fragment_inserts)}")
    print(f"Total links     : {len(link_inserts)}")

    if not commit:
        print(f"\n[DRY-RUN] No writes. Pass --commit to ingest.\n")
        db.close()
        return

    print(f"\nWriting to DB...")

    try:
        cur = db.cursor()

        # Anchors
        for row in anchor_inserts:
            cur.execute("""
                INSERT OR IGNORE INTO anchors
                (id, canonical, display_name, anchor_type, maturity,
                 state, visible, domain_tags, node_type, node_layer)
                VALUES
                (:id, :canonical, :display_name, :anchor_type, :maturity,
                 :state, :visible, :domain_tags, :node_type, :node_layer)
            """, row)

        # Relations
        for row in relation_inserts:
            cur.execute("""
                INSERT OR IGNORE INTO relations_aggregated
                (subject_id, predicate, object_id, domain_tags, edge_type,
                 seen_count, evidence_count, confidence, edge_weight, polarity)
                VALUES
                (:subject_id, :predicate, :object_id, :domain_tags, :edge_type,
                 :seen_count, :evidence_count, :confidence, :edge_weight, :polarity)
            """, row)

        # Fragments
        for row in fragment_inserts:
            cur.execute("""
                INSERT OR IGNORE INTO fragments (id, text, source, state, confidence)
                VALUES (:id, :text, :source, :state, :confidence)
            """, row)

        # Fragment links
        for row in link_inserts:
            cur.execute("""
                INSERT OR IGNORE INTO fragment_links (fragment_id, anchor_id, relation)
                VALUES (:fragment_id, :anchor_id, :relation)
            """, row)

        db.commit()
        print(f"[✓] Committed: {len(anchor_inserts)} anchors, "
              f"{len(relation_inserts)} relations, "
              f"{len(fragment_inserts)} fragments, "
              f"{len(link_inserts)} links\n")

    except Exception as e:
        db.rollback()
        print(f"[✗] Error — rolled back: {e}\n")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                        help="Write to DB (default is dry-run)")
    args = parser.parse_args()
    run(commit=args.commit)
