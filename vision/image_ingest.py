#!/usr/bin/env python3
"""
image_ingest.py — Static image → CMS ingestion pipeline.

Two-layer architecture (GPT architectural invariant):
  Instance layer  — WHICH one it is (person#0, person#1, per-image/frame)
  Canonical layer — WHAT it is      (person → CMS anchor, deduped, truth)

Flow:
    image
      → YOLO detection
      → instance records (instances table)
      → instance relations (instance_relations table)
      → canonical projection (relations table — abstracted truth)
      → image provenance capsule

Usage:
    source ~/vision_env/bin/activate
    python3 vision/image_ingest.py <image_path> [--dry-run] [--model=yolov8s.pt]
"""
import sys
import json
import math
import sqlite3
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vision.anchor_resolver   import AnchorResolver
from vision.relation_inserter import RelationInserter
from vision.yolo_detector     import detect, Detection

DB_PATH = Path.home() / "resonance_v11.db"

DRY_RUN    = "--dry-run" in sys.argv
MODEL_NAME = next((a.split("=")[1] for a in sys.argv if a.startswith("--model=")), "yolov8n.pt")
CONF_THRESH = 0.40
ADJ_THRESH  = 200   # pixel distance threshold for spatial adjacency


# ── Instance layer ────────────────────────────────────────────────────────────

def _make_instances(detections: list[Detection], anchor_ids: dict[int, str],
                    image_id: str) -> list[dict]:
    """
    Build instance records — one per detection, keyed by detection index.
    instance_id = f"{label}_{index}" (local, per-image identity)
    """
    instances = []
    for i, det in enumerate(detections):
        instances.append({
            "instance_id": f"{det.label}_{i}",
            "anchor_id":   anchor_ids[i],
            "label":       det.label,
            "image_id":    image_id,
            "bbox":        json.dumps(list(det.bbox)),
            "confidence":  det.confidence,
        })
    return instances


def _spatial_instance_relations(instances: list[dict],
                                  detections: list[Detection]) -> list[dict]:
    """
    Generate spatial relations between instances from bbox geometry.
    Returns list of relation dicts.
    """
    rels = []
    for i, a in enumerate(detections):
        for j, b in enumerate(detections):
            if i == j:
                continue
            dx = b.center[0] - a.center[0]
            dy = b.center[1] - a.center[1]
            dist = math.sqrt(dx*dx + dy*dy)
            if dist > ADJ_THRESH:
                continue
            conf = max(0.5, 1.0 - dist / ADJ_THRESH)

            # Determine predicate from geometry
            if abs(dy) > abs(dx):
                raw_pred = "above" if dy > 0 else "below"
            else:
                raw_pred = "left of" if dx < 0 else "right of"

            rels.append({
                "subject_instance": instances[i]["instance_id"],
                "object_instance":  instances[j]["instance_id"],
                "subject_anchor":   instances[i]["anchor_id"],
                "object_anchor":    instances[j]["anchor_id"],
                "raw_predicate":    raw_pred,
                "confidence":       conf,
            })
    return rels


def _try_reltr(image_path: str, instances: list[dict]) -> list[dict]:
    """
    Run RelTR scene graph if available.
    Returns relation dicts with subject/object instance IDs.
    Silently skips if not installed.
    """
    try:
        from vision.reltr_decoder import decode_image
        return decode_image(image_path, instances)
    except ImportError:
        return []
    except Exception as e:
        print(f"  [reltr] skipped: {e}")
        return []


# ── CMS canonical projection ──────────────────────────────────────────────────

