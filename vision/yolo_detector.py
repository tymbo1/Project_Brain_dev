"""
yolo_detector.py — YOLOv8 object detection wrapper.

Returns list of Detection namedtuples with label, confidence, bbox, center.
GPU-accelerated with mixed precision. Falls back to CPU if no CUDA.
"""
from __future__ import annotations
from dataclasses import dataclass

import torch

@dataclass
class Detection:
    label:      str
    confidence: float
    bbox:       tuple[float, float, float, float]  # x1, y1, x2, y2
    center:     tuple[float, float]                # cx, cy


_model = None
_device = "cuda" if torch.cuda.is_available() else "cpu"


def _load_model(model_name: str = "yolov8n.pt"):
    global _model
    if _model is None:
        from ultralytics import YOLO
        _model = YOLO(model_name)
        _model.to(_device)
        if _device == "cuda":
            torch.backends.cudnn.benchmark = True
    return _model


def detect(image_path: str, model_name: str = "yolov8n.pt",
           conf_thresh: float = 0.4) -> list[Detection]:
    """
    Run YOLO detection on image_path.
    Returns list of Detection objects sorted by confidence descending.
    """
    model = _load_model(model_name)

    with torch.amp.autocast("cuda", enabled=(_device == "cuda")):
        results = model(image_path, conf=conf_thresh, verbose=False)[0]

    detections = []
    names = results.names

    for box in results.boxes:
        cls_id = int(box.cls[0])
        conf   = float(box.conf[0])
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

        detections.append(Detection(
            label      = names[cls_id],
            confidence = conf,
            bbox       = (x1, y1, x2, y2),
            center     = (cx, cy),
        ))

    detections.sort(key=lambda d: -d.confidence)
    return detections
