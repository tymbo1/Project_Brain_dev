#!/usr/bin/env python3
"""
selyrion_propose_relations.py — Symbolic relation synthesis for sparse nodes.

When Selyrion identifies a high-maturity / low-connectivity concept, this script
infers candidate relations by examining the semantic neighborhood and proposing
edge patterns that likely apply. Proposals go to HITL review before writing back
to relations_aggregated.

Inference logic:
  1. Activate concept → get semantic neighbors from chains
  2. For each neighbor, collect their strong outbound structural edges
  3. Propose: {sparse_concept} {pred} {obj} when {neighbor} {pred} {obj} exists
  4. Score by: neighbor_similarity × predicate_strength × object_quality
  5. Write to relation_proposals table for review

Pipeline:
  --probe            find top sparse nodes + generate proposals for each
  --propose <X>      generate proposals for a specific concept
  --review           show pending proposals
  --accept <id,...>  mark proposals as accepted
  --reject <id,...>  mark proposals as rejected
  --apply            write accepted proposals to relations_aggregated
"""

import sys, sqlite3, hashlib, math, time, argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

DB_PATH = Path.home() / "resonance_v11.db"

parser = argparse.ArgumentParser()
parser.add_argument("--probe",   action="store_true", help="Find top sparse nodes and propose for each")
parser.add_argument("--propose", type=str,            help="Propose relations for a specific concept")
parser.add_argument("--review",  action="store_true", help="Show pending proposals")
parser.add_argument("--accept",  type=str,            help="Comma-separated proposal IDs to accept")
parser.add_argument("--reject",  type=str,            help="Comma-separated proposal IDs to reject")
parser.add_argument("--apply",   action="store_true", help="Write accepted proposals to relations_aggregated")
parser.add_argument("--limit",   type=int, default=10, help="Max proposals per concept (default 10)")
parser.add_argument("--top",     type=int, default=8,  help="Top N sparse nodes for --probe (default 8)")
args = parser.parse_args()


# Predicates worth inheriting from neighbors.
# is_a excluded — too noisy (fragments like "is_a same", "is_a several" everywhere).
# same_as excluded — circular and low-signal for sparse nodes.
_INHERIT_PREDS = {
    "causes", "enables", "produces", "leads_to", "activates",
    "requires", "depends_on", "uses", "used_for",
    "inhibits", "regulates",
    "part_of", "facet_of", "derived_from", "has_subevent",
}

# Predicate strength for scoring (mirrors selyrion_reasoner.py)
_PRED_STRENGTH = {
    "is_a": 1.00, "part_of": 1.00, "derived_from": 0.95, "facet_of": 0.95,
    "causes": 0.90, "enables": 0.90, "produces": 0.90, "leads_to": 0.85,
    "requires": 0.85, "depends_on": 0.85,
    "used_for": 0.80, "uses": 0.80, "has_subevent": 0.75,
    "activates": 0.80, "same_as": 0.70,
    "inhibits": 0.75, "regulates": 0.75,
}

# Noise patterns for noise objects — never propose these as targets
_NOISE_OBJECTS = {
    "german", "french", "latin", "english", "spanish",
    "noun", "verb", "adjective", "adverb", "word", "phrase", "letter",
    "body", "organism", "process", "entity", "thing", "object", "system",
    "person", "individual", "agent", "being", "element",
    "short", "long", "good", "high", "other", "such", "more",
    "trade", "pleasure",  # noise is_a targets we see in data
}


