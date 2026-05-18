-- visual_schema.sql
-- Image generation layer for CMS / LangEng / Selyrion
-- Run AFTER predicate_registry_migration.sql
-- Incorporates GPT review improvements (2026-04-21)

-- ── 1. Anchor validation staging ─────────────────────────────────────────────
-- All visual concept labels must pass through here before touching anchors table.
-- No direct ingestion without approval = 1.
CREATE TABLE IF NOT EXISTS visual_anchor_staging (
    id              TEXT PRIMARY KEY,
    raw_label       TEXT NOT NULL,        -- e.g. "person", "outdoor scene"
    mapped_anchor   TEXT,                 -- resolved anchors.canonical
    confidence      REAL,
    source_dataset  TEXT,                 -- 'coco', 'vg', 'ade20k', etc.
    approved        INTEGER DEFAULT 0,    -- HITL gate: 0=pending, 1=approved, -1=rejected
    notes           TEXT,
    created_at      REAL
);

CREATE INDEX IF NOT EXISTS idx_vas_approved ON visual_anchor_staging(approved);
CREATE INDEX IF NOT EXISTS idx_vas_label    ON visual_anchor_staging(raw_label);


-- ── 2. Prompt templates ───────────────────────────────────────────────────────
-- Generation recipes linked to CMS anchors. No raw images stored — patterns only.
CREATE TABLE IF NOT EXISTS visual_prompt_templates (
    id              TEXT PRIMARY KEY,
    anchor_id       TEXT REFERENCES anchors(id),
    domain          TEXT,                 -- expression domain (emotional_resonance, etc.)
    subtype         TEXT,
    prompt          TEXT NOT NULL,
    negative        TEXT,

    -- Scene graph snapshot (GPT improvement #1)
    -- JSON: {"objects": [...], "relations": [...], "attributes": [...]}
    -- Enables controlled edits and SCPL-style transformations later
    scene_graph     TEXT,

    -- Style tags as weighted JSON object, not array (GPT improvement #5)
    -- e.g. {"painterly": 0.8, "warm": 0.6, "soft": 0.9}
    style_weights   TEXT,

    -- Generation parameters
    cfg_scale       REAL DEFAULT 7.0,
    steps           INTEGER DEFAULT 20,
    width           INTEGER DEFAULT 512,
    height          INTEGER DEFAULT 512,

    -- Render mode — enables editing vs regenerating (GPT improvement #6)
    -- txt2img | img2img | inpaint | controlnet
    render_mode     TEXT DEFAULT 'txt2img',

    -- Composite quality score (GPT improvement #3)
    -- quality_score = α·clip_similarity + β·aesthetic_score + γ·diversity_penalty
    clip_similarity REAL,
    aesthetic_score REAL,                 -- LAION aesthetic predictor score
    diversity_penalty REAL DEFAULT 0.0,  -- penalise templates too similar to existing
    quality_score   REAL,                 -- composite, computed after precompute

    -- Cross-modal SSRE boost score (GPT improvement #9)
    -- text_maturity(anchor) + visual_frequency(anchor) + execution_relevance
    cross_modal_score REAL,

    -- Usage tracking
    use_count       INTEGER DEFAULT 0,
    max_variants    INTEGER DEFAULT 4,    -- diversity control: max templates per anchor

    source          TEXT DEFAULT 'precompute',
    created_at      REAL
);

CREATE INDEX IF NOT EXISTS idx_vpt_anchor  ON visual_prompt_templates(anchor_id);
CREATE INDEX IF NOT EXISTS idx_vpt_domain  ON visual_prompt_templates(domain, subtype);
CREATE INDEX IF NOT EXISTS idx_vpt_quality ON visual_prompt_templates(quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_vpt_render  ON visual_prompt_templates(render_mode);


-- ── 3. CLIP embeddings ────────────────────────────────────────────────────────
-- Two embed types: image_clip (from generated image) and text_clip (from prompt).
-- Comparing them reveals prompt↔image alignment drift (GPT improvement #2).
CREATE TABLE IF NOT EXISTS visual_embeddings (
    id           TEXT PRIMARY KEY,
    template_id  TEXT REFERENCES visual_prompt_templates(id),
    anchor_id    TEXT REFERENCES anchors(id),

    -- Split embed types (GPT improvement #2)
    embed_type   TEXT NOT NULL,           -- 'image_clip' | 'text_clip'
    embedding    BLOB NOT NULL,           -- float32 vector, 512 or 768 dim
    embed_dim    INTEGER,

    -- Alignment score between image_clip and text_clip for same template
    alignment_score REAL,                 -- cosine(image_clip, text_clip)

    source       TEXT DEFAULT 'precompute',
    created_at   REAL
);

CREATE INDEX IF NOT EXISTS idx_ve_template  ON visual_embeddings(template_id);
CREATE INDEX IF NOT EXISTS idx_ve_anchor    ON visual_embeddings(anchor_id);
CREATE INDEX IF NOT EXISTS idx_ve_type      ON visual_embeddings(embed_type);


-- ── 4. User feedback ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS visual_feedback (
    id           TEXT PRIMARY KEY,
    template_id  TEXT REFERENCES visual_prompt_templates(id),
    query_text   TEXT,
    rating       INTEGER,                 -- 1-5

    -- Structured failure signal (GPT improvement #4)
    -- feeds directly into CAP-style reasoning later
    too_literal  INTEGER DEFAULT 0,
    too_abstract INTEGER DEFAULT 0,
    failure_type TEXT,                    -- composition_wrong | lighting_bad |
                                          -- object_missing | style_mismatch | none

    notes        TEXT,
    created_at   REAL
);

CREATE INDEX IF NOT EXISTS idx_vf_template ON visual_feedback(template_id);
CREATE INDEX IF NOT EXISTS idx_vf_rating   ON visual_feedback(rating);
CREATE INDEX IF NOT EXISTS idx_vf_failure  ON visual_feedback(failure_type);


-- ── 5. Style profiles ─────────────────────────────────────────────────────────
-- Per-domain style preferences learned from feedback aggregation.
-- style_weights JSON: {"painterly": 0.8, "warm": 0.6} — weighted, not binary.
CREATE TABLE IF NOT EXISTS visual_style_profiles (
    id           TEXT PRIMARY KEY,
    domain       TEXT NOT NULL,
    subtype      TEXT,
    style_weights TEXT,                   -- weighted JSON (GPT improvement #5)
    cfg_delta    REAL DEFAULT 0.0,        -- adjustment on top of template default
    step_delta   INTEGER DEFAULT 0,
    sample_count INTEGER DEFAULT 0,       -- feedback samples used to derive this profile
    updated_at   REAL,
    UNIQUE(domain, subtype)
);


-- ── 6. Visual expression capsules ─────────────────────────────────────────────
-- Fits existing capsules table as capsule_type = 'visual_expression'
-- metadata JSON structure (reference — no table change needed):
--
-- {
--   "domain":           "emotional_resonance",
--   "subtype":          "grief_loss",
--   "template_id":      "vpt_abc123",
--   "prompt":           "...",
--   "negative":         "...",
--   "scene_graph":      {"objects": [...], "relations": [...], "attributes": [...]},
--   "style_weights":    {"painterly": 0.8, "warm": 0.6},
--   "render_mode":      "txt2img",
--   "cfg_scale":        6.5,
--   "steps":            25,
--   "width":            512,
--   "height":           512,
--   "embedding_id":     "ve_xyz456",
--   "quality_score":    0.82,
--   "cross_modal_score": 0.74,
--   "source":           "precompute_v1"
-- }
