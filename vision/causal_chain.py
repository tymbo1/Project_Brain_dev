"""
causal_chain.py — Causal chain inference from temporal event sequences.

Builds (event_A leads_to event_B) chains when:
  - same subject instance involved in both events
  - events occur within a time/frame window
  - transition is directionally coherent (action → outcome)

Also maintains sequence memory for prediction (sequences table).

Architectural rule: causal confidence is NOT truth.
Chains go to the relations table with edge_type='causal_inferred'
and confidence < 1.0. SSRE validates them separately.
"""
from __future__ import annotations
import sqlite3
import time
import uuid

# Max frame gap between two events to consider causal linkage
CAUSAL_FRAME_WINDOW = 10

# Directional coherence: event_type_A → plausible event_type_B
# Only these transitions are considered for causal chains
COHERENT_TRANSITIONS: dict[str, list[str]] = {
    # Generic (fallback)
    "object_entered":    ["interaction_start", "placement", "relation_formed",
                          "proximity_start"],
    "interaction_start": ["interaction_end", "placement", "relation_formed"],
    "interaction_end":   ["placement", "removal", "object_left"],
    "placement":         ["removal", "relation_ended", "object_left"],
    "removal":           ["object_left", "relation_ended"],
    "relation_formed":   ["relation_ended", "placement", "interaction_end"],
    "proximity_start":   ["interaction_start", "placement", "proximity_end"],
    "proximity_end":     ["relation_ended", "object_left"],
}

# Coherence is also checked by prefix for semantic event types
# e.g. "person_entered" → matches rules for "object_entered"
_PREFIX_MAP: dict[str, str] = {
    "_entered":            "object_entered",
    "_appeared":           "object_entered",
    "object_detected:":    "object_entered",
    "_left":               "object_left",
    "_departed":           "object_left",
    "object_removed:":     "object_left",
    "_interacts_with_":    "interaction_start",
}


def _normalise_event_type(event_type: str) -> str:
    """Map semantic event type to base type for coherence lookup."""
    for suffix, base in _PREFIX_MAP.items():
        if suffix in event_type:
            return base
    return event_type


def _event_key(ev: dict) -> str:
    """Compact string key for an event (for sequence memory)."""
    s = ev.get("instance_sub") or ev.get("subject_id") or ""
    o = ev.get("instance_obj") or ev.get("object_id") or ""
    return f"{ev['type']}:{s}:{o}"


def build_causal_chains(events: list[dict], frame_id: int) -> list[dict]:
    """
    Given a list of events from recent frames, return inferred causal chains.
    Each chain: {subject, predicate='leads_to', object, confidence, frame_id}
    """
    chains = []

    for i, e1 in enumerate(events):
        for e2 in events[i+1:]:
            # Must share subject or object instance
            e1_instances = {e1.get("instance_sub"), e1.get("instance_obj")} - {None}
            e2_instances = {e2.get("instance_sub"), e2.get("instance_obj")} - {None}
            if not e1_instances & e2_instances:
                continue

            # Must be within frame window
            f1 = e1.get("frame_id", frame_id)
            f2 = e2.get("frame_id", frame_id)
            if abs(f2 - f1) > CAUSAL_FRAME_WINDOW:
                continue

            # Must be a coherent transition (normalise semantic → base types first)
            base1 = _normalise_event_type(e1["type"])
            base2 = _normalise_event_type(e2["type"])
            if base2 not in COHERENT_TRANSITIONS.get(base1, []):
                continue

            # Confidence decays with frame distance
            frame_dist = abs(f2 - f1)
            confidence = max(0.3, 1.0 - frame_dist / CAUSAL_FRAME_WINDOW * 0.7)

            chains.append({
                "id":         str(uuid.uuid4()),
                "event_1_id": e1.get("id"),
                "event_2_id": e2.get("id"),
                "event_1_type": e1["type"],
                "event_2_type": e2["type"],
                "shared_instance": next(iter(e1_instances & e2_instances)),
                "confidence": confidence,
                "frame_id":   frame_id,
            })

    return chains


def write_causal_chains(conn: sqlite3.Connection, chains: list[dict]):
    """
    Write causal chains to relations table as 'leads_to' edges.
    edge_type = 'causal_inferred' — never promoted to truth without SSRE validation.
    """
    for c in chains:
        rel_id = str(uuid.uuid4())
        conn.execute("""
            INSERT OR IGNORE INTO relations (
                id, subject_id, predicate, object_id,
                edge_type, predicate_layer, confidence,
                source_dataset, frame_id, domain_tags
            ) VALUES (?, ?, 'leads_to', ?,
                      'causal_inferred', 'relational', ?,
                      'vision_causal', ?, 'vision')
        """, (
            rel_id,
            c["event_1_type"],   # using event type as subject/object here
            c["event_2_type"],   # (anchored to event type string, not anchor_id)
            c["confidence"],
            c["frame_id"],
        ))


def update_sequence_memory(conn: sqlite3.Connection, events: list[dict]):
    """
    Record 3-event sequences in the sequences table for prediction.
    Increments frequency on repeat — builds up pattern memory over time.
    """
    keys = [_event_key(e) for e in events]
    for i in range(len(keys) - 2):
        e1, e2, e3 = keys[i], keys[i+1], keys[i+2]
        conn.execute("""
            INSERT INTO sequences (id, event_1, event_2, event_3, frequency)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(event_1, event_2, event_3)
            DO UPDATE SET frequency = frequency + 1
        """, (str(uuid.uuid4()), e1, e2, e3))


def predict_next_event(conn: sqlite3.Connection,
                       recent_events: list[dict], top_k: int = 3) -> list[dict]:
    """
    Given the last 2 events, predict the most likely next event type.
    Returns list of {event_type, frequency, confidence} sorted by frequency desc.
    """
    if len(recent_events) < 2:
        return []
    e1 = _event_key(recent_events[-2])
    e2 = _event_key(recent_events[-1])

    rows = conn.execute("""
        SELECT event_3, frequency
        FROM sequences
        WHERE event_1 = ? AND event_2 = ?
        ORDER BY frequency DESC
        LIMIT ?
    """, (e1, e2, top_k)).fetchall()

    if not rows:
        return []

    total = sum(r[1] for r in rows)
    return [{"event_type": r[0], "frequency": r[1],
             "confidence": r[1] / total} for r in rows]
