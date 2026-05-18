"""
ssre_anomaly_validator.py — SSRE validation of vision sequence anomalies.

Reads unvalidated sequence_anomaly hypotheses, checks whether the anomalous
event transition is supported or contradicted by the CMS knowledge graph,
then writes validated + ssre_score back.

Validation logic:
  1. Parse event keys → extract CMS concept labels (person, cup, table…)
  2. Look up anchor IDs for those labels
  3. Query CMS for a direct or 1-hop relation path between the key concepts
  4. Score the path: frequency × predicate weight → ssre_score (0–1)
  5. validated = 1 (CMS-plausible) if ssre_score > CONFIRM_THRESHOLD
     validated = 2 (CMS-unsupported) if ssre_score < REJECT_THRESHOLD
     validated = 0 (stay pending) if in between

Run as a background job or call validate_pending() from video_ingest.
"""
from __future__ import annotations
import sqlite3
import time
import re
from pathlib import Path

DB_PATH = Path.home() / "resonance_v11.db"

# CMS is built on scientific literature — everyday vision concepts score low.
# Thresholds calibrated to observed CMS signal range (0–0.25 typical).
CONFIRM_THRESHOLD = 0.05   # any meaningful CMS path → plausible
REJECT_THRESHOLD  = 0.001  # truly zero evidence anywhere → unsupported
BATCH_SIZE        = 50     # hypotheses processed per call

# Predicate reliability weights (mirrors ssre_multipass.py)
_PRED_WEIGHT: dict[str, float] = {
    "requires":    1.0,
    "enables":     1.0,
    "causes":      0.95,
    "is_a":        0.9,
    "produces":    0.85,
    "affects":     0.8,
    "binds_to":    0.75,
    "interacts_with": 0.7,
    "contains":    0.55,
    "context_of":  0.45,
    "associative": 0.3,
}
_PRED_DEFAULT = 0.4


# ── Event key parsing ─────────────────────────────────────────────────────────

def _labels_from_event_key(event_key: str) -> list[str]:
    """
    Extract CMS concept labels from an event key.

    Key format: "{event_type}:{instance_sub}:{instance_obj}"
    Instance IDs: "{label}_{hex}"  e.g. "person_aa11", "cup_bb22"

    Also extracts semantic labels from compound event types:
      "person_interacts_with_cup" → ["person", "cup"]
      "placement"                 → []   (no embedded labels)
    """
    labels = []
    parts = event_key.split(":")
    event_type = parts[0] if parts else ""

    # Base event types that are NOT concept labels
    _BASE_EVENTS = {
        "object_entered", "object_left", "interaction_start", "interaction_end",
        "placement", "removal", "relation_formed", "relation_ended",
        "proximity_start", "proximity_end", "object_detected",
    }

    # Extract embedded labels from semantic event types
    # Matches: person_entered, car_appeared, person_interacts_with_cup
    compound = re.sub(r'_(entered|left|appeared|departed|interacts_with_).*', '', event_type)
    if compound and compound not in _BASE_EVENTS and "_" not in compound:
        labels.append(compound.lower())

    # Right-hand side of interacts_with
    m = re.search(r'_interacts_with_(\w+)', event_type)
    if m:
        labels.append(m.group(1).lower())

    # Instance ID labels (label = everything before final _hex)
    for inst_id in parts[1:]:
        if not inst_id:
            continue
        # instance_id format: "{label}_{6hex}"  e.g. "person_aa1b2c"
        label = re.sub(r'_[0-9a-f]{4,8}$', '', inst_id).lower()
        if label and label not in labels:
            labels.append(label)

    return [l for l in labels if len(l) > 1]


def _lookup_anchor(conn: sqlite3.Connection, label: str) -> str | None:
    """Return anchor_id for a concept label via canonical column."""
    row = conn.execute(
        "SELECT id FROM anchors WHERE canonical = ? LIMIT 1", (label,)
    ).fetchone()
    if row:
        return row[0]
    # Partial match via display_name
    row = conn.execute(
        "SELECT id FROM anchors WHERE display_name = ? LIMIT 1", (label,)
    ).fetchone()
    if row:
        return row[0]
    # Prefix fallback on canonical
    row = conn.execute(
        "SELECT id FROM anchors WHERE canonical LIKE ? LIMIT 1", (f"{label}%",)
    ).fetchone()
    return row[0] if row else None


# ── CMS path scoring ──────────────────────────────────────────────────────────

def _score_direct_link(conn: sqlite3.Connection,
                        subj_id: str, obj_id: str) -> float:
    """
    Score the direct relation(s) between two anchors.
    Uses relations_aggregated (trusted layer only).
    Returns 0.0 if no direct link exists.
    """
    rows = conn.execute("""
        SELECT predicate, seen_count, confidence FROM relations_aggregated
        WHERE subject_id = ? AND object_id = ?
        LIMIT 10
    """, (subj_id, obj_id)).fetchall()

    if not rows:
        return 0.0

    total_seen = sum(r[1] for r in rows)
    weighted = sum(
        _PRED_WEIGHT.get(r[0], _PRED_DEFAULT) * r[1] * (r[2] or 1.0)
        for r in rows
    )
    freq_signal = min(total_seen / 100.0, 1.0)
    pred_signal = weighted / total_seen if total_seen > 0 else 0.0
    return round(freq_signal * 0.5 + pred_signal * 0.5, 3)


