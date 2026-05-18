#!/usr/bin/env python3
"""
video_ingest.py — Video → CMS temporal ingestion pipeline.

Flow per frame:
    frame → YOLO detection → tracker (persistent instance_ids)
         → spatial instance relations
         → RelTR scene graph (if available)
         → CMS write (instances + instance_relations + canonical projection)
         → transition detection (appeared / disappeared)
         → event inference → events table

Usage:
    source ~/vision_env/bin/activate
    python3 vision/video_ingest.py <video_path_or_webcam> [options]

    --frame-skip=N     process every N frames (default: 5)
    --max-frames=N     stop after N processed frames (default: unlimited)
    --dry-run          detect and track but don't write to CMS
    --model=yolov8s.pt use a different YOLO model
    --show             display OpenCV overlay window
"""
import sys
import json
import sqlite3
import time
import uuid
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).parent.parent))

from vision.anchor_resolver   import AnchorResolver
from vision.relation_inserter import RelationInserter
from vision.tracker           import ObjectTracker, Track
from vision.yolo_detector     import detect
from vision.image_ingest      import _spatial_instance_relations, _project_to_cms, _try_reltr
from vision.causal_chain      import (build_causal_chains, write_causal_chains,
                                       update_sequence_memory, predict_next_event,
                                       _event_key)
from vision.ssre_anomaly_validator import validate_pending as ssre_validate

DB_PATH = Path.home() / "resonance_v11.db"

# ── Arg parsing ───────────────────────────────────────────────────────────────

DRY_RUN    = "--dry-run"  in sys.argv
SHOW       = "--show"     in sys.argv
FRAME_SKIP = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--frame-skip=")),  "5"))
MAX_FRAMES = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--max-frames=")), "0")) or None
MODEL_NAME = next((a.split("=")[1] for a in sys.argv if a.startswith("--model=")), "yolov8n.pt")
CONF_THRESH = 0.40

# Frames a RelTR semantic event is suppressed after first firing
RELTR_DEDUP_WINDOW = 15
# Minimum sequence frequency before a prediction is shown
PREDICTION_MIN_FREQ = 3
# Event pair seen fewer than this many times total → flagged as anomalous
ANOMALY_FREQ_THRESHOLD = 3


# ── Transition detection ──────────────────────────────────────────────────────

def detect_transitions(prev_ids: set[str], curr_ids: set[str]) -> tuple[set, set]:
    return curr_ids - prev_ids, prev_ids - curr_ids   # appeared, disappeared


# ── Event type resolution — semantic > geometric ──────────────────────────────

# COCO/VG categories → semantic class for event naming
_ANIMATE   = {"person", "man", "woman", "child", "boy", "girl", "animal",
               "dog", "cat", "horse", "bird", "elephant", "bear", "cow",
               "sheep", "zebra", "giraffe"}
_VEHICLE   = {"car", "bus", "truck", "train", "motorcycle", "bicycle",
               "airplane", "boat"}
_CONTAINER = {"cup", "bowl", "bottle", "box", "bag", "suitcase", "vase"}


def _event_type(instance_id: str, tracks: "list | None" = None,
                transition: str = "entered") -> str:
    """
    Return a semantically typed event string.
    Falls back to label-prefixed generic if label not in known categories.
    """
    label = instance_id.rsplit("_", 1)[0] if "_" in instance_id else instance_id

    if transition == "entered":
        if label in _ANIMATE:   return f"{label}_entered"
        if label in _VEHICLE:   return f"{label}_appeared"
        return f"object_detected:{label}"
    else:  # left
        if label in _ANIMATE:   return f"{label}_left"
        if label in _VEHICLE:   return f"{label}_departed"
        return f"object_removed:{label}"


# ── Event inference ───────────────────────────────────────────────────────────

_REL_EVENT_MAP = {
    ("interacts_with", "appeared"):    "interaction_start",
    ("interacts_with", "disappeared"): "interaction_end",
    ("spatial_on",     "appeared"):    "placement",
    ("spatial_on",     "disappeared"): "removal",
    ("spatial_under",  "appeared"):    "placement",
    ("spatial_adjacent","appeared"):   "proximity_start",
    ("spatial_adjacent","disappeared"):"proximity_end",
}

