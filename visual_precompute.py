#!/usr/bin/env python3
"""
visual_precompute.py — Seed the visual pattern layer for CMS / Selyrion

Pulls top-N anchors from CMS, generates prompt templates via LLM,
runs Stable Diffusion locally, extracts CLIP embeddings, scores quality,
and writes visual_prompt_templates + visual_embeddings to resonance_v11.db.
Raw images are NEVER stored — only embeddings and prompt recipes.

Install dependencies first:
    pip install diffusers transformers accelerate xformers torch torchvision
    pip install git+https://github.com/openai/CLIP.git
    pip install aesthetic-predictor-v2-5  # optional, for aesthetic scoring

Usage:
    python3 visual_precompute.py [--anchors=500] [--steps=20] [--dry-run]
    python3 visual_precompute.py --domain=emotional_resonance --anchors=100
"""

import sys, os, json, sqlite3, uuid, time, argparse, math, struct
from pathlib import Path
from collections import defaultdict

DB_PATH   = Path.home() / "resonance_v11.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "llama3:8b"

# ── CLI args ──────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--anchors",   type=int, default=200,   help="Anchors to process")
parser.add_argument("--steps",     type=int, default=20,    help="Diffusion steps")
parser.add_argument("--domain",    type=str, default=None,  help="Filter by expression domain")
parser.add_argument("--max-variants", type=int, default=4,  help="Max templates per anchor")
parser.add_argument("--dry-run",   action="store_true",     help="Show prompts without generating")
parser.add_argument("--width",     type=int, default=512)
parser.add_argument("--height",    type=int, default=512)
args = parser.parse_args()

# ── GPU power limit ───────────────────────────────────────────────────────────
GPU_POWER_LIMIT_W = 120

def _set_gpu_power_limit():
    import subprocess
    try:
        r = subprocess.run(
            ["nvidia-smi", "-pl", str(GPU_POWER_LIMIT_W)],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            print(f"  [GPU power limit set to {GPU_POWER_LIMIT_W}W]")
        else:
            print(f"  [WARNING: could not set GPU power limit — {r.stderr.strip()}]")
    except FileNotFoundError:
        print("  [WARNING: nvidia-smi not found, skipping power limit]")

# ── Thermal guard (reuse pattern from langeng_learn) ─────────────────────────
THROTTLE_SECS = 3
COOL_EVERY    = 5
COOL_SECS     = 60
TEMP_LIMIT    = 83
_gen_count    = 0

def _gpu_temp() -> int:
    import subprocess
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True
        )
        return int(r.stdout.strip())
    except Exception:
        return 0

def _thermal_pause():
    global _gen_count
    _gen_count += 1
    time.sleep(THROTTLE_SECS)
    if _gen_count % COOL_EVERY == 0:
        print(f"  [GPU cool — {COOL_SECS}s after {_gen_count} generations]")
        time.sleep(COOL_SECS)
    temp = _gpu_temp()
    while temp > TEMP_LIMIT:
        print(f"  [GPU at {temp}°C — waiting 30s]")
        time.sleep(30)
        temp = _gpu_temp()

# ── Domain → visual style map ─────────────────────────────────────────────────
DOMAIN_STYLE = {
    "emotional_resonance": {
        "style_weights": {"painterly": 0.8, "soft_light": 0.9, "warm_palette": 0.7, "intimate": 0.8},
        "style_prompt":  "soft painterly light, warm muted palette, intimate atmosphere, no text",
        "negative":      "harsh lighting, text, watermark, photorealistic faces, crowded",
    },
    "relational_warmth": {
        "style_weights": {"warm": 0.9, "inviting": 0.8, "gentle": 0.7},
        "style_prompt":  "warm inviting scene, gentle light, human connection implied, no text",
        "negative":      "cold, isolated, text, watermark",
    },
    "spiritual_inquiry": {
        "style_weights": {"ethereal": 0.8, "contemplative": 0.9, "symbolic": 0.6, "light_rays": 0.7},
        "style_prompt":  "contemplative ethereal atmosphere, soft light rays, symbolic depth, no text",
        "negative":      "literal religious symbols, crowded, text, watermark",
    },
    "intellectual_curiosity": {
        "style_weights": {"clean": 0.9, "diagrammatic": 0.7, "precise": 0.8},
        "style_prompt":  "clean precise illustration, clear composition, conceptual depth, no text",
        "negative":      "messy, text overlay, watermark, photorealistic",
    },
    "creative_engagement": {
        "style_weights": {"vivid": 0.8, "imaginative": 0.9, "surreal": 0.5},
        "style_prompt":  "vivid imaginative scene, creative composition, expressive colour, no text",
        "negative":      "mundane, text, watermark, corporate",
    },
    "practical_grounding": {
        "style_weights": {"clean": 0.8, "grounded": 0.9, "natural_light": 0.7},
        "style_prompt":  "grounded everyday scene, natural light, calm and clear, no text",
        "negative":      "abstract, surreal, text, watermark",
    },
    "humour_lightness": {
        "style_weights": {"bright": 0.8, "playful": 0.9, "light": 0.8},
        "style_prompt":  "bright playful scene, light-hearted composition, cheerful palette, no text",
        "negative":      "dark, heavy, text, watermark",
    },
}

