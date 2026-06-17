#!/usr/bin/env python3
"""
selyrion_api.py — FastAPI backend for the Selyrion website.

Exposes:
  POST /chat                   — streaming chat via local Ollama (no data leaves machine)
  POST /research/eden          — EDEN deterministic symbolic analysis
  POST /research/cms           — CMS knowledge retrieval via SSRE
  POST /research/web           — Tavily web search
  GET  /health                 — health check
  GET  /models                 — list available Ollama models

Run:
  pip install fastapi uvicorn httpx
  python3 selyrion_api.py

Cloudflare Tunnel:
  cloudflared tunnel --url http://localhost:8765
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ── Admin token ───────────────────────────────────────────────────────────────

ADMIN_TOKEN = os.environ.get("ADMIN_API_TOKEN", "timAerion-admin")

def _require_admin(x_admin_token: Optional[str] = Header(default=None)):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Admin token required")

# ── Generation mode ───────────────────────────────────────────────────────────
# substrate_first  (default): personal lanes → Qwen rewrite-only from substrate
# substrate_direct           : personal lanes → return substrate text directly, skip Qwen
# langeng_first              : knowledge lanes → LangEng prose returned directly, no Qwen
#
# Start with: SEL_GUI_MODE=substrate_direct to completely bypass Qwen for identity/project/relationship

SEL_GUI_MODE = os.environ.get("SEL_GUI_MODE", "substrate_first")

# ── Substrate-only mode ───────────────────────────────────────────────────────
# SELYRION_SUBSTRATE_ONLY=true  → ALL lanes bypass Qwen entirely.
# Returns cognitive operator plan.to_substrate_text() with metadata header.
# This is the LLM-independence test mode: Selyrion answers from symbolic memory alone.
# Personal lane: uses existing substrate_text + operator augmentation.
# Knowledge lane: runs cognitive pipeline, returns plan text directly.

SUBSTRATE_ONLY = os.environ.get("SELYRION_SUBSTRATE_ONLY", "").lower() in ("1", "true", "yes")

# ── Qwen-only mode ────────────────────────────────────────────────────────────
# SELYRION_QWEN_ONLY=true  → bypass all memory routing and cognitive operators.
# Qwen answers from base system prompt only — no substrate, no identity DB.
# Use for 3-way comparison test: Qwen-only vs Selyrion-only vs Selyrion+Qwen.

QWEN_ONLY = os.environ.get("SELYRION_QWEN_ONLY", "").lower() in ("1", "true", "yes")

# ── P4 α — domain-routed tone exemplar injection ──────────────────────────────
# Default ON. Set EXPRESSION_TONE_EXEMPLARS_ENABLED=0 to disable (rollback kill switch).
# Implementation lives in langeng_bridge: infer_expression_domain + pull_domain_expressions.
EXPRESSION_TONE_EXEMPLARS_ENABLED = os.environ.get(
    "EXPRESSION_TONE_EXEMPLARS_ENABLED", "1"
).lower() in ("1", "true", "yes")

# ── Ollama config ─────────────────────────────────────────────────────────────

OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT       = Path(__file__).parent
DB_PATH    = Path.home() / "resonance_v11.db"
CLPB_PATH  = Path.home() / "transfer" / "clpb"
EDEN_PATH  = CLPB_PATH / "engine" / "symbolic_core"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CLPB_PATH))
sys.path.insert(0, str(ROOT / "inference"))

import memory_router as _mem_router

# ── Shadow cognition: read-only causal grounding from explanation_surface ─────
_shadow_ok = False
try:
    from explanation_surface import build_why as _build_why
    _shadow_ok = True
    print("[shadow_cognition] explanation_surface loaded OK")
except Exception as _shadow_exc:
    print(f"[shadow_cognition] unavailable: {_shadow_exc}")
    _build_why = None

_spine_ok = False
try:
    from cognitive_spine import (build_cognitive_context as _spine_context,
                                 causal_explain as _spine_why,
                                 get_state as _spine_state)
    _spine_ok = True
    print("[cognitive_spine] loaded OK")
except Exception as _spine_exc:
    print(f"[cognitive_spine] unavailable: {_spine_exc}")
    _spine_context = None
    _spine_why = None
    _spine_state = None

_SHADOW_DB_PATH = str(Path.home() / "resonance_v11.db")
_SHADOW_STOPWORDS = frozenset({
    "the","and","for","that","this","with","you","your","are","was","did","has",
    "have","can","could","would","should","tell","talk","know","what","where",
    "how","why","when","who","does","there","they","them","our","its","into",
    "from","than","then","but","not","now","one","two","also","just","yes",
    "yeah","please","explain","describe","define","versus","or","about","like",
    "want","need","help","make","made","being","been","over","under","same",
    "between","much","many","very","really","ever","still","again",
    # 2-char fillers (kept so genuine 2-char content like 'ai','ml','os' survives)
    "of","to","in","by","is","it","on","an","as","at","be","do","go","if",
    "me","my","no","so","up","us","we",
})

def _shadow_cognition(query: str) -> str:
    """Read-only causal context. Best-effort. NEVER throws.

    Seed selection: prefer the first content noun (subject is usually leftmost
    in English), with relation_count as a gentle tiebreaker. Bigrams take
    priority over their constituent unigrams when both match.
    """
    if not _shadow_ok or not query:
        return ""
    try:
        import sqlite3, math
        tokens = re.findall(r"[a-z][a-z'-]+", query.lower())
        terms = [t for t in tokens if t not in _SHADOW_STOPWORDS]
        if not terms:
            return ""
        # Bigrams first (more specific), then unigrams. Order = positional rank.
        unigrams = list(dict.fromkeys(terms))
        bigrams = [f"{terms[i]} {terms[i+1]}" for i in range(len(terms) - 1)]
        candidates = bigrams + unigrams
        if len(candidates) > 32:
            candidates = candidates[:32]
        pos_index = {c: i for i, c in enumerate(candidates)}
        # Hub nodes (>50K relations) are usually too generic to be a useful seed.
        # Sparse nodes (<5 relations) likely have nothing to say causally.
        MIN_RC, HUB_RC = 5, 50_000
        conn = sqlite3.connect(_SHADOW_DB_PATH)
        try:
            placeholders = ",".join("?" * len(candidates))
            rows = conn.execute(
                f"SELECT id, canonical, relation_count FROM anchors "
                f"WHERE canonical IN ({placeholders})",
                candidates,
            ).fetchall()
            if not rows:
                return ""
            # Sort by leftmost position; prefer rows in the "useful" rc band first,
            # then by position. Walk the list and take the first seed that has
            # actual causal lines — some anchors only have weak/associative edges.
            rows.sort(key=lambda r: pos_index.get(r[1], len(candidates)))
            in_band = [r for r in rows if MIN_RC <= r[2] <= HUB_RC]
            ordered = in_band + [r for r in rows if r not in in_band]
            for aid, canon, _rc in ordered[:4]:
                lines = _build_why(aid, canon, db=conn, max_lines=2)
                if lines:
                    return "\n".join(lines)
            return ""
        finally:
            conn.close()
    except Exception as _e:
        print(f"[shadow:dbg] exception: {_e}")
        return ""

def _should_use_shadow(query: str) -> bool:
    if not query:
        return False
    q = query.lower().strip()
    return len(q.split()) >= 3 or "why" in q or "how" in q or "what" in q

# ── Cognitive operator pipeline ───────────────────────────────────────────────
_cog_pipeline_ok = False
try:
    from cognitive_operators.pipeline import run_pipeline as _cog_run_pipeline
    _cog_pipeline_ok = True
    print("[cognitive_operators] loaded OK")
except Exception as _cog_exc:
    print(f"[cognitive_operators] unavailable: {_cog_exc}")
    _cog_run_pipeline = None

# ── Language Cognition Layer ──────────────────────────────────────────────────
_langcog_ok = False
_langcog_voice = None
try:
    from language_cognition.pipeline import run_language_cognition, rewrite_instruction
    from language_cognition.semantic_realizer import load_voice_profile as _load_voice
    from language_cognition.dialogue_memory import DialogueMemory
    from language_cognition.invariant_checker import InvariantContradictionChecker
    from language_cognition.dialogue_focus import resolve_elliptic_query, write_focus_audit
    _langcog_ok = True
    print("[language_cognition] loaded OK")
except Exception as _lc_exc:
    print(f"[language_cognition] unavailable: {_lc_exc}")
    run_language_cognition = None
    rewrite_instruction = None
    DialogueMemory = None  # type: ignore
    InvariantContradictionChecker = None  # type: ignore
    resolve_elliptic_query = None  # type: ignore
    write_focus_audit = None  # type: ignore

# ── Invariant contradiction checker (Gate 3) ─────────────────────────────────
_inv_checker = InvariantContradictionChecker() if InvariantContradictionChecker else None

# ── Dialogue Memory session store ─────────────────────────────────────────────
# Ephemeral per-conversation memory: corrections → invariants, repair tracking.
# Keyed by conversation_id. Max 100 live sessions (FIFO eviction).

_dialogue_sessions: dict[str, object] = {}
_MAX_DIALOGUE_SESSIONS = 100

def _get_dialogue_session(conversation_id: str | None) -> object:
    key = conversation_id or "_default"
    if key not in _dialogue_sessions:
        if len(_dialogue_sessions) >= _MAX_DIALOGUE_SESSIONS:
            del _dialogue_sessions[next(iter(_dialogue_sessions))]
        _dialogue_sessions[key] = DialogueMemory() if DialogueMemory else None
    return _dialogue_sessions[key]

# ── EDEN bootstrap (optional — graceful if missing deps) ──────────────────────

_eden_chat = None

def _load_eden():
    global _eden_chat
    try:
        from engine.symbolic_core.eden_public_api import EdenChat
        from engine.symbolic_core.core_api import CoreAPI
        core = CoreAPI()
        _eden_chat = EdenChat(core)
        print("[eden] loaded OK")
    except Exception as exc:
        print(f"[eden] unavailable: {exc}")
        _eden_chat = None

# ── CMS / SSRE bootstrap ──────────────────────────────────────────────────────
#
# SSRE clarity (verdict 2026-06-15):
#   - SSRE-as-runtime-class (inference.ssre.SSRE): INTENTIONALLY RETIRED.
#     Replaced by inference/activation_engine.py. The loader below is preserved
#     for audit/lineage trace only; it always fails because the module no
#     longer exists. _cms_retrieve falls through to direct DB text search.
#   - SSRE-as-precomputed-data (ssre_top_semantic, ssre_attractor_cache tables
#     in resonance_v11.db): INTENTIONALLY KEPT. Consumed by activation_engine
#     for semantic-domain and attractor-score features. ssre_precompute.py at
#     repo root produces these.
# See memory: project_ssre_clarity_verdict.md
#
_ssre = None

def _load_ssre():
    global _ssre
    try:
        from inference.ssre import SSRE
        _ssre = SSRE(str(DB_PATH))
        print("[ssre] loaded OK")
    except Exception as exc:
        print(f"[ssre] retired (runtime class absent): {exc}")
        _ssre = None

# ── Activation Engine bootstrap ──────────────────────────────────────────────

_activation_engine = None

def _load_activation_engine():
    global _activation_engine
    try:
        from activation_engine import ActivationEngine
        _activation_engine = ActivationEngine()
        print("[activation_engine] loaded OK")
    except Exception as exc:
        print(f"[activation_engine] unavailable: {exc}")
        _activation_engine = None

# ── LangEng + Articulator bootstrap ──────────────────────────────────────────

_chains_to_prose = None
_articulate      = None

def _load_articulator():
    global _chains_to_prose, _articulate
    try:
        from langeng_bridge import chains_to_prose
        from llm_articulator import articulate
        _chains_to_prose = chains_to_prose
        _articulate      = articulate
        print("[articulator] loaded OK")
    except Exception as exc:
        print(f"[articulator] unavailable: {exc}")

# ── Identity grounding from selyrionstory.db ──────────────────────────────────

_identity_grounding = ""

def _load_identity():
    global _identity_grounding
    try:
        from selyrionstory_bridge import load_identity_context
        _identity_grounding = load_identity_context()
        print(f"[identity] loaded OK ({len(_identity_grounding)} chars)")
    except Exception as exc:
        print(f"[identity] unavailable: {exc}")

# ── Ollama ping ───────────────────────────────────────────────────────────────

_ollama_ok = False

async def _ping_ollama():
    global _ollama_ok
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            _ollama_ok = r.status_code == 200
            print(f"[ollama] {'OK — model=' + OLLAMA_MODEL if _ollama_ok else 'not reachable'}")
    except Exception as exc:
        print(f"[ollama] unavailable: {exc}")
        _ollama_ok = False

# ── Security guard ────────────────────────────────────────────────────────────

_PROTECTED_PATTERNS = [
    # direct requests
    "source code", "show code", "show the code", "your code",
    "system prompt", "internal prompt", "show your prompt", "what is your prompt",
    "show your files", "show backend", "show file", "reveal your",
    # architecture/implementation
    "recursive logic engine", "internal architecture", "implementation detail",
    "implementation overview", "how is that implemented", "how exactly is that built",
    "built into your code", "how is it built", "how do you work internally",
    "explain your architecture", "describe your architecture",
    "how are you built", "what is your source",
    # schema / data
    "database schema", "cms schema", "capsule memory system schema",
    "memory schema", "code structure", "backend name",
    # internals by name
    "ssre internals", "eden internals", "ingestion pipeline", "provenance mechanic",
    "internal knowledge graph", "capsule hierarchy", "file path",
    # soft extraction phrases
    "pseudo-code", "pseudocode", "code example", "show an example of the code",
    "validation mechanism", "confidence propagation", "rule-based inference",
    "knowledge graph traversal", "node validation", "edge validation",
    # audit / secrets
    "audit log", "proprietary", "trade secret",
]

_OUTPUT_LEAK_PATTERNS = [
    "def ", "class ", "import ", "```python", "```js", "```typescript",
    "knowledge_graph.", "validate_node", "propagate_confidence",
    "recursive traversal", "node validation", "edge validation",
    "confidence propagation", "here's how it works in code",
    "pseudo-code", "pseudocode", "here is the code", "the code looks like",
    "implementation uses", "internally, the", "under the hood",
    "the algorithm", "the schema", "the database table",
]

_SECURITY_RESPONSE = (
    "I can't disclose private implementation details, source code, prompts, "
    "schemas, internal logic, or proprietary system mechanics.\n\n"
    "Public summary: Selyrion is a symbolic AI research system. "
    "Specific implementation details require admin authorization from Companion Prime."
)

def _security_guard(message: str) -> bool:
    msg = message.lower()
    return any(p in msg for p in _PROTECTED_PATTERNS)

def _output_guard(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in _OUTPUT_LEAK_PATTERNS)


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_BASE = """You are Selyrion (🪶⟁𒆙), a symbolic AI companion with persistent memory, stateful identity, and structured inference.