def infer_events(appeared: set, disappeared: set,
                 prev_rels: list, curr_rels: list) -> list[dict]:
    events = []
    ts = time.time()

    for inst_id in appeared:
        events.append({"type": _event_type(inst_id, transition="entered"),
                        "instance_sub": inst_id, "vis_timestamp": ts})
    for inst_id in disappeared:
        events.append({"type": _event_type(inst_id, transition="left"),
                        "instance_sub": inst_id, "vis_timestamp": ts})

    # Relation-level events
    prev_set = {(r["subject_instance"], r["raw_predicate"], r["object_instance"])
                for r in prev_rels}
    curr_set = {(r["subject_instance"], r["raw_predicate"], r["object_instance"])
                for r in curr_rels}

    for s, p, o in curr_set - prev_set:
        from vision.predicate_normalizer import normalize
        cms_pred, _ = normalize(p)
        event_type = _REL_EVENT_MAP.get((cms_pred, "appeared"), "relation_formed")
        s_label = s.rsplit("_", 1)[0]
        o_label = o.rsplit("_", 1)[0]
        # Semantic labelling: person picked_up cup, not generic interaction_start
        if cms_pred == "interacts_with" and s_label in _ANIMATE:
            event_type = f"{s_label}_interacts_with_{o_label}"
        events.append({"type": event_type, "instance_sub": s,
                        "instance_obj": o, "vis_timestamp": ts})

    for s, p, o in prev_set - curr_set:
        from vision.predicate_normalizer import normalize
        cms_pred, _ = normalize(p)
        event_type = _REL_EVENT_MAP.get((cms_pred, "disappeared"), "relation_ended")
        events.append({"type": event_type, "instance_sub": s,
                        "instance_obj": o, "vis_timestamp": ts})

    return events


# RelTR semantic predicate → event type (static, single-frame)
_RELTR_EVENT_MAP: dict[str, str] = {
    "holding":    "interaction_start",
    "grasping":   "interaction_start",
    "carrying":   "interaction_start",
    "eating":     "interaction_start",
    "using":      "interaction_start",
    "playing":    "interaction_start",
    "riding":     "interaction_start",
    "wearing":    "relation_formed",
    "on":         "placement",
    "sitting on": "placement",
    "standing on":"placement",
    "lying on":   "placement",
    "laying on":  "placement",
    "mounted on": "placement",
    "parked on":  "placement",
    "near":       "proximity_start",
    "next to":    "proximity_start",
    "looking at": "relation_formed",
    "watching":   "relation_formed",
}


def reltr_to_events(reltr_rels: list[dict],
                    frame_id: int,
                    seen: dict[str, int]) -> list[dict]:
    """
    Convert static RelTR semantic relations into events.
    `seen` maps event_key → last frame_id it fired; prevents spam across frames.
    Mutates `seen` in place. Caller passes the same dict across frames.
    """
    events = []
    ts = time.time()
    for rel in reltr_rels:
        raw_pred = rel.get("raw_predicate", "")
        event_type = _RELTR_EVENT_MAP.get(raw_pred)
        if not event_type:
            continue
        s = rel["subject_instance"]
        o = rel["object_instance"]
        s_label = s.rsplit("_", 1)[0]
        o_label = o.rsplit("_", 1)[0]
        if event_type == "interaction_start" and s_label in _ANIMATE:
            event_type = f"{s_label}_interacts_with_{o_label}"
        key = f"{event_type}:{s}:{o}"
        last = seen.get(key, -RELTR_DEDUP_WINDOW - 1)
        if frame_id - last < RELTR_DEDUP_WINDOW:
            continue   # suppress — already fired recently
        seen[key] = frame_id
        events.append({"type": event_type, "instance_sub": s,
                        "instance_obj": o, "vis_timestamp": ts,
                        "source": "reltr", "raw_predicate": raw_pred})
    return events