def _score_hop(conn: sqlite3.Connection,
               subj_id: str, obj_id: str) -> float:
    """
    1-hop path score: subj → intermediate → obj.
    Returns the best single intermediate score via SQL JOIN.
    """
    row = conn.execute("""
        SELECT MAX(ra1.seen_count * ra2.seen_count) as path_strength
        FROM relations_aggregated ra1
        JOIN relations_aggregated ra2 ON ra1.object_id = ra2.subject_id
        WHERE ra1.subject_id = ? AND ra2.object_id = ?
        LIMIT 1
    """, (subj_id, obj_id)).fetchone()

    raw = row[0] if (row and row[0]) else 0
    return round(min(raw / 500.0, 1.0), 3)


def _score_vision_layer(conn: sqlite3.Connection,
                        subj_id: str, obj_id: str) -> float:
    """
    Check vision-inferred relations (source_dataset='coco' or 'vision_causal').
    Gives a baseline score even when science CMS has no path.
    """
    row = conn.execute("""
        SELECT COUNT(*), MAX(confidence) FROM relations
        WHERE subject_id IN (?, ?) AND object_id IN (?, ?)
          AND source_dataset IN ('coco', 'vision_causal')
    """, (subj_id, obj_id, subj_id, obj_id)).fetchone()
    count, max_conf = (row[0] or 0), (row[1] or 0.0)
    if count == 0:
        return 0.0
    return round(min(count / 5.0, 0.5) * 0.5 + max_conf * 0.5, 3)


def cms_plausibility(conn: sqlite3.Connection,
                     labels_e1: list[str], labels_e2: list[str]) -> float:
    """
    Score the CMS plausibility of transition (e1 → e2).
    Checks all label pairs across both events.
    Returns the maximum score found (best evidence wins).
    """
    best = 0.0
    anchor_cache: dict[str, str | None] = {}

    def _anc(label):
        if label not in anchor_cache:
            anchor_cache[label] = _lookup_anchor(conn, label)
        return anchor_cache[label]

    all_labels = list(dict.fromkeys(labels_e1 + labels_e2))  # ordered dedup
    anchor_ids = [a for l in all_labels if (a := _anc(l)) is not None]

    # Score all anchor pairs
    for i, a1 in enumerate(anchor_ids):
        for a2 in anchor_ids[i+1:]:
            direct = _score_direct_link(conn, a1, a2)
            if direct > best:
                best = direct
            # 1-hop only if direct didn't confirm
            if best < CONFIRM_THRESHOLD:
                hop = _score_hop(conn, a1, a2)
                if hop > best:
                    best = hop
            # Vision layer as additional evidence
            vision = _score_vision_layer(conn, a1, a2)
            if vision > best:
                best = vision

    return best


# ── Batch validation ──────────────────────────────────────────────────────────

def validate_pending(conn: sqlite3.Connection,
                     batch_size: int = BATCH_SIZE,
                     verbose: bool = False) -> dict:
    """
    Process up to batch_size unvalidated sequence_anomaly hypotheses.
    Updates validated + ssre_score in-place.
    Returns summary counts.
    """
    rows = conn.execute("""
        SELECT id, subject, object FROM hypotheses
        WHERE predicate = 'sequence_anomaly' AND validated = 0
        LIMIT ?
    """, (batch_size,)).fetchall()

    confirmed = rejected = pending = 0

    for hyp_id, e1_key, e2_key in rows:
        labels_e1 = _labels_from_event_key(e1_key)
        labels_e2 = _labels_from_event_key(e2_key)

        score = cms_plausibility(conn, labels_e1, labels_e2)

        if score >= CONFIRM_THRESHOLD:
            validated = 1
            confirmed += 1
        elif score < REJECT_THRESHOLD:
            validated = 2
            rejected += 1
        else:
            validated = 0   # leave pending — weak signal, not enough to decide
            pending += 1

        conn.execute("""
            UPDATE hypotheses SET validated = ?, ssre_score = ?
            WHERE id = ?
        """, (validated, score, hyp_id))

        if verbose:
            label = {0: "pending", 1: "✓ confirm", 2: "✗ reject"}[validated]
            print(f"  [{label}]  {e1_key.split(':')[0]} → {e2_key.split(':')[0]}"
                  f"  score={score:.3f}")

    conn.commit()
    return {"confirmed": confirmed, "rejected": rejected, "pending": pending,
            "total": len(rows)}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    db_path = next((a.split("=")[1] for a in sys.argv if a.startswith("--db=")), str(DB_PATH))

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")

    print(f"SSRE anomaly validator — {db_path}")
    t0 = time.time()
    stats = validate_pending(conn, verbose=verbose)
    elapsed = time.time() - t0

    print(f"\nDone in {elapsed:.1f}s")
    print(f"  confirmed (CMS-plausible): {stats['confirmed']}")
    print(f"  rejected  (unsupported):   {stats['rejected']}")
    print(f"  pending   (weak signal):   {stats['pending']}")
    print(f"  total processed:           {stats['total']}")
    conn.close()