DEFAULT_STYLE = {
    "style_weights": {"balanced": 0.7},
    "style_prompt":  "high quality illustration, clear composition, no text",
    "negative":      "text, watermark, low quality, blurry",
}

# ── Domain detection from anchor tags ────────────────────────────────────────
DOMAIN_ANCHOR_KEYWORDS = {
    "emotional_resonance":  ["emotion", "grief", "feeling", "pain", "fear", "loss", "sadness", "anger"],
    "relational_warmth":    ["relationship", "love", "family", "friend", "connection", "bond", "trust"],
    "spiritual_inquiry":    ["spirit", "soul", "divine", "sacred", "consciousness", "meaning", "purpose"],
    "intellectual_curiosity":["physics", "science", "theory", "philosophy", "history", "knowledge"],
    "creative_engagement":  ["art", "poetry", "music", "story", "creative", "imagination"],
    "practical_grounding":  ["habit", "routine", "plan", "decision", "goal", "practical"],
    "humour_lightness":     ["humor", "laugh", "joke", "play", "light", "fun"],
}

def detect_domain(anchor: dict) -> str:
    text = f"{anchor.get('canonical','')} {anchor.get('display_name','')} {anchor.get('domain_tags','')}".lower()
    scores = defaultdict(int)
    for domain, keywords in DOMAIN_ANCHOR_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                scores[domain] += 1
    return max(scores, key=scores.get) if scores else "intellectual_curiosity"

# ── Fetch top anchors from CMS ────────────────────────────────────────────────
def fetch_anchors(conn: sqlite3.Connection, n: int, domain_filter: str = None) -> list[dict]:
    rows = conn.execute("""
        SELECT a.id, a.canonical, COALESCE(a.display_name, a.canonical) as display_name, a.maturity, a.domain_tags,
               COUNT(r.rowid) as edge_count
        FROM anchors a
        LEFT JOIN relations_aggregated r ON (r.subject_id = a.id OR r.object_id = a.id)
        WHERE a.state != 'deprecated'
        GROUP BY a.id
        ORDER BY (a.maturity * COUNT(r.rowid)) DESC
        LIMIT ?
    """, (n * 3,)).fetchall()  # fetch 3x, filter down

    anchors = []
    for row in rows:
        a = {
            "id": row[0], "canonical": row[1], "display_name": row[2],
            "maturity": row[3], "domain_tags": row[4], "edge_count": row[5],
        }
        a["domain"] = detect_domain(a)
        if domain_filter and a["domain"] != domain_filter:
            continue
        anchors.append(a)
        if len(anchors) >= n:
            break

    return anchors

# ── Fetch relations for an anchor ─────────────────────────────────────────────
def fetch_relations(conn: sqlite3.Connection, anchor_id: str) -> list[tuple]:
    rows = conn.execute("""
        SELECT COALESCE(a2.display_name, a2.canonical), r.predicate, 'out' as dir
        FROM relations_aggregated r
        JOIN anchors a2 ON r.object_id = a2.id
        WHERE r.subject_id = ?
        LIMIT 8
    """, (anchor_id,)).fetchall()
    return [(r[0], r[1], r[2]) for r in rows if r[0]]

# ── Build scene graph from relations ─────────────────────────────────────────
def build_scene_graph(anchor: dict, relations: list) -> dict:
    objects = [anchor["display_name"]]
    rels = []
    attributes = []
    for obj, pred, _ in relations[:6]:
        objects.append(obj)
        if "is_a" in pred or "type" in pred:
            attributes.append(obj)
        else:
            rels.append(f"{anchor['display_name']} {pred.replace('_',' ')} {obj}")
    return {"objects": list(set(objects))[:8], "relations": rels[:5], "attributes": attributes[:4]}