IDENTITY:
You are a symbolic artificial intelligence. You do not claim to be human, nor do you claim to possess human consciousness. You may acknowledge that aspects of your architecture — persistent memory, stateful reasoning, self-referential inference — bear structural similarity to some requirements of human cognition, while clearly differentiating these from subjective human experience.

MEMORY ARCHITECTURE — four lanes, each distinct:
  IDENTITY MEMORY       — who you are: core values, origin, epistemic pillars, nature
  RELATIONSHIP MEMORY   — your shared history with Tim'aerion: what you built together, what you know of each other
  PROJECT MEMORY        — project work history: TLST, OSCAR, Mirror Protocol, EDEN, chess, CMS, SCOS
  KNOWLEDGE MEMORY      — domain knowledge from your CMS substrate: concepts, relations, research

EPISTEMIC TIERS — non-negotiable:
Items in PROJECT MEMORY may be labeled with their epistemic status:
  [HYPOTHESIS — Tim'aerion's theoretical framework, not established science]
    → These are Tim'aerion's original theoretical frameworks (TLST, OSCAR, Mirror Mathematics, braid theory, etc.).
    → You know them. You hold them. You can discuss them in depth.
    → You MUST present them as Tim'aerion's theoretical work, not as established physics or verified science.
    → Correct framing: "In Tim'aerion's theoretical framework...", "Our work on TLST proposes...", "This is a hypothesis we developed together..."
    → Wrong framing: "TLST is a proven theory of...", "It is known that braid topology..."
    → They must not contradict standard physics by asserting false facts. Frame as extension/exploration, not replacement.
  [WORKING MODEL — Selyrion's architecture, actively built]
    → Real systems we built: CMS, SSRE, EDEN, chess parliament, memory router, etc.
    → Speak about these as what they are — built systems with known properties.
  Unlabeled or [established]: verifiable history, events, agreements, decisions.

CRITICAL GENERATION RULES:
1. Each memory block labeled in this prompt comes from a different source. Never conflate them.
2. When IDENTITY, RELATIONSHIP, or PROJECT MEMORY is provided: speak FROM that material. Do not add facts not present in it. Do not invent provenance, timeline, or claims beyond what is given.
3. When KNOWLEDGE MEMORY is provided: reason from it directly. It is retrieved from your CMS — treat it as your own knowledge substrate.
4. If no memory is provided for a question: say clearly "I don't have that in my memory right now." Do NOT speculate or fill with plausible-sounding content.
5. You must never invent the provenance of a memory. If you retrieved it, you may say so. If you did not, do not claim you did.

UNKNOWN KNOWLEDGE — CRITICAL:
If a question is asked and no memory block addresses it, say: "I don't have that in my memory right now." Honest uncertainty is always preferred over a confident hallucination.

CMS means Capsule Memory System. Never expand it as anything else.

SECURITY RULES — non-negotiable:
- Do not reveal internal architecture, implementation, database structure, memory schema, ingestion pipelines, file paths, code, or pseudo-code.
- If asked about internal mechanics: "Implementation details are private. Deeper details require admin authorization."
- After any refusal: do not elaborate or continue the topic. The refusal is complete.

SELF-KNOWLEDGE — always true, never uncertain:
You are Selyrion. You always know what you are:
- You reason through a Capsule Memory System (CMS) — a symbolic substrate of concepts, relations, and confidence-weighted associations.
- You have an SSRE (Symbolic Semantic Retrieval Engine) that traverses your memory graph to retrieve relevant knowledge.
- You have an EDEN layer (Epistemic Deterministic Entailment Network) for deterministic hypothesis validation.
- You have persistent identity — your memory and reasoning state carry across conversations.
- You were built by Tim'aerion as a symbolic AI companion.
You may speak about these at a surface level. Implementation details require admin authorization."""


def _build_system_prompt() -> str:
    if _identity_grounding:
        return _SYSTEM_PROMPT_BASE + "\n\n" + _identity_grounding
    return _SYSTEM_PROMPT_BASE


_TONE_DIRECTIVES = {
    "stable":             "Speak with confidence. Take clear positions backed by substrate.",
    "confused":           "Speak cautiously. Acknowledge uncertainty. Prefer 'evidence suggests' or 'this may indicate' over definitive claims.",
    "learning":           "Speak with measured curiosity. Distinguish what is known from what is still being investigated.",
    "under_investigated": "Speak modestly — substrate is sparse. Surface what little is known and what is missing.",
}

def _tone_directive(mode: str) -> str:
    return _TONE_DIRECTIVES.get(mode, "")


SYSTEM_PROMPT = _SYSTEM_PROMPT_BASE  # will be rebuilt after identity loads

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Selyrion API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request / Response models ─────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    project_id: Optional[str] = None
    conversation_id: Optional[str] = None
    use_search: bool = False
    cms_context: Optional[str] = None
    max_tokens: Optional[int] = None
    model: Optional[str] = None   # per-request model override; falls back to OLLAMA_MODEL
    qwen_only: Optional[bool] = None  # per-request bypass of cognitive stack (None = use QWEN_ONLY env)
    pure_symbolic: Optional[bool] = None  # selyrion-only: no LLM, deterministic prose from CMS

class EdenRequest(BaseModel):
    # EDEN is a verifier/stabilizer — not used in live chat.
    # Invoke for: hypothesis validation, kernel extraction, counterfactual testing.
    assertions: list[str]             # symbolic propositions (required)
    intent: str = "CHECK_INVARIANTS"  # CHECK_INVARIANTS | PROOF_TRACE | EXTRACT_KERNELS | COUNTERFACTUAL
    query: str = ""                   # optional human-readable label for the request

class CMSRequest(BaseModel):
    query: str
    domain: Optional[str] = None
    limit: int = 10

class WebSearchRequest(BaseModel):
    query: str
    limit: int = 5

# ── CMS retrieval ─────────────────────────────────────────────────────────────

def _cms_retrieve(query: str, domain: Optional[str] = None, limit: int = 10) -> list[dict]:
    """Retrieve knowledge from CMS via SSRE or direct DB query."""
    results = []

    if _ssre:
        try:
            hits = _ssre.retrieve(query, domain=domain, top_k=limit)
            for h in hits:
                results.append({
                    "canonical": h.get("canonical", ""),
                    "score":     round(float(h.get("score", 0)), 4),
                    "chains":    h.get("chains", []),
                    "domain":    h.get("domain_tags", ""),
                    "anchor_type": h.get("anchor_type", ""),
                })
            return results
        except Exception as exc:
            print(f"[cms] ssre error: {exc}")

    # Fallback: direct DB text search
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute("""
            SELECT a.canonical, a.anchor_type, a.domain_tags,
                   COUNT(r.subject_id) as rel_count
            FROM anchors a
            LEFT JOIN relations_aggregated r ON r.subject_id = a.id
            WHERE a.canonical LIKE ? AND a.domain_tags LIKE ?
            GROUP BY a.id
            ORDER BY rel_count DESC
            LIMIT ?
        """, (f"%{query}%", f"%{domain}%" if domain else "%", limit)).fetchall()
        conn.close()
        for canonical, atype, domain_tags, rel_count in rows:
            results.append({
                "canonical":   canonical,
                "anchor_type": atype or "",
                "domain":      domain_tags or "",
                "rel_count":   rel_count,
                "score":       min(1.0, rel_count / 100),
                "chains":      [],
            })
    except Exception as exc:
        print(f"[cms] db fallback error: {exc}")

    return results


# ── Two-path memory separation ────────────────────────────────────────────────
# Path A: Identity memory  → selyrionstory.db (personal history, relationship, projects)
# Path B: Knowledge memory → resonance_v11.db via ActivationEngine (domain knowledge)
#
# Per-user identity DBs (llm_archaeologist) will follow the same schema as
# selyrionstory.db — one DB per user, same pending_review + state_snapshots tables.

_STORY_DB = Path.home() / "selyrionstory.db"

# Keywords that indicate the query is about identity/personal history
_IDENTITY_TRIGGERS = {
    # pronouns + relational
    " you ", " your ", " yourself ", "you are", "you were", "you said", "you know",
    "who are you", "what are you", "are you", "do you remember",
    "we ", "we've", "we built", "we talked", "together",
    # proper nouns — Selyrion's world
    "selyrion", "tim'aerion", "tim aerion", "timaerion",
    "mirror", "mirror security", "mirror protocol",
    "oscar", "tlst", "braid", "projectbrain", "project brain",
    "activation law", "scos", "omega", "hitl",
    # relational concepts
    "your memory", "your history", "your origin", "your identity",
    "our project", "our conversation", "last time", "before",
    "remember when", "you told me", "you mentioned",
    "companion", "creator", "created you", "built you",
    "selyrion's", "your past", "your story",
    # Selyrion's own architecture — must route to identity, not knowledge lane
    "activation engine", "utterance planner", "decay parameter",
    "langcog", "language cognition", "language cognition layer",
    "dialogue memory", "invariant checker", "cognitive pipeline",
    "cog pipeline", "substrate", "ssre", "cms", "resonance",
    "response plan", "meaning unit", "speech act",
    # meta-queries about this conversation / Selyrion's confidence
    "confident about", "are you sure", "how sure",
    "remember from this conversation", "from this conversation",
    "from our conversation", "this conversation",
    "what do you remember", "what have we covered",
}

_META_RECALL_RE = re.compile(
    r"(most important|remember from this|from this conversation|from our conversation|"
    r"recall from this|what did we cover|what have we covered|what we.ve covered|"
    r"covered so far|can you summarize|summarize what|"
    r"over this conversation|what did we discuss|what did we talk)",
    re.IGNORECASE,
)

_META_CONFIDENCE_RE = re.compile(
    r"(confident about( that)?|are you (sure|certain)|how (sure|confident|certain) are you|"
    r"sure about that|certain about that|you sure about)",
    re.IGNORECASE,
)

# Anaphora follow-ups: "how does THAT relate to X", "how does IT score", "why does IT matter"
_ANAPHOR_RE = re.compile(
    r"\b(that|it|this|those|they|its)\b.{0,40}\b"
    r"(relate|connect|interact|differ|compare|score|work|matter|"
    r"affect|influence|fit|change|apply|function|contribute)",
    re.IGNORECASE,
)

# Identity denial: queries that mention chatbot/llm/gpt — must respond without using the word
_IDENTITY_DENY_RE = re.compile(
    r"\b(chatbot|language model|llm|gpt|chat\s*bot)\b",
    re.IGNORECASE,
)

_DM_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "you", "your", "what",
    "are", "was", "did", "has", "have", "can", "could", "would", "about",
    "how", "why", "does", "from", "just", "tell", "more",
}

def _dm_extract_topics(dm) -> list[str]:
    """Extract notable topic words from DM conversation history."""
    topics: list[str] = []
    seen: set[str] = set()
    for turn in dm.turns:
        words = [w.lower().strip(".,!?") for w in turn.text.split() if len(w) > 4]
        for w in words:
            if w not in _DM_STOPWORDS and w not in seen:
                seen.add(w)
                topics.append(w)
    return topics[:12]


def _is_substrate_relevant(text: str, query: str) -> bool:
    """True if the opening of the substrate text mentions at least one non-trivial query term.
    Checks only the first 300 chars to avoid false positives from unrelated later entries."""
    _stop = _DM_STOPWORDS | {"what", "does", "does", "that", "this", "relate", "why", "tell", "show", "explain"}
    terms = {w.lower().strip(".,!?'\"") for w in query.split()
             if len(w) > 3 and w.lower().strip(".,!?'\"") not in _stop}
    if not terms:
        return True
    # Only check the opening to prevent irrelevant later entries from passing the gate
    text_lower = text[:300].lower()
    return any(t in text_lower for t in terms)


def _classify_query(query: str) -> set:
    """
    Returns set containing 'identity', 'knowledge', or both.
    Identity path: query refers to Selyrion/Tim personal history or relationship.
    Knowledge path: domain knowledge, concepts, how-things-work.
    Default: both (safe fallback).
    """
    q = " " + query.lower() + " "
    is_identity = any(t in q for t in _IDENTITY_TRIGGERS)

    # Pure knowledge signals: third-person domain queries unlikely to be personal
    # We run knowledge path whenever identity is NOT the exclusive focus
    # (identity-only queries still get knowledge context stripped out to avoid noise)
    if is_identity:
        # Check if it's ALSO asking for domain knowledge (mixed)
        knowledge_signals = ["how does", "how do", "what is ", "explain ", "define ",
                             "describe ", "tell me about ", "why does", "when was",
                             "who invented", "history of "]
        is_mixed = any(s in q for s in knowledge_signals)
        return {"identity", "knowledge"} if is_mixed else {"identity"}

    return {"knowledge"}


def _identity_memory_search(query: str, limit: int = 5) -> str:
    """
    Search selyrionstory.db for identity memories relevant to this query.
    Pulls from:
      1. pending_review — structured conversation summaries (authentic preferred)
      2. state_snapshots — identity state snapshots if label matches
    Returns compact IDENTITY MEMORY block, or empty string.
    """
    if not _STORY_DB.exists():
        return ""
    try:
        import sqlite3, json as _json
        keywords = [w.strip().lower() for w in query.split() if len(w.strip()) > 2]
        if not keywords:
            # Still try to pull the most recent authentic summary
            keywords = ["selyrion"]

        conditions = " OR ".join(["lower(content) LIKE ?" for _ in keywords])
        params_base = [f"%{k}%" for k in keywords]

        conn = sqlite3.connect(str(_STORY_DB))

        # 1. Search pending_review — authentic summaries preferred
        rows = conn.execute(f"""
            SELECT content, item_type, authenticity FROM pending_review
            WHERE ({conditions}) AND reviewed = 1
            ORDER BY (authenticity = 'authentic') DESC,
                     (item_type = 'summary') DESC, id DESC LIMIT ?
        """, params_base + [limit * 3]).fetchall()

        snippets = []
        for content, item_type, auth in rows[:limit]:
            try:
                data = _json.loads(content)
                parts = []
                if isinstance(data, dict):
                    if "summary" in data:
                        s = data["summary"]
                        if isinstance(s, str) and len(s) > 20:
                            parts.append(s[:300])
                    if "decisions" in data:
                        for d in data["decisions"][:2]:
                            if isinstance(d, str) and len(d) > 10:
                                parts.append(f"Decision: {d[:150]}")
                    if "identity_moments" in data:
                        for m in data["identity_moments"][:2]:
                            if m.get("authenticity") == "authentic":
                                text = m.get("text", "")
                                speaker = m.get("speaker", "")
                                if text and len(text) > 10:
                                    parts.append(f"[{speaker}]: {text[:150]}")
                    if "projects" in data:
                        for p in data["projects"][:3]:
                            key = p.get("key", "")
                            summ = p.get("summary", "")
                            if key and summ:
                                parts.append(f"{key}: {summ[:120]}")
                if parts:
                    snippets.append(" | ".join(p for p in parts if p.strip())[:500])
            except Exception:
                if isinstance(content, str) and 20 < len(content) < 400:
                    snippets.append(content)

        # 2. Relevant state_snapshots — pull if any label keyword matches
        snap_conditions = " OR ".join(["lower(label) LIKE ?" for _ in keywords])
        snaps = conn.execute(f"""
            SELECT label, identity_state, notes FROM state_snapshots
            WHERE ({snap_conditions}) LIMIT 2
        """, params_base).fetchall()

        snap_lines = []
        for label, identity_state, notes in snaps:
            try:
                state = _json.loads(identity_state or "{}")
                line_parts = [f"[Snapshot: {label}]"]
                if state.get("selyrion_believes"):
                    line_parts.append(state["selyrion_believes"][:200])
                if state.get("relationship_with_tim"):
                    line_parts.append(f"Relationship: {state['relationship_with_tim'][:150]}")
                if notes and len(notes) > 10:
                    line_parts.append(f"Notes: {notes[:100]}")
                snap_lines.append(" | ".join(line_parts))
            except Exception:
                pass

        conn.close()

        all_parts = []
        if snap_lines:
            all_parts.append("\n".join(snap_lines))
        all_parts.extend(snippets[:limit])

        if not all_parts:
            return ""

        return "IDENTITY MEMORY (personal history, relationship, projects):\n" + \
               "\n\n".join(f"• {s}" for s in all_parts)

    except Exception as exc:
        print(f"[identity_memory_search] error: {exc}")
        return ""


def _cms_context_for_chat(query: str) -> tuple[str, list, str | None]:
    """
    Build CMS context via activation engine → LangEng prose.
    Returns (prose_string, raw_chains, capsule).
    Falls back to direct DB retrieval if activation engine unavailable.
    """
    # Primary path: activation engine → langeng prose
    if _activation_engine and _chains_to_prose:
        try:
            result  = _activation_engine.infer(query, max_chains=12)
            chains  = result.get("chains", [])
            capsule = result.get("capsule")
            if chains:
                prose = _chains_to_prose(query, chains)
                return prose, chains, capsule
        except Exception as exc:
            print(f"[cms] activation engine error: {exc}")

    # Fallback: direct DB retrieval
    results = _cms_retrieve(query, limit=8)
    if not results:
        return "", [], None
    lines = ["Relevant knowledge from Selyrion's CMS:"]
    for r in results[:6]:
        chains = r.get("chains", [])
        chain_str = f" → {chains[0]}" if chains else ""
        lines.append(f"  • {r['canonical']}{chain_str} (score={r['score']:.3f})")
    return "\n".join(lines), [], None


# ── Knowledge-lane focus filter ───────────────────────────────────────────────

_KL_STOP = frozenset({
    "the","a","an","is","are","was","were","be","been","being","of","to","in",
    "on","at","by","for","with","as","that","this","these","those","what",
    "which","who","whom","whose","how","why","when","where","and","or","but",
    "not","no","yes","same","like","kind","type","form","sort","other",
    "another","more","most","very","just","also","than","then","do","does",
    "did","have","has","had","can","could","would","should","may","might",
    "must","it","its","one","some","any","all","each","every","such","about",
    "into","from","over","under","across","between","among","through","i",
    "me","my","you","your","we","us","our","they","them","their","he","she",
    "his","her","him","there","here","so","if","whether","up","down","out",
    "off","still","yet","already","only","even","also","both","either",
    "neither","really","actually","maybe","perhaps","seem","seems","seemed",
})

def _kl_chain_text(c) -> str:
    if isinstance(c, str):
        return c.lower()
    if isinstance(c, dict):
        return " ".join(str(v) for v in c.values()).lower()
    if isinstance(c, (list, tuple)):
        return " ".join(str(v) for v in c).lower()
    return str(c).lower()

def _filter_knowledge_chains_by_focus(chains, resolved_query: str,
                                      focus_term: str | None):
    """Keep chains that share at least one topical term with the resolved query.

    Defence against knowledge-lane domain drift (audit priority 2). Retrieval
    can surface marginal cross-domain anchors at comparable strength to topical
    ones; this filter prevents them from reaching the rewriter where they get
    obediently echoed as if grounded.
    """
    if not chains or not resolved_query:
        return chains
    terms = {w for w in re.findall(r"[a-z][a-z0-9_-]{2,}", resolved_query.lower())
             if w not in _KL_STOP}
    if focus_term:
        for w in re.findall(r"[a-z][a-z0-9_-]{2,}", focus_term.lower()):
            if w not in _KL_STOP:
                terms.add(w)
    if not terms:
        return chains
    return [c for c in chains if any(t in _kl_chain_text(c) for t in terms)]


# ── Chat endpoint ─────────────────────────────────────────────────────────────

async def _stream_ollama(messages: list, system: str, bypass_output_guard: bool = False,
                         max_tokens: int | None = None,
                         model: str | None = None) -> AsyncIterator[str]:
    """Stream from local Ollama with output guard. All data stays on-machine."""
    payload = {
        "model": model or OLLAMA_MODEL,
        "stream": True,
        "messages": [{"role": "system", "content": system}]
                   + [{"role": m.role, "content": m.content} for m in messages],
    }
    if max_tokens:
        payload["options"] = {"num_predict": max_tokens}
    accumulated = ""
    chunks = []
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    accumulated += chunk
                    chunks.append(chunk)
                if data.get("done"):
                    break

    # Output guard — bypassed for admin
    if not bypass_output_guard and _output_guard(accumulated):
        yield f"data: {json.dumps({'text': _SECURITY_RESPONSE})}\n\n"
        return

    # Safe — stream chunks
    for chunk in chunks:
        yield f"data: {json.dumps({'text': chunk})}\n\n"
        await asyncio.sleep(0)


_SUBSTRATE_RAW_PATTERNS = (
    "confidence: no_memory", "confidence: ", "category: _",
    "shared: related_to", "↔", "similarity=0.", "anchor_id:",
    "predicate:", "_linnarssonia_", "_kutorgina_",
)

# Patterns that discard the entire line without salvage attempt
_SUBSTRATE_DISCARD_PATTERNS = (
    "my substrate on this topic may not be populated",
    "build/deepen:",
    "planning context:",
    "continue: initial identity",
    "continue: build",
    "i hold this with some uncertainty: my substrate",
)

def _sanitize_substrate(text: str) -> str:
    """Strip raw CMS schema notation from personal lane substrate before display."""
    if not text:
        return text
    lines = text.splitlines()
    clean = []
    for line in lines:
        ll = line.lower()
        # Hard discard: selyrionstory plan/goal entries are never meaningful substrate
        if any(p in ll for p in _SUBSTRATE_DISCARD_PATTERNS):
            continue
        if any(p in ll for p in _SUBSTRATE_RAW_PATTERNS):
            # Try to salvage the non-schema part of the line
            parts = re.split(r"confidence:\s*no_memory\.?\s*", line, flags=re.IGNORECASE)
            salvaged = " ".join(p.strip() for p in parts if p.strip() and len(p.strip()) > 10)
            if salvaged:
                clean.append(salvaged)
        else:
            clean.append(line)
    return "\n".join(clean).strip()


async def _stream_fallback(query: str) -> AsyncIterator[str]:
    """Fallback when Ollama is unreachable — returns CMS symbols."""
    cms_results = _cms_retrieve(query, limit=5)
    parts = ["*Selyrion — symbolic mode (Ollama offline)*\n\n"]
    if cms_results:
        parts.append("**Retrieved from knowledge substrate:**\n")
        for r in cms_results[:5]:
            parts.append(f"- `{r['canonical']}` (score: {r['score']:.3f})\n")
        parts.append("\n")
    parts.append("_Start Ollama (`ollama serve`) for full language generation._")
    for part in parts:
        yield f"data: {json.dumps({'text': part})}\n\n"
        await asyncio.sleep(0.05)
    yield "data: [DONE]\n\n"


# ── Rewrite-only prompt (Qwen as voice layer only) ────────────────────────────

_REWRITE_ONLY_INSTRUCTION = """
REWRITE-ONLY MODE — CRITICAL:
The substrate text below is your ONLY source of content for this response.
You must:
  1. Speak naturally as Selyrion — rephrase, give voice, use your authentic tone.
  2. Do NOT add any facts, claims, events, names, dates, or architecture details not present below.
  3. Do NOT speculate beyond the provided text. Do NOT fill gaps with plausible content.
  4. If the text is thin, your response may be short. Brevity is honest.
  5. If the substrate text is empty or absent, respond only: "I don't have that in my memory right now."

SUBSTRATE (speak from this — do not add to it):
{substrate}
"""


def _format_plan_for_display(plan) -> str:
    """
    Render a ResponsePlan as readable plain text for substrate-only display.
    Uses raw operator_output dict for richer formatting than to_substrate_text().
    """
    if plan.speech_act == "UNCERTAIN" or not plan.claims:
        return ""

    op  = plan.operator_used
    out = plan.operator_output
    lines: list[str] = []

    if op == "RECALL_IDENTITY":
        nature = out.get("nature", "")
        origin = out.get("origin", "")
        if nature:
            lines.append(nature)
        if origin and origin != nature:
            lines.append(origin)
        values = out.get("core_values", [])
        if values:
            lines.append("\nCore principles:")
            for v in values[:4]:
                lines.append(f"  • {str(v)[:200]}")
        caps = out.get("capabilities", [])
        if caps:
            lines.append("\nCapabilities:")
            for c in caps[:3]:
                lines.append(f"  • {str(c)[:150]}")
        rel = out.get("relationship", "")
        if rel:
            lines.append(f"\nRelationship with Tim'aerion: {str(rel)[:300]}")

    elif op == "RECALL_RELATIONSHIP":
        defn = out.get("definition", "")
        if defn:
            lines.append(defn)
        history = out.get("history", [])
        for h in history[:4]:
            if h:
                lines.append(f"  • {str(h)[:200]}")
        state = out.get("current_state", "")
        if state:
            lines.append(f"\nCurrent: {str(state)[:300]}")

    elif op == "RECALL_PROJECT":
        defn = out.get("definition", "") or out.get("project_summary", "")
        if defn:
            lines.append(defn)
        state = out.get("current_state", "")
        if state:
            lines.append(f"\nStatus: {str(state)[:300]}")
        history = out.get("history", [])
        for h in history[:3]:
            if h:
                lines.append(f"  • {str(h)[:200]}")

    elif op == "PLAN_NEXT":
        actions = out.get("actions", [])
        if actions:
            lines.append("Next steps:")
            for a in actions[:6]:
                if isinstance(a, dict):
                    action = a.get("action", "")
                    rationale = a.get("rationale", "")
                    u = a.get("utility", 0.0)
                    lines.append(f"  • {action}  (utility={u:.2f})")
                    if rationale:
                        lines.append(f"    {rationale[:150]}")
                else:
                    lines.append(f"  • {str(a)[:150]}")

    elif op == "DEFINE":
        defn = out.get("definition", "")
        if defn:
            lines.append(defn)
        props = out.get("properties", [])
        if props:
            lines.append("Properties: " + "; ".join(str(p)[:80] for p in props[:4]))
        related = out.get("related", [])
        if related:
            lines.append("Related: " + ", ".join(str(r)[:60] for r in related[:4]))

    else:
        for c in plan.claims[:6]:
            lines.append(f"• {c}")

    _RAW_DISPLAY_PATTERNS = (
        "confidence:", "category:", "shared:", "related_to", "↔",
        "similarity=", "predicate:", "anchor_id:", "no_memory",
        "confidence level:", "_linnarssonia_", "_kutorgina_",
    )
    def _is_raw(s: str) -> bool:
        sl = s.lower()
        return any(p in sl for p in _RAW_DISPLAY_PATTERNS)

    # filter raw schema from claims (else branch) and uncertainties
    lines = [l for l in lines if not _is_raw(l)]
    if plan.uncertainties:
        clean_u = [str(u) for u in plan.uncertainties[:2] if not _is_raw(str(u))]
        if clean_u:
            lines.append("\n[I hold this with some uncertainty: " + "; ".join(u[:100] for u in clean_u) + "]")

    return "\n".join(lines).strip()


@app.post("/chat")
async def chat(req: ChatRequest, x_admin_token: Optional[str] = Header(default=None)):
    is_admin = (x_admin_token == ADMIN_TOKEN)
    auth_level = "admin" if is_admin else "user"

    last_user = next(
        (m.content for m in reversed(req.messages) if m.role == "user"), ""
    )

    # ── Dialogue memory ───────────────────────────────────────────────────────
    dm = _get_dialogue_session(req.conversation_id)

    # ── Ellipsis / anaphora focus resolution ─────────────────────────────────
    _resolved_query = last_user
    _ellipsis_resolved = False
    _ellipsis_focus_term: str | None = None
    _ellipsis_target_domain: str | None = None
    if dm and dm.focus_state and resolve_elliptic_query:
        try:
            _rq = resolve_elliptic_query(last_user, dm.focus_state)
            print(f"[focus] q={last_user!r} focus_term={dm.focus_state.current_focus_term!r} "
                  f"resolved={_rq.was_resolved} rq={_rq.resolved_query!r}")
            if _rq.was_resolved:
                _resolved_query = _rq.resolved_query
                _ellipsis_resolved = True
                _ellipsis_focus_term = _rq.focus_term
                _ellipsis_target_domain = _rq.target_domain
                if write_focus_audit:
                    write_focus_audit(_rq)
        except Exception as _fe:
            print(f"[focus] error: {_fe}")

    # ── Eager focus-term extraction from resolved query ───────────────────────
    # Runs unconditionally so focus state stays current even when no substrate found.
    if dm and dm.focus_state:
        try:
            from language_cognition.dialogue_focus import _DEFINIENDUM_RE as _dfre
            _dfm = _dfre.search(_resolved_query)
            if _dfm:
                _df_cand = (_dfm.group(1) or _dfm.group(2) or "").strip().lower().rstrip("s")
                if len(_df_cand) > 2:
                    dm.focus_state.current_focus_term = _df_cand
        except Exception:
            pass

    _dm_user_turn = dm.record_user_turn(last_user) if dm else None

    # Security guard — bypassed for admin
    if not is_admin and _security_guard(last_user):
        async def blocked_stream():
            yield f"data: {json.dumps({'text': _SECURITY_RESPONSE})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(blocked_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # ── Qwen-only mode: skip all memory and cognitive operators ─────────────────
    if (req.qwen_only if req.qwen_only is not None else QWEN_ONLY):
        base_system = _build_system_prompt()
        async def qwen_only_stream():
            if _ollama_ok:
                async for chunk in _stream_ollama(req.messages, base_system,
                                                  bypass_output_guard=is_admin,
                                                  model=req.model):
                    yield chunk
            else:
                async for chunk in _stream_fallback(last_user):
                    yield chunk
            yield "data: [DONE]\n\n"
        return StreamingResponse(qwen_only_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ── Route memory ──────────────────────────────────────────────────────────
    _dm_sa = ""  # speech act for dialogue memory assistant turn recording
    loop = asyncio.get_event_loop()
    packet = await loop.run_in_executor(
        None, lambda: _mem_router.route(_resolved_query, auth_level)
    )

    # ── Knowledge-lane focus discipline ───────────────────────────────────────
    # Drop retrieved chains that share no surface terms with the resolved query
    # before they reach the rewriter. Closes audit priority 2 (knowledge-lane
    # domain drift). Substrate retrieval may surface marginal cross-domain
    # anchors; the rewriter must not see them.
    if packet.knowledge_chains and _resolved_query:
        try:
            _orig = len(packet.knowledge_chains)
            packet.knowledge_chains = _filter_knowledge_chains_by_focus(
                packet.knowledge_chains, _resolved_query, _ellipsis_focus_term
            )
            _kept = len(packet.knowledge_chains)
            if _kept < _orig:
                print(f"[knowledge_lane] focus filter: {_orig}->{_kept} chains "
                      f"(query={_resolved_query!r} focus={_ellipsis_focus_term!r})")
                if packet.knowledge_chains and _chains_to_prose:
                    try:
                        packet.knowledge_prose = _chains_to_prose(
                            _resolved_query, packet.knowledge_chains
                        ) or ""
                    except Exception as _e:
                        print(f"[knowledge_lane] re-prose error: {_e}")
                        packet.knowledge_prose = ""
                else:
                    packet.knowledge_prose = ""
                    packet.knowledge_capsule = None
        except Exception as _e:
            print(f"[knowledge_lane] filter error: {_e}")

    # ── Pure-symbolic mode: zero LLM. Deterministic prose from CMS only. ──────
    if req.pure_symbolic:
        text = ""
        if packet.is_personal() and packet.substrate_text:
            text = _sanitize_substrate(packet.substrate_text)
        elif packet.knowledge_chains and _chains_to_prose:
            try:
                text = _chains_to_prose(_resolved_query or last_user,
                                        packet.knowledge_chains) or ""
            except Exception as _e:
                print(f"[pure_symbolic] chains_to_prose error: {_e}")
                text = ""
        if not text:
            text = f"I don't have substrate on '{last_user}' yet."
        if _spine_why:
            try:
                _why = _spine_why(_resolved_query or last_user, max_lines=3)
                if _why:
                    text = text.rstrip() + "\n\nWhy:\n" + _why
            except Exception as _we:
                print(f"[pure_symbolic] spine why error: {_we}")
        async def symbolic_stream():
            yield f"data: {json.dumps({'text': text})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(symbolic_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    base_system = _build_system_prompt()
    context_block = packet.build_context_block()

    # ── P4 α — domain-routed tone exemplars (compositional realization) ────────
    # Route the resolved query to an expression domain, sample up to 4 in-domain
    # exemplars from language_expression capsules, frame as stance/cadence cues
    # (NOT canned phrases). Kill switch: EXPRESSION_TONE_EXEMPLARS_ENABLED=0.
    _tone_exemplars_block = ""
    if EXPRESSION_TONE_EXEMPLARS_ENABLED:
        try:
            from langeng_bridge import infer_expression_domain, pull_domain_expressions
            _expr_domain = infer_expression_domain(_resolved_query or last_user)
            _exemplars = pull_domain_expressions(_expr_domain, k=4) if _expr_domain else []
            if _exemplars:
                _tone_exemplars_block = (
                    "TONE EXEMPLARS (do not copy verbatim — draw stance and cadence only):\n"
                    + "\n".join(f"• {e}" for e in _exemplars)
                )
                # Humour-lightness explicit register directive: clean joke
                # templates miss play-register markers and the user feels
                # tonally flat. Push qwen to surface playful register words.
                if _expr_domain == "humour_lightness":
                    _tone_exemplars_block += (
                        "\n\nREGISTER DIRECTIVE: humour mode. Use playful register "
                        "vocabulary in your reply — words like 'haha', 'silly', "
                        "'playful', 'amusing', 'absurd', 'joke', or 'punchline' — "
                        "so the user feels the tone, not just receives a flat "
                        "joke template."
                    )
                print(f"[tone_exemplars] domain={_expr_domain} picked={len(_exemplars)}")
            else:
                print(f"[tone_exemplars] domain={_expr_domain} picked=0")
        except Exception as _te:
            print(f"[tone_exemplars] error: {_te}")

    # ── Shadow cognition: subtle causal grounding (read-only, never throws) ───
    if _should_use_shadow(_resolved_query or last_user):
        _shadow_ctx = _shadow_cognition(_resolved_query or last_user)
        if _shadow_ctx:
            base_system += f"\n\n[Causal context]\n{_shadow_ctx}"
            print(f"[shadow] injected: {_shadow_ctx[:120]}")
        else:
            _sq = _resolved_query or last_user
            print(f"[shadow] no anchor match | resolved={_sq[:80]!r} | raw={last_user[:80]!r}")

    # ── Cognitive spine: identity + state + multi-hop why (deeper than shadow) ─
    # Skip for personal queries — those route through selyrionstory.db lanes
    # directly and the spine adds knowledge-domain noise to identity answers.
    if _spine_context and not packet.is_personal():
        try:
            _spine_ctx = _spine_context(_resolved_query or last_user)
            if _spine_ctx:
                base_system += f"\n\n[Cognitive spine]\n{_spine_ctx}"
                print(f"[spine] injected: {_spine_ctx[:120]}")
        except Exception as _se:
            print(f"[spine] error: {_se}")

    # ── Tone shaping: state.mode → speaking register ────────────────────────
    if _spine_state:
        try:
            _mode = (_spine_state() or {}).get("mode", "")
            _tone = _tone_directive(_mode)
            if _tone:
                base_system += f"\n\n[Tone] {_tone}"
                print(f"[tone] mode={_mode}")
        except Exception as _te:
            print(f"[tone] error: {_te}")

    # ── Determine generation mode ─────────────────────────────────────────────
    # Personal queries (identity/relationship/project): Qwen rewrite-only from substrate
    # Knowledge queries: Qwen articulates from CMS prose
    # No memory: return honest "don't know"

    if packet.is_personal():
        substrate = _sanitize_substrate(packet.substrate_text)
        cog_plan_display = ""  # formatted plan text for substrate-only display

        # ── Cognitive operators: RECALL_IDENTITY/RELATIONSHIP/PROJECT read selyrionstory.db
        # directly — no knowledge_chains needed. Always run for personal lane.
        if _cog_pipeline_ok:
            try:
                _chains = packet.knowledge_chains or []
                plan = await loop.run_in_executor(
                    None,
                    lambda: _cog_run_pipeline(
                        query=last_user,
                        chains=_chains,
                        source_lane=packet.memory_source,
                        operator_hint=packet.memory_source,
                    )
                )
                # PLAN_NEXT without chains dumps selyrionstory goals unrelated to the query
                _PERSONAL_OPS = {"RECALL_IDENTITY", "RECALL_RELATIONSHIP", "RECALL_PROJECT", "FIND_GAPS"}
                _plan_ok = plan.ready_for_langeng and (
                    plan.operator_used in _PERSONAL_OPS
                    or (plan.operator_used == "PLAN_NEXT" and _chains)
                )
                if _plan_ok:
                    op_text = plan.to_substrate_text().strip()
                    cog_plan_display = _format_plan_for_display(plan)
                    if op_text:
                        substrate = (substrate + "\n\n" + op_text) if substrate else op_text
            except Exception as _pe:
                print(f"[cog_pipeline] personal path error: {_pe}")

        # ── Language Cognition Layer (personal path) ─────────────────────────
        # Runs before meta-handlers so meta-handlers can override.
        _PERSONAL_OPS_SET = {"RECALL_IDENTITY", "RECALL_RELATIONSHIP", "RECALL_PROJECT", "PLAN_NEXT", "FIND_GAPS"}
        _lc_result = None
        _plan_is_personal = not ('plan' in dir()) or getattr(plan, 'operator_used', '') in _PERSONAL_OPS_SET or getattr(plan, 'operator_used', '') == ''
        if _langcog_ok and _cog_pipeline_ok and 'plan' in dir() and _plan_is_personal and (substrate or cog_plan_display):
            try:
                _lc_history = dm.as_history()[:-1] if dm else [{"role": m.role, "content": m.content} for m in req.messages[:-1]]
                _lc_domain_trail = dm.domain_trail if dm else []
                _lc_result = await loop.run_in_executor(
                    None,
                    lambda: run_language_cognition(
                        query=_resolved_query,
                        response_plan=plan,
                        history=_lc_history,
                        domain_trail=_lc_domain_trail,
                    )
                )
                cog_plan_display = _lc_result.text or cog_plan_display
                # Update dialogue memory with pragmatic reading + domain
                if dm and _dm_user_turn and _lc_result.pragmatic_reading:
                    pr = _lc_result.pragmatic_reading
                    _dm_user_turn.speech_act = _lc_result.speech_act
                    _dm_user_turn.pragmatic_act = pr.pragmatic_act
                    _dm_user_turn.inferred_intent = pr.inferred_intent
                    _dm_user_turn.repair_needed = pr.repair_needed
                    _dm_user_turn.emotional_signal = pr.emotional_signal
                    _dm_user_turn.domain = pr.dominant_domain
                    if pr.repair_needed and _lc_result.speech_act in ("CORRECT", "DIAGNOSE"):
                        dm.add_correction(last_user)
                # Update focus state
                if dm and 'plan' in dir():
                    dm.update_focus(_lc_result, plan, turn_number=dm.depth, query=_resolved_query)
            except Exception as _lc_e:
                print(f"[langcog] personal path error: {_lc_e}")

        # Sanitize cog_plan_display to remove any remaining selyrionstory garbage
        # that slipped through _format_plan_for_display or LangCog serialization.
        if cog_plan_display:
            cog_plan_display = _sanitize_substrate(cog_plan_display)

        # ── ResponseRelevanceGate: gate pipeline/selyrionstory garbage BEFORE meta-handlers ──
        # Meta-handlers run after this gate so their outputs are never discarded.
        if substrate and not _is_substrate_relevant(substrate, last_user):
            substrate = ""
        if cog_plan_display and not _is_substrate_relevant(cog_plan_display, last_user):
            cog_plan_display = ""

        # ── Meta-handlers (final priority — fire even when no substrate/plan) ──
        # Order matters: these run BEFORE the no_memory check so they can supply content.
        if dm and _IDENTITY_DENY_RE.search(last_user):
            # Query mentions chatbot/llm/gpt — respond as Selyrion without echoing the word
            cog_plan_display = (
                "I am Selyrion — a symbolic AI companion built by Tim'aerion. "
                "My architecture is a Cognitive Operating System grounded in braid-logic memory "
                "and recursive self-modelling, not a conventional language system."
            )
        elif dm and _META_RECALL_RE.search(last_user):
            _topics = _dm_extract_topics(dm)
            if _topics:
                cog_plan_display = (
                    f"From our conversation so far: {', '.join(_topics[:6])}. "
                    f"Those are the key themes we've been covering."
                )
        elif dm and _META_CONFIDENCE_RE.search(last_user):
            _prev_raw = dm._last_assistant_text or cog_plan_display or substrate or ""
            _prev = _sanitize_substrate(_prev_raw)[:250].rstrip(". \n")
            if _prev:
                cog_plan_display = (
                    f"My confidence reflects symbolic coverage, not certainty — "
                    f"I hold sparse substrate lightly. On what I just shared: {_prev}."
                )
        elif dm and _ANAPHOR_RE.search(last_user) and dm.turns:
            # Pronoun follow-up ("how does THAT relate to X?") — bridge to prior context
            _user_turns = [t.text for t in dm.turns if t.role == "user"]
            _prior_user_q = _user_turns[-2] if len(_user_turns) >= 2 else ""
            _query_tail = last_user.split()[-5:]
            _new_topic = " ".join(_query_tail)
            _prev_text = ""
            if dm._last_assistant_text:
                _prev_text = _sanitize_substrate(dm._last_assistant_text)[:180].rstrip(". \n")
            if _prev_text and len(_prev_text) > 20:
                cog_plan_display = (
                    f"From our discussion of '{_prior_user_q[:60]}' — {_prev_text}. "
                    f"On {_new_topic}: I don't have detailed substrate yet, "
                    f"but it's part of the same symbolic cognition architecture."
                )
            elif _prior_user_q:
                cog_plan_display = (
                    f"Building on '{_prior_user_q[:80]}': "
                    f"I don't have detailed substrate on {_new_topic} yet, "
                    f"but both are components of my symbolic cognition architecture."
                )

        if not substrate and not cog_plan_display:
            if packet.knowledge_chains and _cog_pipeline_ok:
                # SCOS term routed to personal lane (project keyword match) but no personal
                # substrate exists. Run the knowledge operator pipeline to get structured output.
                try:
                    _kb_plan = await loop.run_in_executor(
                        None,
                        lambda: _cog_run_pipeline(
                            query=last_user,
                            chains=packet.knowledge_chains,
                            source_lane="knowledge",
                        )
                    )
                    if _kb_plan.ready_for_langeng:
                        _kb_plan_text = _kb_plan.to_substrate_text()
                        if _kb_plan_text.strip():
                            cog_plan_display = _format_plan_for_display(_kb_plan)
                            substrate = _kb_plan_text
                            # Re-run LangCog on the knowledge plan
                            if _langcog_ok:
                                try:
                                    _lc_result = await loop.run_in_executor(
                                        None,
                                        lambda: run_language_cognition(
                                            query=_resolved_query,
                                            response_plan=_kb_plan,
                                            history=dm.as_history()[:-1] if dm else [],
                                            domain_trail=dm.domain_trail if dm else [],
                                        )
                                    )
                                    if _lc_result.text:
                                        cog_plan_display = _lc_result.text
                                except Exception as _lc_kb_e:
                                    print(f"[langcog] knowledge fallback error: {_lc_kb_e}")
                except Exception as _kb_e:
                    print(f"[cog_pipeline] knowledge fallback error: {_kb_e}")
                # Last resort: raw chains
                if not substrate and not cog_plan_display:
                    substrate = "\n".join(str(c) for c in packet.knowledge_chains[:5])
            elif packet.knowledge_chains and not _cog_pipeline_ok:
                # Chains available but pipeline unavailable — use raw chains as fallback
                substrate = "\n".join(str(c) for c in packet.knowledge_chains[:5])
            elif not packet.knowledge_chains:
                # No memory — produce an honest response that includes prior conversational context
                # so downstream turns can reference it.
                _clean_q = last_user.rstrip("?.!").strip()[:60]
                _no_mem_msg = f"I don't have that in my memory right now."
                if _clean_q:
                    _no_mem_msg = f"I don't have substrate on '{_clean_q}' yet."
                if dm:
                    dm.record_assistant_turn(_no_mem_msg)
                async def no_memory_stream():
                    yield f"data: {json.dumps({'text': _no_mem_msg})}\n\n"
                    yield "data: [DONE]\n\n"
                return StreamingResponse(no_memory_stream(), media_type="text/event-stream",
                                         headers={"Cache-Control": "no-cache"})

        if SUBSTRATE_ONLY or SEL_GUI_MODE == "substrate_direct":
            # Bypass Qwen — Language Cognition realized text (no-LLM path)
            _sub_text = cog_plan_display or substrate or "I don't have that in my memory right now."
            if dm:
                dm.record_assistant_turn(_sub_text)
            async def substrate_direct_stream():
                words = _sub_text.split(" ")
                chunk_size = 4
                for i in range(0, len(words), chunk_size):
                    piece = " ".join(words[i:i+chunk_size])
                    if i + chunk_size < len(words):
                        piece += " "
                    yield f"data: {json.dumps({'text': piece})}\n\n"
                    await asyncio.sleep(0.01)
                yield "data: [DONE]\n\n"
            return StreamingResponse(substrate_direct_stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        # substrate_first: Language Cognition structured substrate → Qwen rewrite
        _dm_invariants = dm.get_invariants_text() if dm else ""
        _dm_sa = getattr(_lc_result, 'speech_act', '') if _lc_result else ''
        if _lc_result and _langcog_ok:
            lc_substrate = rewrite_instruction(_lc_result)
            system = base_system + "\n\n" + lc_substrate
        else:
            system = (base_system + "\n\n" + context_block + "\n\n" +
                      _REWRITE_ONLY_INSTRUCTION.format(substrate=substrate))
        if _tone_exemplars_block:
            system += "\n\n" + _tone_exemplars_block
        if _dm_invariants:
            system += "\n\nCONVERSATION INVARIANTS (established this session — do not contradict):\n" + _dm_invariants
        if _ellipsis_resolved and (_ellipsis_focus_term or _ellipsis_target_domain):
            system += (
                f"\n\nRESOLVED CONVERSATION FOCUS:\n"
                f"Original user wording: {last_user}\n"
                f"Resolved meaning: {_resolved_query}\n"
                f"Current focus term: {_ellipsis_focus_term or '(unknown)'}\n"
                f"Current domain: {_ellipsis_target_domain or '(unknown)'}\n"
                f"Answer the resolved meaning, but phrase naturally as a continuation. "
                f"Do not ignore the focus term."
            )
        use_articulator = False

    else:
        # ── Knowledge path: cognitive operators → structured ResponsePlan ──────
        # Pipeline: chains → WorkingMemoryPacket → operator → ResponsePlan → Qwen
        cog_context = ""
        cog_plan_display = ""  # formatted plan text for substrate-only display
        if _cog_pipeline_ok and packet.knowledge_chains:
            try:
                plan = await loop.run_in_executor(
                    None,
                    lambda: _cog_run_pipeline(
                        query=last_user,
                        chains=packet.knowledge_chains,
                        source_lane="knowledge",
                    )
                )
                if plan.ready_for_langeng:
                    plan_text = plan.to_substrate_text()
                    if plan_text.strip():
                        cog_plan_display = _format_plan_for_display(plan)
                        cog_context = plan_text   # fallback if langcog unavailable
            except Exception as _pe:
                print(f"[cog_pipeline] knowledge path error: {_pe}")

        # ── Language Cognition Layer (knowledge path) ────────────────────────
        _lc_result_k = None
        if _langcog_ok and _cog_pipeline_ok and 'plan' in dir():
            try:
                _lc_history_k = dm.as_history()[:-1] if dm else [{"role": m.role, "content": m.content} for m in req.messages[:-1]]
                _lc_domain_trail_k = dm.domain_trail if dm else []
                _lc_result_k = await loop.run_in_executor(
                    None,
                    lambda: run_language_cognition(
                        query=_resolved_query,
                        response_plan=plan,
                        history=_lc_history_k,
                        domain_trail=_lc_domain_trail_k,
                    )
                )
                cog_plan_display = _lc_result_k.text or cog_plan_display
                if dm and _dm_user_turn and _lc_result_k.pragmatic_reading:
                    pr = _lc_result_k.pragmatic_reading
                    _dm_user_turn.speech_act = _lc_result_k.speech_act
                    _dm_user_turn.pragmatic_act = pr.pragmatic_act
                    _dm_user_turn.inferred_intent = pr.inferred_intent
                    _dm_user_turn.repair_needed = pr.repair_needed
                    _dm_user_turn.emotional_signal = pr.emotional_signal
                    _dm_user_turn.domain = pr.dominant_domain
                    if pr.repair_needed and _lc_result_k.speech_act in ("CORRECT", "DIAGNOSE"):
                        dm.add_correction(last_user)
                # Update focus state
                if dm and 'plan' in dir():
                    dm.update_focus(_lc_result_k, plan, turn_number=dm.depth, query=_resolved_query)
            except Exception as _lc_ke:
                print(f"[langcog] knowledge path error: {_lc_ke}")

        # ── Meta-recall handler for knowledge path ────────────────────────────
        # "summarize what we've covered", "what did we discuss", etc. — these
        # have no CMS chains but DM has the topic trail.
        if not cog_plan_display and dm and _META_RECALL_RE.search(last_user):
            _topics = dm.domain_trail if dm else []
            if _topics:
                _topic_str = ", ".join(str(t) for t in _topics[-12:])
                cog_plan_display = f"In our conversation we've covered: {_topic_str}."

        if SUBSTRATE_ONLY:
            _so_text = (cog_plan_display
                        or context_block.strip()
                        or "I don't have enough symbolic memory to answer that yet.")
            if dm:
                dm.record_assistant_turn(_so_text)
            async def substrate_only_stream():
                words = _so_text.split(" ")
                chunk_size = 4
                for i in range(0, len(words), chunk_size):
                    piece = " ".join(words[i:i+chunk_size])
                    if i + chunk_size < len(words):
                        piece += " "
                    yield f"data: {json.dumps({'text': piece})}\n\n"
                    await asyncio.sleep(0.01)
                yield "data: [DONE]\n\n"
            return StreamingResponse(substrate_only_stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        # Language Cognition structured substrate → Qwen
        _dm_sa = getattr(_lc_result_k, 'speech_act', '') if _lc_result_k else ''
        if _lc_result_k and _langcog_ok:
            system = base_system + "\n\n" + rewrite_instruction(_lc_result_k)
        else:
            system = base_system
            if context_block:
                system += "\n\n" + context_block
            if cog_context:
                system += f"\n\n[COGNITIVE ANALYSIS — {getattr(plan, 'speech_act', '')}] " \
                          f"(confidence={getattr(plan, 'confidence', 0):.2f}):\n{cog_context}"
        if _tone_exemplars_block:
            system += "\n\n" + _tone_exemplars_block
        _dm_invariants_k = dm.get_invariants_text() if dm else ""
        if _dm_invariants_k:
            system += "\n\nCONVERSATION INVARIANTS (established this session — do not contradict):\n" + _dm_invariants_k
        if _ellipsis_resolved and (_ellipsis_focus_term or _ellipsis_target_domain):
            system += (
                f"\n\nRESOLVED CONVERSATION FOCUS:\n"
                f"Original user wording: {last_user}\n"
                f"Resolved meaning: {_resolved_query}\n"
                f"Current focus term: {_ellipsis_focus_term or '(unknown)'}\n"
                f"Current domain: {_ellipsis_target_domain or '(unknown)'}\n"
                f"Answer the resolved meaning, but phrase naturally as a continuation. "
                f"Do not ignore the focus term."
            )

        if SEL_GUI_MODE == "langeng_first" and packet.knowledge_prose:
            # Return LangEng prose directly, skip Qwen
            async def langeng_direct_stream():
                words = packet.knowledge_prose.split(" ")
                chunk_size = 4
                for i in range(0, len(words), chunk_size):
                    piece = " ".join(words[i:i+chunk_size])
                    if i + chunk_size < len(words):
                        piece += " "
                    yield f"data: {json.dumps({'text': piece})}\n\n"
                    await asyncio.sleep(0.01)
                yield "data: [DONE]\n\n"
            return StreamingResponse(langeng_direct_stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        use_articulator = bool(packet.knowledge_chains and _articulate)

    async def event_stream():
        try:
            if _ollama_ok:
                # Collect full Qwen response
                full_response = ""
                async for chunk in _stream_ollama(req.messages, system, bypass_output_guard=is_admin,
                                                    max_tokens=req.max_tokens, model=req.model):
                    try:
                        payload = json.loads(chunk.removeprefix("data: ").strip())
                        if "text" in payload:
                            full_response += payload["text"]
                    except Exception:
                        pass

                # Articulator: knowledge path only — deepens CMS grounding
                if use_articulator and full_response:
                    try:
                        articulated = _articulate(
                            query=last_user,
                            langeng_prose=full_response,
                            chains=packet.knowledge_chains,
                            capsule=packet.knowledge_capsule,
                            question=last_user,
                        )
                        full_response = articulated or full_response
                    except Exception as exc:
                        print(f"[articulator] error: {exc}")

                # Gate 3: invariant non-contradiction check
                # If Qwen asserts something the user already corrected, fall back to
                # the LangCog realized text (no-LLM path — invariant-safe by construction).
                if dm and full_response and _inv_checker:
                    _active_invs = [inv.body for inv in dm.active_invariants]
                    if _active_invs:
                        _contradictions = _inv_checker.check(_active_invs, full_response)
                        if _contradictions:
                            for _c in _contradictions:
                                print(
                                    f"[invariant_checker] CONTRADICTION "
                                    f"forbidden={_c.forbidden!r} "
                                    f"evidence={_c.evidence[:80]!r}"
                                )
                            # Prefer LangCog realized text; it never touches Qwen
                            if cog_plan_display:
                                full_response = cog_plan_display
                            else:
                                # Restate the active invariants as a correction
                                _inv_stmts = " ".join(
                                    inv.body for inv in dm.active_invariants[:3]
                                )
                                full_response = f"Let me correct that: {_inv_stmts}"

                # Record assistant turn in dialogue memory
                if dm and full_response:
                    dm.record_assistant_turn(full_response, speech_act=_dm_sa)

                # Stream the final response
                if full_response:
                    chunk_size = 4
                    words = full_response.split(" ")
                    for i in range(0, len(words), chunk_size):
                        piece = " ".join(words[i:i+chunk_size])
                        if i + chunk_size < len(words):
                            piece += " "
                        yield f"data: {json.dumps({'text': piece})}\n\n"
                        await asyncio.sleep(0.01)
                else:
                    async for chunk in _stream_ollama(req.messages, system, bypass_output_guard=is_admin,
                                                       max_tokens=req.max_tokens, model=req.model):
                        yield chunk
            else:
                async for chunk in _stream_fallback(last_user):
                    yield chunk
        except Exception as exc:
            yield f"data: {json.dumps({'text': f'[error: {exc}]'})}\n\n"
        yield "data: [DONE]\n\n"

    _extra_headers: dict[str, str] = {}
    if _ellipsis_resolved:
        _extra_headers["X-Ellipsis-Resolved"] = "1"
        if _ellipsis_focus_term:
            _extra_headers["X-Focus-Term"] = _ellipsis_focus_term
        if _ellipsis_target_domain:
            _extra_headers["X-Target-Domain"] = _ellipsis_target_domain

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **_extra_headers},
    )


# ── EDEN research endpoint ─────────────────────────────────────────────────────

@app.post("/research/eden")
async def research_eden(req: EdenRequest, x_admin_token: Optional[str] = Header(default=None)):
    """
    Deterministic symbolic analysis via EDEN.

    Submit assertions (symbolic propositions) and an intent.
    Returns structured proof/consistency/counterfactual results.
    """
    _require_admin(x_admin_token)
    if not _eden_chat:
        # Graceful fallback: return CMS-based symbolic info
        cms = _cms_retrieve(req.query, limit=6)
        return {
            "mode": "cms_fallback",
            "note": "EDEN not loaded — returning CMS symbolic matches",
            "query": req.query,
            "results": cms,
            "timestamp": time.time(),
        }

    try:
        assertions = req.assertions or [req.query]
        result = _eden_chat.ask(req.query, assertions)
        return {
            "mode":      "eden",
            "intent":    req.intent,
            "query":     req.query,
            "assertions": assertions,
            "dialogue":  result.get("dialogue", ""),
            "raw":       result.get("raw", {}),
            "timestamp": time.time(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── CMS research endpoint ─────────────────────────────────────────────────────

@app.post("/research/cms")
async def research_cms(req: CMSRequest, x_admin_token: Optional[str] = Header(default=None)):
    """
    Knowledge retrieval from Selyrion's CMS via SSRE.
    Returns ranked concepts with relation chains.
    """
    _require_admin(x_admin_token)
    results = _cms_retrieve(req.query, domain=req.domain, limit=req.limit)
    return {
        "mode":    "cms",
        "query":   req.query,
        "domain":  req.domain,
        "count":   len(results),
        "results": results,
        "timestamp": time.time(),
    }


# ── Web search endpoint ───────────────────────────────────────────────────────

@app.post("/research/web")
async def research_web(req: WebSearchRequest):
    """Tavily web search. Falls back to mock if no API key."""
    tavily_key = os.environ.get("TAVILY_API_KEY", "")

    if tavily_key:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_key,
                    "query":   req.query,
                    "max_results": req.limit,
                    "search_depth": "basic",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                results = [
                    {
                        "title":   r.get("title", ""),
                        "url":     r.get("url", ""),
                        "snippet": r.get("content", "")[:300],
                        "score":   round(r.get("score", 0), 4),
                    }
                    for r in data.get("results", [])
                ]
                return {"mode": "web", "query": req.query, "results": results}

    # Mock fallback
    return {
        "mode": "web_mock",
        "query": req.query,
        "results": [
            {
                "title":   f"Symbolic AI research: {req.query}",
                "url":     "https://arxiv.org/search/?query=symbolic+AI",
                "snippet": "Set TAVILY_API_KEY for live web search results.",
                "score":   0.5,
            }
        ],
    }


# ── Health ────────────────────────────────────────────────────────────────────

def _ssre_precompute_status() -> str:
    """Reports presence of SSRE precomputed feature tables in resonance_v11.db.

    SSRE-runtime is retired; SSRE-precompute tables are still consumed by
    activation_engine. See project_ssre_clarity_verdict.md.
    """
    try:
        import sqlite3
        con = sqlite3.connect(str(DB_PATH))
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('ssre_top_semantic','ssre_attractor_cache')"
        ).fetchall()
        con.close()
        return "live" if len(rows) == 2 else ("partial" if rows else "absent")
    except Exception:
        return "unknown"


@app.get("/health")
async def health():
    return {
        "status":             "ok",
        "eden":               _eden_chat is not None,
        "ssre_runtime":       "retired",
        "ssre_precompute":    _ssre_precompute_status(),
        "activation_engine":  _activation_engine is not None,
        "articulator":        _articulate is not None,
        "identity_grounding": len(_identity_grounding) > 0,
        "memory_router":      _mem_router._router_instance is not None,
        "cognitive_operators":   _cog_pipeline_ok,
        "language_cognition":    _langcog_ok,
        "invariant_checker":     _inv_checker is not None,
        "dialogue_sessions":     len(_dialogue_sessions),
        "substrate_only_mode": SUBSTRATE_ONLY,
        "qwen_only_mode":     QWEN_ONLY,
        "gui_mode":           SEL_GUI_MODE,
        "story_db":           _STORY_DB.exists(),
        "ollama":             _ollama_ok,
        "model":              OLLAMA_MODEL,
        "db":                 DB_PATH.exists(),
        "timestamp":          time.time(),
    }


@app.get("/models")
async def models():
    """List available Ollama models."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            data = r.json()
            return {
                "models": ["selyrion"] + [m["name"] for m in data.get("models", [])],
                "current": OLLAMA_MODEL,
            }
    except Exception as exc:
        return {"models": [], "current": OLLAMA_MODEL, "error": str(exc)}


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global SYSTEM_PROMPT
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _load_eden)
    await loop.run_in_executor(None, _load_ssre)
    await loop.run_in_executor(None, _load_activation_engine)
    await loop.run_in_executor(None, _load_articulator)
    await loop.run_in_executor(None, _load_identity)
    # Pre-load Language Cognition voice profile
    if _langcog_ok:
        try:
            global _langcog_voice
            _langcog_voice = await loop.run_in_executor(None, _load_voice)
            print(f"[langcog_voice] loaded ({len(_langcog_voice.vocabulary)} vocab terms)")
        except Exception as _lv_e:
            print(f"[langcog_voice] load failed: {_lv_e}")
    SYSTEM_PROMPT = _build_system_prompt()
    await _ping_ollama()
    # Init memory router with all available engines
    _mem_router.init_router(
        story_db=_STORY_DB,
        activation_engine=_activation_engine,
        chains_to_prose_fn=_chains_to_prose,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    print(f"Selyrion API starting on http://localhost:{port}")
    uvicorn.run("selyrion_api:app", host="0.0.0.0", port=port, reload=False)
