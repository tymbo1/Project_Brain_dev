"""
reltr_decoder.py — RelTR scene graph inference + decoding.

Loads RelTR pretrained on Visual Genome.
Returns (subject_instance_id, raw_predicate, object_instance_id, confidence) tuples
that plug directly into image_ingest._project_to_cms().

Weights: ~/RelTR/checkpoints/reltr.pth
VG label files: ~/RelTR/data/vg/vg_list.py (or generated below)
"""
from __future__ import annotations
import sys
import json
from pathlib import Path

import torch
import torch.nn.functional as F
import torchvision.transforms as T
from PIL import Image

RELTR_DIR  = Path.home() / "RelTR"
CKPT_PATH  = RELTR_DIR / "checkpoints" / "reltr.pth"
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"
CONF_THRESH = 0.3

# ── VG label lists ────────────────────────────────────────────────────────────
# Embedded here to avoid dataset dependency at inference time.
# 150 object classes + 50 predicate classes from Visual Genome split.

VG_OBJ_CLASSES = [
    "__background__", "airplane", "animal", "arm", "bag", "banana", "basket",
    "beach", "bear", "bed", "bench", "bike", "bird", "board", "boat",
    "book", "boot", "bottle", "bowl", "box", "boy", "branch", "building",
    "bus", "cabinet", "cap", "car", "cat", "chair", "child", "clock",
    "coat", "counter", "cow", "cup", "curtain", "desk", "dog", "door",
    "drawer", "ear", "elephant", "engine", "eye", "face", "fence", "finger",
    "flag", "flower", "food", "fork", "fruit", "giraffe", "girl", "glass",
    "glove", "guy", "hair", "hand", "handle", "hat", "head", "helmet",
    "hill", "horse", "house", "jacket", "jean", "kid", "kite", "lady",
    "lamp", "laptop", "leaf", "leg", "letter", "light", "logo", "man",
    "men", "motorcycle", "mountain", "mouth", "neck", "nose", "orange",
    "pant", "paper", "people", "person", "phone", "pillow", "pizza",
    "plane", "plant", "plate", "player", "pole", "post", "pot", "racket",
    "railing", "rock", "roof", "room", "screen", "seat", "sheep", "shelf",
    "shirt", "shoe", "short", "sidewalk", "sign", "sink", "skateboard",
    "ski", "sky", "snow", "sock", "stand", "street", "surf", "surfboard",
    "table", "tail", "tie", "tile", "tire", "toilet", "towel", "tower",
    "track", "train", "tree", "truck", "trunk", "umbrella", "vase",
    "vegetable", "vehicle", "wave", "wheel", "window", "windshield",
    "wing", "wire", "woman", "zebra",
]

VG_REL_CLASSES = [
    "__background__", "above", "across", "against", "along", "and",
    "at", "attached to", "behind", "belonging to", "between", "carrying",
    "covered in", "covering", "eating", "flying in", "for", "from",
    "growing on", "hanging from", "has", "holding", "in", "in front of",
    "laying on", "looking at", "lying on", "made of", "mounted on",
    "near", "of", "on", "on back of", "over", "painted on", "parked on",
    "part of", "playing", "riding", "says", "sitting on", "standing on",
    "to", "under", "using", "walking in", "walking on", "watching",
    "wearing", "wired to", "with",
]

# ── Model loading ─────────────────────────────────────────────────────────────

_model = None

def _load_model():
    global _model
    if _model is not None:
        return _model

    if not CKPT_PATH.exists():
        raise FileNotFoundError(f"RelTR weights not found: {CKPT_PATH}")

    sys.path.insert(0, str(RELTR_DIR))

    # Build model directly — avoids importing engine.py (eval dependency chain)
    import argparse
    from models.backbone import build_backbone
    from models.transformer import build_transformer
    from models.reltr import RelTR

    args = argparse.Namespace(
        backbone="resnet50", dilation=False, position_embedding="sine",
        enc_layers=6, dec_layers=6, dim_feedforward=2048, hidden_dim=256,
        dropout=0.1, nheads=8, num_entities=100, num_triplets=200,
        dataset="vg", device=DEVICE,
        lr_backbone=0.0, frozen_weights=None, pre_norm=False,
        return_interm_layers=False, masks=False,
        aux_loss=False,
    )

    backbone    = build_backbone(args)
    transformer = build_transformer(args)
    model = RelTR(
        backbone, transformer,
        num_classes=151,        # VG object classes
        num_rel_classes=51,     # VG predicate classes
        num_entities=args.num_entities,
        num_triplets=args.num_triplets,
        aux_loss=args.aux_loss,
    )
    import argparse as _argparse
    torch.serialization.add_safe_globals([_argparse.Namespace])
    ckpt = torch.load(CKPT_PATH, map_location=DEVICE, weights_only=True)
    model.load_state_dict(ckpt["model"])
    model.to(DEVICE)
    model.eval()
    if DEVICE == "cuda":
        torch.backends.cudnn.benchmark = True
    _model = model
    return model