def ensure_proposals_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relation_proposals (
            id           TEXT PRIMARY KEY,
            subject      TEXT NOT NULL,
            predicate    TEXT NOT NULL,
            object       TEXT NOT NULL,
            confidence   REAL DEFAULT 0.0,
            source_neighbor TEXT,
            inference_path  TEXT,
            proposed_at  REAL,
            reviewed     INTEGER DEFAULT 0,
            accepted     INTEGER DEFAULT 0
        )
    """)
    conn.commit()


def _proposal_id(subject: str, pred: str, obj: str) -> str:
    return hashlib.md5(f"{subject}|{pred}|{obj}".encode()).hexdigest()[:12]


def _get_existing_objects(anchor_id: str, conn: sqlite3.Connection) -> set[str]:
    """All concepts already connected to this anchor (as objects)."""
    rows = conn.execute(
        "SELECT a2.canonical FROM relations_aggregated r "
        "JOIN anchors a2 ON r.object_id = a2.id WHERE r.subject_id=?",
        (anchor_id,)
    ).fetchall()
    return {r[0].lower() for r in rows}


def _object_quality(obj_canonical: str, conn: sqlite3.Connection) -> float:
    """Quality score for a proposed object: maturity + length heuristic."""
    row = conn.execute(
        "SELECT maturity FROM anchors WHERE canonical=? LIMIT 1",
        (obj_canonical,)
    ).fetchone()
    if not row:
        return 0.0
    mat = min(1.0, math.log1p(row[0]) / 15.0)
    # Multi-word concepts are more specific
    words = len(obj_canonical.split())
    specificity = 0.9 if words >= 3 else (0.7 if words == 2 else 0.5)
    return (mat + specificity) / 2.0


def _extract_neighbors_from_chains(concept: str, chains: list) -> list[tuple[str, float]]:
    """
    Pull semantic neighbors from activation chains with similarity scores.
    Returns [(canonical, similarity_score), ...] sorted by score.
    """
    concept_l = concept.lower().replace(" ", "_")
    concept_plain = concept.lower()
    neighbor_scores: dict[str, float] = {}

    for chain in chains:
        parts = chain.split(" | ")
        if len(parts) < 3:
            continue
        subj = parts[0].strip()
        pred = parts[1].strip()
        obj  = parts[2].split(" | strength:")[0].strip()
        strength_raw = 50
        if "strength:" in parts[-1]:
            try:
                strength_raw = int(parts[-1].split("strength:")[-1].strip())
            except ValueError:
                pass
        similarity = min(1.0, strength_raw / 100.0)

        # Collect the OTHER node in each chain edge as a neighbor
        for candidate in (subj, obj):
            if candidate in (concept_l, concept_plain):
                continue
            if len(candidate) < 4 or candidate in _NOISE_OBJECTS:
                continue
            if candidate not in neighbor_scores or neighbor_scores[candidate] < similarity:
                neighbor_scores[candidate] = similarity

    # Sort by score, return top 15
    return sorted(neighbor_scores.items(), key=lambda x: -x[1])[:15]


def _get_neighbor_structural_edges(neighbor_canonical: str,
                                   conn: sqlite3.Connection) -> list[tuple]:
    """
    Get strong outbound structural edges from a neighbor concept.
    Returns [(pred, obj_canonical, seen_count, confidence), ...]
    """
    row = conn.execute(
        "SELECT id FROM anchors WHERE canonical=? LIMIT 1", (neighbor_canonical,)
    ).fetchone()
    if not row:
        return []
    n_id = row[0]

    preds_placeholder = ",".join("?" * len(_INHERIT_PREDS))
    edges = conn.execute(f"""
        SELECT r.predicate, a2.canonical, r.seen_count, r.confidence
        FROM relations_aggregated r
        JOIN anchors a2 ON r.object_id = a2.id
        WHERE r.subject_id = ?
          AND r.predicate IN ({preds_placeholder})
          AND r.seen_count >= 2
          AND length(a2.canonical) >= 5
          AND a2.canonical NOT IN ('paper','study','result','figure',
              'method','data','model','table','value','group','level',
              'analysis','process','function','type','form','part',
              'more','over','other','such','this','that','which',
              'person','entity','individual','thing','object','being','agent',
              'several','complete','popular','immortal','itself','himself',
              'herself','itself','themselves','yourself','ourself')
        ORDER BY r.seen_count DESC LIMIT 12
    """, [n_id] + list(_INHERIT_PREDS)).fetchall()

    return edges


def propose_for_concept(concept: str, conn: sqlite3.Connection,
                        max_proposals: int = 10) -> list[dict]:
    """
    Generate relation proposals for a sparse concept.
    Uses activation engine to find semantic neighbors, inherits their
    structural edges as candidate relations for the sparse node.
    """
    from inference.activation_engine import ActivationEngine
    engine = ActivationEngine()

    # Get anchor ID and existing connections
    anchor_row = conn.execute(
        "SELECT id FROM anchors WHERE canonical=? LIMIT 1", (concept.lower(),)
    ).fetchone()
    if not anchor_row:
        print(f"  [!] Concept '{concept}' not found in anchors.")
        return []
    anchor_id = anchor_row[0]
    existing = _get_existing_objects(anchor_id, conn)

    # Activate field to get semantic neighborhood
    result = engine.infer(concept)
    chains = result.get("chains", [])
    if not chains:
        print(f"  [!] No activation chains for '{concept}'.")
        return []

    neighbors = _extract_neighbors_from_chains(concept, chains)
    if not neighbors:
        print(f"  [!] No neighbors found in activation field for '{concept}'.")
        return []

    # Collect proposals from neighbor edges
    proposals: dict[str, dict] = {}

    for neighbor_canonical, neighbor_sim in neighbors:
        # Filter noise objects from neighbor list
        if neighbor_canonical.lower() in _NOISE_OBJECTS:
            continue

        neighbor_edges = _get_neighbor_structural_edges(neighbor_canonical, conn)
        for pred, obj_canonical, seen_count, edge_conf in neighbor_edges:
            obj_lower = obj_canonical.lower()

            # Skip if already exists, is the concept itself, or is noise
            if obj_lower in existing or obj_lower == concept.lower():
                continue
            if obj_lower in _NOISE_OBJECTS:
                continue

            proposal_id = _proposal_id(concept, pred, obj_canonical)
            if proposal_id in proposals:
                # Keep the one with higher confidence
                if proposals[proposal_id]["confidence"] >= edge_conf:
                    continue

            pred_strength  = _PRED_STRENGTH.get(pred, 0.65)
            obj_quality    = _object_quality(obj_canonical, conn)
            # Confidence: neighbor similarity × predicate strength × object quality × inference discount
            confidence = neighbor_sim * pred_strength * obj_quality * 0.55

            proposals[proposal_id] = {
                "id":               proposal_id,
                "subject":          concept,
                "predicate":        pred,
                "object":           obj_canonical,
                "confidence":       round(confidence, 4),
                "source_neighbor":  neighbor_canonical,
                "inference_path":   f"{concept} ~ {neighbor_canonical} → {neighbor_canonical} {pred} {obj_canonical}",
                "proposed_at":      time.time(),
            }

    if not proposals:
        print(f"  No proposals generated for '{concept}' (all candidates already exist or filtered).")
        return []

    # Sort by confidence descending
    ranked = sorted(proposals.values(), key=lambda x: -x["confidence"])
    return ranked[:max_proposals]


def save_proposals(proposals: list[dict], conn: sqlite3.Connection) -> int:
    """Write proposals to relation_proposals table. Returns count of new inserts."""
    inserted = 0
    for p in proposals:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO relation_proposals
                    (id, subject, predicate, object, confidence,
                     source_neighbor, inference_path, proposed_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                p["id"], p["subject"], p["predicate"], p["object"],
                p["confidence"], p["source_neighbor"], p["inference_path"],
                p["proposed_at"],
            ))
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception as e:
            print(f"  [!] Insert error for {p['id']}: {e}")
    conn.commit()
    return inserted


def _find_sparse_nodes(conn: sqlite3.Connection, top_n: int = 8) -> list[tuple[str, float, int]]:
    """Return top-N gap nodes: high maturity, low connectivity, clean canonicals."""
    import re
    _NOISE_RE = re.compile(
        r"^(his |her |our |your |their |the |upon |between |likewise |numerou|"
        r"- |\w+ itself$|idea or |unavoidable )", re.IGNORECASE
    )

    rows = conn.execute("""
        SELECT a.canonical, a.maturity, COUNT(r.subject_id) as rel_count
        FROM anchors a
        LEFT JOIN relations_aggregated r ON r.subject_id = a.id AND r.seen_count >= 2
        WHERE a.maturity >= 10000
          AND length(a.canonical) BETWEEN 4 AND 28
          AND a.canonical NOT GLOB '*[0-9]*'
          AND trim(a.canonical) = a.canonical
        GROUP BY a.id
        HAVING rel_count BETWEEN 0 AND 4
        ORDER BY a.maturity / (rel_count + 1) DESC
        LIMIT 200
    """).fetchall()

    def _clean(c):
        if _NOISE_RE.search(c):
            return False
        words = c.split()
        if len(words) >= 2 and any(w[0].isupper() for w in words if w):
            return False
        if any(len(w) <= 1 for w in words):
            return False
        return True

    return [r for r in rows if _clean(r[0])][:top_n]


def cmd_propose(concept: str, conn: sqlite3.Connection):
    print(f"\n  Generating proposals for: '{concept}'")
    proposals = propose_for_concept(concept, conn, max_proposals=args.limit)
    if not proposals:
        return
    n = save_proposals(proposals, conn)
    print(f"  {n} new proposals saved ({len(proposals)} generated).\n")
    for p in proposals:
        marker = "NEW" if p["proposed_at"] > time.time() - 5 else "   "
        print(f"  [{p['id']}] {marker}  conf={p['confidence']:.3f}  "
              f"{p['subject']} —[{p['predicate']}]→ {p['object']}")
        print(f"          via: {p['inference_path']}")


def cmd_probe(conn: sqlite3.Connection):
    print(f"\n  Probing top-{args.top} sparse nodes...\n")
    nodes = _find_sparse_nodes(conn, top_n=args.top)
    if not nodes:
        print("  No sparse nodes found.")
        return
    total_new = 0
    for canonical, maturity, rel_count in nodes:
        mat_str = f"{maturity/1_000_000:.1f}M" if maturity >= 1_000_000 else f"{maturity/1000:.0f}k"
        print(f"  ── [{canonical}]  maturity={mat_str}  edges={rel_count}")
        proposals = propose_for_concept(canonical, conn, max_proposals=args.limit)
        n = save_proposals(proposals, conn)
        total_new += n
        for p in proposals[:5]:
            print(f"     [{p['id']}] conf={p['confidence']:.3f}  "
                  f"—[{p['predicate']}]→ {p['object']}  (via {p['source_neighbor']})")
        if len(proposals) > 5:
            print(f"     ... and {len(proposals)-5} more")
        print()
    print(f"  Total new proposals saved: {total_new}")


def cmd_review(conn: sqlite3.Connection):
    rows = conn.execute("""
        SELECT id, subject, predicate, object, confidence, source_neighbor, inference_path
        FROM relation_proposals
        WHERE reviewed = 0
        ORDER BY subject, confidence DESC
        LIMIT 100
    """).fetchall()
    if not rows:
        print("\n  No pending proposals.")
        return
    print(f"\n  {len(rows)} pending proposals:\n")
    current_subject = None
    for pid, subj, pred, obj, conf, neighbor, path in rows:
        if subj != current_subject:
            print(f"  ── [{subj}]")
            current_subject = subj
        print(f"     [{pid}]  conf={conf:.3f}  —[{pred}]→ {obj}")
        print(f"             via: {path}")
    print(f"\n  Accept: --accept id1,id2,...   Reject: --reject id1,id2,...")


def cmd_accept(ids_str: str, conn: sqlite3.Connection):
    ids = [x.strip() for x in ids_str.split(",") if x.strip()]
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE relation_proposals SET reviewed=1, accepted=1 WHERE id IN ({placeholders})",
        ids
    )
    conn.commit()
    changed = conn.execute("SELECT changes()").fetchone()[0]
    print(f"  Accepted {changed} proposal(s).")


def cmd_reject(ids_str: str, conn: sqlite3.Connection):
    ids = [x.strip() for x in ids_str.split(",") if x.strip()]
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE relation_proposals SET reviewed=1, accepted=0 WHERE id IN ({placeholders})",
        ids
    )
    conn.commit()
    changed = conn.execute("SELECT changes()").fetchone()[0]
    print(f"  Rejected {changed} proposal(s).")


def cmd_apply(conn: sqlite3.Connection):
    """Write accepted proposals into relations_aggregated as a new tier."""
    accepted = conn.execute("""
        SELECT subject, predicate, object, confidence
        FROM relation_proposals
        WHERE accepted=1
        AND id NOT IN (
            SELECT p.id FROM relation_proposals p
            JOIN anchors a1 ON a1.canonical = p.subject
            JOIN anchors a2 ON a2.canonical = p.object
            JOIN relations_aggregated r
              ON r.subject_id = a1.id AND r.predicate = p.predicate AND r.object_id = a2.id
        )
    """).fetchall()

    if not accepted:
        print("  No accepted proposals pending write-back.")
        return

    written = 0
    skipped = 0
    for subj, pred, obj, conf in accepted:
        a1 = conn.execute("SELECT id FROM anchors WHERE canonical=? LIMIT 1", (subj,)).fetchone()
        a2 = conn.execute("SELECT id FROM anchors WHERE canonical=? LIMIT 1", (obj,)).fetchone()
        if not a1 or not a2:
            skipped += 1
            continue
        # Determine edge_type from predicate category
        edge_type = "semantic"
        if pred in {"causes", "enables", "produces", "leads_to", "activates",
                    "inhibits", "regulates", "requires", "depends_on"}:
            edge_type = "mechanistic"
        elif pred in {"is_a", "part_of", "facet_of", "derived_from", "same_as"}:
            edge_type = "taxonomic"
        elif pred in {"used_for", "uses", "has_subevent"}:
            edge_type = "functional"

        conn.execute("""
            INSERT OR IGNORE INTO relations_aggregated
                (subject_id, predicate, object_id, domain_tags, edge_type,
                 seen_count, evidence_count, confidence)
            VALUES (?,?,?,?,?, 1, 1, ?)
        """, (a1[0], pred, a2[0], "proposed|", edge_type, conf))
        if conn.execute("SELECT changes()").fetchone()[0]:
            written += 1
            print(f"  WRITTEN: {subj} —[{pred}]→ {obj}  (conf={conf:.3f})")
        else:
            skipped += 1

    conn.commit()
    print(f"\n  Written: {written}  Skipped (already exists): {skipped}")
    print("  Written edges carry domain_tag 'proposed|' — distinguishable from corpus edges.")


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size=-32000")
    ensure_proposals_table(conn)

    if args.propose:
        cmd_propose(args.propose, conn)
    elif args.probe:
        cmd_probe(conn)
    elif args.review:
        cmd_review(conn)
    elif args.accept:
        cmd_accept(args.accept, conn)
    elif args.reject:
        cmd_reject(args.reject, conn)
    elif args.apply:
        cmd_apply(conn)
    else:
        parser.print_help()

    conn.close()


if __name__ == "__main__":
    main()