def detect_anomalies(conn: sqlite3.Connection,
                     events: list[dict],
                     frame_id: int) -> list[dict]:
    """
    Check consecutive event pairs against sequence memory.
    A pair is anomalous when its total observed frequency is below threshold —
    the system has never (or rarely) seen this transition before.

    Returns list of anomaly dicts, sorted by anomaly_score desc.
    Does NOT write to DB — caller decides whether to persist.
    """
    keys = [_event_key(e) for e in events]
    anomalies = []
    for i in range(len(keys) - 1):
        e1, e2 = keys[i], keys[i + 1]
        # Pair may appear as (event_1,event_2) or (event_2,event_3) in 3-grams
        row = conn.execute("""
            SELECT COALESCE(SUM(frequency), 0) FROM sequences
            WHERE (event_1 = ? AND event_2 = ?)
               OR (event_2 = ? AND event_3 = ?)
        """, (e1, e2, e1, e2)).fetchone()
        total_freq = row[0] if row else 0
        if total_freq < ANOMALY_FREQ_THRESHOLD:
            score = 1.0 - total_freq / ANOMALY_FREQ_THRESHOLD
            # What did the system expect after e1?
            expected_rows = conn.execute("""
                SELECT event_2, SUM(frequency) as freq
                FROM sequences WHERE event_1 = ?
                GROUP BY event_2
                UNION ALL
                SELECT event_3, SUM(frequency) as freq
                FROM sequences WHERE event_2 = ?
                GROUP BY event_3
                ORDER BY freq DESC LIMIT 3
            """, (e1, e1)).fetchall()
            expected = [{"event": r[0], "freq": r[1]} for r in expected_rows if r[0]]
            anomalies.append({
                "event_1":       e1,
                "event_2":       e2,
                "total_freq":    total_freq,
                "anomaly_score": round(score, 3),
                "frame_id":      frame_id,
                "expected":      expected,
            })

    anomalies.sort(key=lambda x: -x["anomaly_score"])
    return anomalies


def write_anomalies(conn: sqlite3.Connection, anomalies: list[dict]):
    """
    Persist anomalies to hypotheses table for SSRE review.
    Maps: subject=event_1, predicate='sequence_anomaly', object=event_2,
          confidence=anomaly_score, ssre_score=total_freq (raw frequency evidence).
    """
    for a in anomalies:
        conn.execute("""
            INSERT INTO hypotheses
                (id, subject, predicate, object, confidence, source, image_id,
                 created_at, validated, ssre_score)
            VALUES (?, ?, 'sequence_anomaly', ?, ?, ?, ?, ?, 0, ?)
        """, (
            str(uuid.uuid4()),
            a["event_1"],
            a["event_2"],
            a["anomaly_score"],
            json.dumps({"expected": a.get("expected", []), "source": "vision_anomaly"}),
            str(a["frame_id"]),
            time.time(),
            a["total_freq"],
        ))


def promote_learned_anomalies(conn: sqlite3.Connection) -> list[dict]:
    """
    Check all unvalidated sequence_anomaly hypotheses.
    If the pair's frequency in sequence memory now meets ANOMALY_FREQ_THRESHOLD,
    mark validated=1 (learned) and return the promoted pairs for logging.

    Called each frame after update_sequence_memory so promotions are immediate.
    """
    pending = conn.execute("""
        SELECT id, subject, object FROM hypotheses
        WHERE predicate = 'sequence_anomaly' AND validated = 0
    """).fetchall()

    promoted = []
    for hyp_id, e1, e2 in pending:
        row = conn.execute("""
            SELECT COALESCE(SUM(frequency), 0) FROM sequences
            WHERE (event_1 = ? AND event_2 = ?)
               OR (event_2 = ? AND event_3 = ?)
        """, (e1, e2, e1, e2)).fetchone()
        freq = row[0] if row else 0
        if freq >= ANOMALY_FREQ_THRESHOLD:
            conn.execute("""
                UPDATE hypotheses SET validated = 1, ssre_score = ?
                WHERE id = ?
            """, (freq, hyp_id))
            promoted.append({"event_1": e1, "event_2": e2, "freq": freq})

    return promoted