_transform = T.Compose([
    T.Resize(800),
    T.ToTensor(),
    T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
])


# ── Decoding ──────────────────────────────────────────────────────────────────

def _decode_outputs(outputs: dict) -> list[tuple[str, str, str, float]]:
    """
    Decode RelTR tensor outputs into (subj_label, pred, obj_label, confidence).
    """
    pred_logits = outputs["rel_logits"][0]   # (num_triplets, num_preds)
    sub_logits  = outputs["sub_logits"][0]   # (num_triplets, num_obj_classes)
    obj_logits  = outputs["obj_logits"][0]   # (num_triplets, num_obj_classes)

    results = []
    for i in range(pred_logits.shape[0]):
        pred_prob  = F.softmax(pred_logits[i], dim=-1)
        pred_score, pred_idx = pred_prob.max(dim=-1)

        if pred_score.item() < CONF_THRESH:
            continue
        pid = pred_idx.item()
        if pid == 0 or pid >= len(VG_REL_CLASSES):   # background / DETR no-pred
            continue

        subj_idx = sub_logits[i].argmax().item()
        obj_idx  = obj_logits[i].argmax().item()

        # Index 0 = explicit background; index >= num_classes = DETR no-object slot
        if subj_idx == 0 or subj_idx >= len(VG_OBJ_CLASSES):
            continue
        if obj_idx == 0 or obj_idx >= len(VG_OBJ_CLASSES):
            continue

        subj  = VG_OBJ_CLASSES[subj_idx]
        obj   = VG_OBJ_CLASSES[obj_idx]
        pred  = VG_REL_CLASSES[pid]

        results.append((subj, pred, obj, float(pred_score)))

    # Sort by confidence, deduplicate
    results.sort(key=lambda x: -x[3])
    seen = set()
    deduped = []
    for r in results:
        key = (r[0], r[1], r[2])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped[:20]   # cap at 20 relations per image


# VG uses fine-grained human/vehicle terms; map to COCO equivalents
_VG_TO_COCO: dict[str, str] = {
    "man": "person", "woman": "person", "men": "person", "boy": "person",
    "girl": "person", "guy": "person", "kid": "person", "child": "person",
    "lady": "person", "people": "person", "player": "person",
    "motorcycle": "motorcycle", "motorbike": "motorcycle",
    "plane": "airplane", "aeroplane": "airplane",
}


def _match_to_instance(label: str, instances: list[dict],
                        used: set) -> str | None:
    """
    Match a VG label to the closest unused instance by label similarity.
    Falls back to any instance with matching label.
    """
    norm = _VG_TO_COCO.get(label.lower().strip(), label.lower().strip())
    for inst in instances:
        inst_label = inst["label"].lower()
        if inst_label == norm or norm in inst_label or inst_label in norm:
            if inst["instance_id"] not in used:
                used.add(inst["instance_id"])
                return inst["instance_id"]
    # Fallback: any matching instance even if used
    for inst in instances:
        inst_label = inst["label"].lower()
        if inst_label == norm or norm in inst_label or inst_label in norm:
            return inst["instance_id"]
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def decode_image(image_path: str, instances: list[dict]) -> list[dict]:
    """
    Run RelTR on image_path. Map decoded labels to existing instances.
    Returns list of relation dicts compatible with image_ingest._project_to_cms().
    """
    model = _load_model()
    img = Image.open(image_path).convert("RGB")
    img_t = _transform(img).unsqueeze(0).to(DEVICE)

    with torch.amp.autocast("cuda", enabled=(DEVICE == "cuda")):
        with torch.no_grad():
            outputs = model(img_t)

    raw_rels = _decode_outputs(outputs)

    rels = []
    used_subjects = set()
    used_objects  = set()

    for subj_label, pred, obj_label, conf in raw_rels:
        subj_inst = _match_to_instance(subj_label, instances, used_subjects)
        obj_inst  = _match_to_instance(obj_label,  instances, used_objects)
        if not subj_inst or not obj_inst or subj_inst == obj_inst:
            continue

        # Look up anchor IDs from instance records
        subj_anchor = next((i["anchor_id"] for i in instances
                            if i["instance_id"] == subj_inst), None)
        obj_anchor  = next((i["anchor_id"] for i in instances
                            if i["instance_id"] == obj_inst), None)
        if not subj_anchor or not obj_anchor:
            continue

        rels.append({
            "subject_instance": subj_inst,
            "object_instance":  obj_inst,
            "subject_anchor":   subj_anchor,
            "object_anchor":    obj_anchor,
            "raw_predicate":    pred,
            "confidence":       conf,
        })

    return rels