def _project_to_cms(conn: sqlite3.Connection, inserter: RelationInserter,
                     rels: list[dict], image_id: str, dry_run: bool):
    """
    Project instance relations to CMS canonical layer.
    Writes to instance_relations (instance layer) and relations (canonical layer).

    CMS gets abstracted truth: person spatial_on chair (not person#0 spatial_on chair#1).
    Instance layer preserves the specific occurrence.
    """
    from vision.predicate_normalizer import normalize

    for rel in rels:
        cms_pred, pred_type = normalize(rel["raw_predicate"])
        if not dry_run:
            # Instance layer
            conn.execute("""
                INSERT INTO instance_relations
                    (id, subject_instance, predicate, object_instance,
                     raw_predicate, predicate_type, confidence, image_id, source_dataset)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'coco')
            """, (
                str(uuid.uuid4()),
                rel["subject_instance"], cms_pred, rel["object_instance"],
                rel["raw_predicate"], pred_type, rel["confidence"], image_id,
            ))
            # Canonical CMS projection
            inserter.insert(
                rel["subject_anchor"], rel["raw_predicate"], rel["object_anchor"],
                confidence=rel["confidence"],
            )
        label_s = rel["subject_instance"].rsplit("_", 1)[0]
        label_o = rel["object_instance"].rsplit("_", 1)[0]
        print(f"  {'[DRY] ' if dry_run else ''}rel: {rel['subject_instance']} "
              f"—[{rel['raw_predicate']}]→ {rel['object_instance']}  "
              f"(cms: {label_s} {cms_pred} {label_o}, conf={rel['confidence']:.2f})")


# ── Main ──────────────────────────────────────────────────────────────────────

def ingest_image(image_path: str) -> dict:
    image_path = str(Path(image_path).resolve())
    image_id   = f"img_{uuid.uuid4().hex[:12]}"
    print(f"\nIngesting: {image_path}")

    detections = detect(image_path, model_name=MODEL_NAME, conf_thresh=CONF_THRESH)
    if not detections:
        print("  No objects detected.")
        return {"image": image_path, "objects": 0, "relations": 0}

    labels = [d.label for d in detections]
    print(f"  Detected {len(detections)}: {', '.join(f'{l}_{i}' for i,l in enumerate(labels))}")

    conn     = sqlite3.connect(DB_PATH, timeout=30)
    resolver = AnchorResolver(conn)
    inserter = RelationInserter(conn, source_dataset="coco")

    # Resolve each detection to canonical anchor (by index, not label)
    anchor_ids = {i: resolver.resolve(det.label) for i, det in enumerate(detections)}

    # Build instance records
    instances = _make_instances(detections, anchor_ids, image_id)
    if not DRY_RUN:
        for inst in instances:
            conn.execute("""
                INSERT OR IGNORE INTO instances
                    (instance_id, anchor_id, image_id, bbox, confidence)
                VALUES (?, ?, ?, ?, ?)
            """, (inst["instance_id"], inst["anchor_id"],
                  inst["image_id"], inst["bbox"], inst["confidence"]))

    # Spatial relations from geometry
    rels = _spatial_instance_relations(instances, detections)

    # RelTR scene graph (plugs in when model available)
    reltr_rels = _try_reltr(image_path, instances)
    rels.extend(reltr_rels)

    # Project to CMS
    _project_to_cms(conn, inserter, rels, image_id, DRY_RUN)

    # Image capsule
    if not DRY_RUN:
        cap_id = f"vision_img_{uuid.uuid4().hex[:12]}"
        conn.execute("""
            INSERT INTO capsules (id, capsule_type, domain, source, title, metadata, created_at)
            VALUES (?, 'vision_image', 'vision', 'image_ingest', ?, ?, ?)
        """, (cap_id, Path(image_path).name, json.dumps({
            "image_id": image_id, "image_path": image_path,
            "objects": labels, "object_count": len(detections),
            "relation_count": inserter.stats["inserted"],
            "model": MODEL_NAME, "ingested_at": time.time(),
        }), time.time()))
        conn.commit()

    stats = inserter.stats
    print(f"  → {stats['inserted']} canonical relations, {stats['deduped']} deduped")
    if DRY_RUN:
        print("  [DRY RUN] nothing written")
    conn.close()
    return {"image": image_path, "objects": len(detections),
            "relations": stats["inserted"], "deduped": stats["deduped"]}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage: python3 vision/image_ingest.py <image> [--dry-run] [--model=yolov8s.pt]")
        sys.exit(1)
    for path in args:
        if not Path(path).exists():
            print(f"File not found: {path}")
            continue
        ingest_image(path)


if __name__ == "__main__":
    main()