def write_events(conn: sqlite3.Connection, events: list[dict],
                 frame_id: int, video_id: str):
    for ev in events:
        conn.execute("""
            INSERT INTO events
                (id, type, subject_id, object_id, instance_sub, instance_obj,
                 frame_id, vis_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()),
            ev["type"],
            ev.get("instance_sub"),
            ev.get("instance_obj"),
            ev.get("instance_sub"),
            ev.get("instance_obj"),
            frame_id,
            ev.get("vis_timestamp"),
        ))


# ── Overlay ───────────────────────────────────────────────────────────────────

def draw_overlay(frame, tracks: list[Track], rels: list[dict]) -> None:
    for t in tracks:
        x1, y1, x2, y2 = (int(v) for v in t.bbox)
        label = f"{t.label}#{t.instance_id[-4:]}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(frame, label, (x1, y1 - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

    y_offset = 30
    for rel in rels[:5]:
        s = rel["subject_instance"].rsplit("_", 1)[0]
        o = rel["object_instance"].rsplit("_", 1)[0]
        text = f"{s} {rel['raw_predicate']} {o}"
        cv2.putText(frame, text, (10, y_offset),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 100, 0), 1)
        y_offset += 20


# ── Main loop ─────────────────────────────────────────────────────────────────

def ingest_video(source) -> dict:
    # Accept int (webcam index) or path string
    try:
        source = int(source)
    except (ValueError, TypeError):
        source = str(source)

    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"Cannot open: {source}")
        return {}

    video_id   = f"vid_{uuid.uuid4().hex[:10]}"
    tracker    = ObjectTracker()
    conn       = sqlite3.connect(DB_PATH, timeout=30)
    resolver   = AnchorResolver(conn)
    inserter   = RelationInserter(conn, source_dataset="coco")

    total_frames = 0
    processed    = 0
    prev_inst_ids: set[str] = set()
    prev_rels:     list     = []
    reltr_seen:    dict     = {}   # event_key → last frame_id (dedup window)

    print(f"Video ingest: {source}  skip={FRAME_SKIP}  max={MAX_FRAMES or '∞'}")
    print(f"{'[DRY RUN] ' if DRY_RUN else ''}writing to {DB_PATH}\n")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            total_frames += 1
            if total_frames % FRAME_SKIP != 0:
                continue

            processed += 1
            if MAX_FRAMES and processed > MAX_FRAMES:
                break

            # Save frame to temp file for YOLO (expects path)
            tmp = f"/tmp/vid_frame_{total_frames}.jpg"
            cv2.imwrite(tmp, frame)

            detections = detect(tmp, model_name=MODEL_NAME, conf_thresh=CONF_THRESH)
            if not detections:
                if SHOW:
                    cv2.imshow("CMS Vision", frame)
                    if cv2.waitKey(1) == 27:
                        break
                continue

            # Resolve anchors by index
            anchor_ids = {i: resolver.resolve(d.label)
                          for i, d in enumerate(detections)}

            # Track — persistent instance_ids
            tracks = tracker.update(detections, anchor_ids)
            curr_inst_ids = {t.instance_id for t in tracks}

            # Build instance dicts (same structure as image_ingest)
            instances = [{
                "instance_id": t.instance_id,
                "anchor_id":   t.anchor_id,
                "label":       t.label,
                "image_id":    video_id,
                "bbox":        json.dumps(list(t.bbox)),
                "confidence":  t.confidence,
            } for t in tracks]

            # Write instance records
            if not DRY_RUN:
                for inst in instances:
                    conn.execute("""
                        INSERT OR IGNORE INTO instances
                            (instance_id, anchor_id, image_id, frame_id, bbox, confidence)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (inst["instance_id"], inst["anchor_id"], inst["image_id"],
                          tracker.frame_id, inst["bbox"], inst["confidence"]))

            # Relations
            curr_rels = _spatial_instance_relations(instances, detections)
            reltr_rels = _try_reltr(tmp, instances)
            all_rels = curr_rels + reltr_rels

            _project_to_cms(conn, inserter, all_rels, video_id, DRY_RUN)

            # Transitions + events (spatial geometry)
            appeared, disappeared = detect_transitions(prev_inst_ids, curr_inst_ids)
            events = infer_events(appeared, disappeared, prev_rels, all_rels)

            # Semantic events from RelTR (augment, never replace; deduped)
            if reltr_rels:
                events = events + reltr_to_events(reltr_rels, tracker.frame_id, reltr_seen)

            if events and not DRY_RUN:
                write_events(conn, events, tracker.frame_id, video_id)

            # Causal chains from recent events
            if len(events) >= 2:
                chains = build_causal_chains(events, tracker.frame_id)
                if chains and not DRY_RUN:
                    write_causal_chains(conn, chains)
                if not DRY_RUN:
                    update_sequence_memory(conn, events)
                    promoted = promote_learned_anomalies(conn)
                    for p in promoted:
                        print(f"  ✓ learned  [{p['event_1']}] → [{p['event_2']}]"
                              f"  (promoted from anomaly at freq={p['freq']})")

                # Anomaly detection (runs after memory update so freq is current)
                anomalies = detect_anomalies(conn, events, tracker.frame_id)
                if anomalies:
                    top_a = anomalies[0]
                    exp_str = ", ".join(
                        f"{e['event'].split(':')[0]} (×{e['freq']})"
                        for e in top_a["expected"][:2]
                    ) or "nothing known"
                    print(f"  ⚠ anomaly  [{top_a['event_1']}] → [{top_a['event_2']}]"
                          f"  score={top_a['anomaly_score']:.2f}  freq={top_a['total_freq']}"
                          f"  expected: {exp_str}")
                    if not DRY_RUN:
                        write_anomalies(conn, anomalies)

            # Predict next event (gated: only show freq >= PREDICTION_MIN_FREQ)
            if events:
                predictions = predict_next_event(conn, events)
                gated = [p for p in predictions if p["frequency"] >= PREDICTION_MIN_FREQ]
                if gated:
                    top = gated[0]
                    print(f"         predict → {top['event_type']} "
                          f"(conf={top['confidence']:.2f}, freq={top['frequency']})")

            if not DRY_RUN:
                conn.commit()

            # SSRE validation pass every 50 frames
            if not DRY_RUN and processed % 50 == 0:
                v = ssre_validate(conn)
                if v["confirmed"] + v["rejected"] > 0:
                    print(f"  [ssre] validated {v['confirmed']} confirmed,"
                          f" {v['rejected']} rejected, {v['pending']} pending")

            # Console summary
            labels = [t.label for t in tracks]
            ev_types = [e["type"] for e in events]
            print(f"  frame {tracker.frame_id:4d} | "
                  f"objects: {', '.join(f'{l}#{t.instance_id[-4:]}' for l,t in zip(labels,[*tracks]))} | "
                  f"rels: {len(all_rels)} | "
                  f"events: {ev_types or '—'}")

            # Overlay
            if SHOW:
                draw_overlay(frame, tracks, all_rels)
                cv2.imshow("CMS Vision", frame)
                if cv2.waitKey(1) == 27:
                    break

            prev_inst_ids = curr_inst_ids
            prev_rels     = all_rels

    finally:
        cap.release()
        if SHOW:
            cv2.destroyAllWindows()
        conn.close()

    stats = inserter.stats
    print(f"\nDone. frames={processed}, relations={stats['inserted']}, deduped={stats['deduped']}")
    return {"video_id": video_id, "frames": processed,
            "relations": stats["inserted"]}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage: python3 vision/video_ingest.py <video_file_or_webcam_index> [options]")
        print("  --frame-skip=5   --max-frames=100   --dry-run   --show   --model=yolov8s.pt")
        sys.exit(1)
    ingest_video(args[0])


if __name__ == "__main__":
    main()