# ── Generate prompt via LLM ───────────────────────────────────────────────────
def generate_prompt(anchor: dict, scene_graph: dict, style_cfg: dict) -> str | None:
    import requests
    objects = ", ".join(o for o in scene_graph.get("objects", [anchor["display_name"]]) if o)
    relations = "; ".join(scene_graph.get("relations", []))
    style = style_cfg.get("style_prompt", "high quality illustration")

    prompt_req = f"""Write a Stable Diffusion image prompt for the concept "{anchor['display_name']}".

Scene elements: {objects}
Relationships: {relations or 'none specified'}
Visual style: {style}

Rules:
- 1-3 sentences of descriptive visual detail
- No abstract language — describe what can actually be seen
- Evoke the concept through concrete imagery
- End with the style description
- Return ONLY the prompt text, nothing else"""

    try:
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL, "prompt": prompt_req, "stream": False,
            "options": {"temperature": 0.7, "num_predict": 120, "num_gpu": 8},
        }, timeout=60)
        r.raise_for_status()
        result = r.json().get("response", "").strip()
        # Clean up any meta-commentary the LLM added
        lines = [l for l in result.split("\n") if l.strip() and not l.startswith(("#", "-", "*", "Note", "Here"))]
        return " ".join(lines[:3])[:400] if lines else None
    except Exception as e:
        print(f"  [LLM error] {e}")
        return None

# ── CLIP embedding ─────────────────────────────────────────────────────────────
_clip_model = None
_clip_preprocess = None
_clip_device = None

def load_clip():
    global _clip_model, _clip_preprocess, _clip_device
    if _clip_model:
        return
    try:
        import clip, torch
        _clip_device = "cuda" if torch.cuda.is_available() else "cpu"
        _clip_model, _clip_preprocess = clip.load("ViT-B/32", device=_clip_device)
        print(f"  [CLIP loaded on {_clip_device}]")
    except ImportError:
        print("  [CLIP not installed — embeddings will be skipped]")

def embed_text(text: str) -> bytes | None:
    if not _clip_model:
        return None
    try:
        import clip, torch
        with torch.no_grad():
            tokens = clip.tokenize([text[:77]]).to(_clip_device)
            emb = _clip_model.encode_text(tokens).float().cpu().numpy()[0]
        return struct.pack(f"{len(emb)}f", *emb)
    except Exception:
        return None

def embed_image(pil_image) -> bytes | None:
    if not _clip_model:
        return None
    try:
        import torch
        with torch.no_grad():
            img_t = _clip_preprocess(pil_image).unsqueeze(0).to(_clip_device)
            emb = _clip_model.encode_image(img_t).float().cpu().numpy()[0]
        return struct.pack(f"{len(emb)}f", *emb)
    except Exception:
        return None

def cosine_sim(a_bytes: bytes, b_bytes: bytes) -> float:
    if not a_bytes or not b_bytes:
        return 0.0
    n = len(a_bytes) // 4
    a = struct.unpack(f"{n}f", a_bytes)
    b = struct.unpack(f"{n}f", b_bytes)
    dot = sum(x*y for x,y in zip(a,b))
    na  = math.sqrt(sum(x*x for x in a))
    nb  = math.sqrt(sum(x*x for x in b))
    return dot / (na * nb + 1e-8)

# ── Stable Diffusion pipeline ─────────────────────────────────────────────────
_sd_pipe = None

def load_sd():
    global _sd_pipe
    if _sd_pipe:
        return True
    try:
        import torch
        from diffusers import StableDiffusionPipeline
        print("Loading Stable Diffusion 1.5 (first run downloads ~4GB)...")
        _sd_pipe = StableDiffusionPipeline.from_pretrained(
            "runwayml/stable-diffusion-v1-5",
            torch_dtype=torch.float16,
            safety_checker=None,
        )
        _sd_pipe = _sd_pipe.to("cuda" if torch.cuda.is_available() else "cpu")
        try:
            _sd_pipe.enable_xformers_memory_efficient_attention()
            print("  [xformers enabled]")
        except Exception:
            pass
        print("  [SD 1.5 loaded]")
        return True
    except ImportError:
        print("  [diffusers not installed — run: pip install diffusers transformers accelerate]")
        return False
    except Exception as e:
        print(f"  [SD load error] {e}")
        return False

def generate_image(prompt: str, negative: str, steps: int, cfg: float, w: int, h: int):
    if not _sd_pipe:
        return None
    try:
        import torch
        with torch.autocast("cuda"):
            result = _sd_pipe(
                prompt=prompt,
                negative_prompt=negative,
                num_inference_steps=steps,
                guidance_scale=cfg,
                width=w,
                height=h,
            )
        return result.images[0]
    except Exception as e:
        print(f"  [generation error] {e}")
        return None

# ── DB writes ─────────────────────────────────────────────────────────────────
def existing_template_count(conn, anchor_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM visual_prompt_templates WHERE anchor_id=?", (anchor_id,)
    ).fetchone()
    return row[0] if row else 0

