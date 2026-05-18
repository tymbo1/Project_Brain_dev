#!/usr/bin/env python3
"""
coco_anchor_analysis.py

Maps COCO 80-category labels against existing CMS anchors.
Reports: matched, fuzzy-matched, missing, and ontology bloat risk.

Run before any visual ingestion to understand anchor coverage.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "resonance_v11.db"

# COCO 80 object categories (canonical)
COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
    "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
]

# Normalization map — COCO label → preferred CMS canonical
# Applied before anchor lookup to prevent ontology bloat
NORMALIZE = {
    "tv":           "television",
    "cell phone":   "mobile phone",
    "couch":        "sofa",
    "hot dog":      "hotdog",
    "potted plant": "plant",
    "dining table": "table",
    "wine glass":   "glass",
    "hair drier":   "hair dryer",
    "sports ball":  "ball",
    "teddy bear":   "teddy bear",
}


def normalize(label: str) -> str:
    return NORMALIZE.get(label, label).lower()


def main():
    conn = sqlite3.connect(DB_PATH, timeout=10)

    # Load all existing anchor canonicals into a set for fast lookup
    all_canonicals = {
        row[0].lower()
        for row in conn.execute("SELECT canonical FROM anchors WHERE canonical IS NOT NULL")
    }

    print(f"CMS anchor count: {len(all_canonicals):,}")
    print(f"COCO categories:  {len(COCO_LABELS)}")
    print()

    matched      = []   # direct or normalized match
    fuzzy        = []   # partial substring match (candidate)
    missing      = []   # no match at all

    for label in COCO_LABELS:
        norm = normalize(label)

        if norm in all_canonicals:
            matched.append((label, norm, "exact"))
        elif label in all_canonicals:
            matched.append((label, label, "exact_raw"))
        else:
            # Fuzzy: check if any anchor contains the term or vice versa
            candidates = [
                c for c in all_canonicals
                if norm in c or c in norm
            ]
            if candidates:
                best = min(candidates, key=lambda c: abs(len(c) - len(norm)))
                fuzzy.append((label, norm, best))
            else:
                missing.append((label, norm))

    # ── Report ────────────────────────────────────────────────────────────────

    print(f"{'='*60}")
    print(f"MATCHED ({len(matched)})  — safe to resolve to existing anchor")
    print(f"{'='*60}")
    for label, resolved, how in sorted(matched):
        tag = f"  [{how}]" if how != "exact" else ""
        print(f"  {label:<22} → {resolved}{tag}")

    print()
    print(f"{'='*60}")
    print(f"FUZZY ({len(fuzzy)})  — candidate anchor exists, verify before use")
    print(f"{'='*60}")
    for label, norm, candidate in sorted(fuzzy):
        print(f"  {label:<22} → norm: {norm:<22} candidate: {candidate}")

    print()
    print(f"{'='*60}")
    print(f"MISSING ({len(missing)})  — will create new anchors (review for bloat)")
    print(f"{'='*60}")
    for label, norm in sorted(missing):
        tag = f"  [normalized: {norm}]" if norm != label else ""
        print(f"  {label}{tag}")

    print()
    print(f"{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    total = len(COCO_LABELS)
    print(f"  Exact match:    {len(matched):>3} / {total}  ({len(matched)/total*100:.0f}%)")
    print(f"  Fuzzy match:    {len(fuzzy):>3} / {total}  ({len(fuzzy)/total*100:.0f}%)")
    print(f"  New anchors:    {len(missing):>3} / {total}  ({len(missing)/total*100:.0f}%)")
    print()
    print(f"  Bloat risk: {'LOW' if len(missing) < 20 else 'MEDIUM' if len(missing) < 50 else 'HIGH'}")
    print(f"  All new anchors are concrete physical objects — ontology-safe.")

    conn.close()


if __name__ == "__main__":
    main()