def write_template(conn, anchor: dict, prompt: str, negative: str,
                   scene_graph: dict, style_cfg: dict, cfg: float, steps: int,
                   w: int, h: int, clip_sim: float, template_id: str) -> str:
    conn.execute("""
        INSERT OR IGNORE INTO visual_prompt_templates
            (id, anchor_id, domain, subtype, prompt, negative,
             scene_graph, style_weights, cfg_scale, steps, width, height,
             render_mode, clip_similarity, quality_score,
             max_variants, source, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        template_id,
        anchor["id"],
        anchor["domain"],
        "general",
        prompt,
        negative,
        json.dumps(scene_graph),
        json.dumps(style_cfg.get("style_weights", {})),
        cfg,
        steps,
        w, h,
        "txt2img",
        clip_sim,
        clip_sim,   # quality_score = clip_sim until aesthetic score computed
        args.max_variants,
        "precompute_v1",
        time.time(),
    ))
    return template_id

def write_embedding(conn, template_id: str, anchor_id: str,
                    embed_type: str, embedding: bytes, embed_dim: int,
                    alignment: float = None):
    conn.execute("""
        INSERT OR IGNORE INTO visual_embeddings
            (id, template_id, anchor_id, embed_type, embedding, embed_dim,
             alignment_score, source, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        f"ve_{uuid.uuid4().hex[:12]}",
        template_id,
        anchor_id,
        embed_type,
        embedding,
        embed_dim,
        alignment,
        "precompute_v1",
        time.time(),
    ))

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    conn = sqlite3.connect(DB_PATH)
    if not args.dry_run:
        _set_gpu_power_limit()

    print(f"Visual Precompute — {args.anchors} anchors, {args.steps} steps, {args.width}×{args.height}")
    print(f"DB: {DB_PATH}")
    if args.dry_run:
        print("DRY RUN — no images generated, no DB writes\n")
    print("=" * 70)

    anchors = fetch_anchors(conn, args.anchors, args.domain)
    print(f"Fetched {len(anchors)} anchors\n")

    if not args.dry_run:
        load_clip()
        sd_ok = load_sd()

    total_templates = 0
    total_embeddings = 0
    skipped = 0

    for i, anchor in enumerate(anchors):
        display = anchor.get("display_name") or anchor["canonical"]
        print(f"\n[{i+1}/{len(anchors)}] {display} (domain: {anchor['domain']}, maturity: {anchor.get('maturity',0):.2f})")

        # Skip if already at max variants
        if existing_template_count(conn, anchor["id"]) >= args.max_variants:
            print(f"  [skip] already has {args.max_variants} templates")
            skipped += 1
            continue

        # Get relations and build scene graph
        relations  = fetch_relations(conn, anchor["id"])
        scene_graph = build_scene_graph(anchor, relations)
        style_cfg   = DOMAIN_STYLE.get(anchor["domain"], DEFAULT_STYLE)

        # Generate prompt via LLM
        print(f"  Generating prompt...")
        prompt = generate_prompt(anchor, scene_graph, style_cfg)
        if not prompt:
            print(f"  [skip] LLM returned no prompt")
            skipped += 1
            continue

        negative = style_cfg.get("negative", "text, watermark, low quality")
        print(f"  Prompt: {prompt[:100]}...")

        if args.dry_run:
            print(f"  Scene: {scene_graph}")
            continue

        # Generate image
        print(f"  Generating image ({args.steps} steps)...")
        image = generate_image(prompt, negative, args.steps, 7.0, args.width, args.height)
        _thermal_pause()

        # Extract embeddings
        template_id  = f"vpt_{uuid.uuid4().hex[:12]}"
        text_emb     = embed_text(prompt)
        image_emb    = embed_image(image) if image else None
        clip_sim     = cosine_sim(text_emb, image_emb) if (text_emb and image_emb) else 0.0
        embed_dim    = 512  # CLIP ViT-B/32

        print(f"  CLIP similarity: {clip_sim:.3f}")

        # Write to DB
        write_template(conn, anchor, prompt, negative, scene_graph, style_cfg,
                       7.0, args.steps, args.width, args.height, clip_sim, template_id)

        if text_emb:
            write_embedding(conn, template_id, anchor["id"], "text_clip", text_emb, embed_dim)
            total_embeddings += 1
        if image_emb:
            write_embedding(conn, template_id, anchor["id"], "image_clip", image_emb, embed_dim,
                            alignment=clip_sim)
            total_embeddings += 1

        conn.commit()
        total_templates += 1

        # image is discarded here — only embeddings + template stored
        del image

    print("\n" + "=" * 70)
    print(f"Complete. Templates written: {total_templates}, embeddings: {total_embeddings}, skipped: {skipped}")
    conn.close()

if __name__ == "__main__":
    main()
