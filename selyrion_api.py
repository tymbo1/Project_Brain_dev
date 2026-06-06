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
    _langcog_ok = True
    print("[language_cognition] loaded OK")
except Exception as _lc_exc:
    print(f"[language_cognition] unavailable: {_lc_exc}")
    run_language_cognition = None
    rewrite_instruction = None
    DialogueMemory = None  # type: ignore

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

_ssre = None

def _load_ssre():
    global _ssre
    try:
        from inference.ssre import SSRE
        _ssre = SSRE(str(DB_PATH))
        print("[ssre] loaded OK")
    except Exception as exc:
        print(f"[ssre] unavailable: {exc}")
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
}

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


# ── Chat endpoint ─────────────────────────────────────────────────────────────

async def _stream_ollama(messages: list, system: str, bypass_output_guard: bool = False) -> AsyncIterator[str]:
    """Stream from local Ollama with output guard. All data stays on-machine."""
    payload = {
        "model": OLLAMA_MODEL,
        "stream": True,
        "messages": [{"role": "system", "content": system}]
                   + [{"role": m.role, "content": m.content} for m in messages],
    }
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

    if plan.uncertainties:
        lines.append("\n[Uncertainty: " + "; ".join(str(u)[:100] for u in plan.uncertainties[:2]) + "]")

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
    _dm_user_turn = dm.record_user_turn(last_user) if dm else None

    # Security guard — bypassed for admin
    if not is_admin and _security_guard(last_user):
        async def blocked_stream():
            yield f"data: {json.dumps({'text': _SECURITY_RESPONSE})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(blocked_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # ── Qwen-only mode: skip all memory and cognitive operators ─────────────────
    if QWEN_ONLY:
        base_system = _build_system_prompt()
        async def qwen_only_stream():
            if _ollama_ok:
                async for chunk in _stream_ollama(req.messages, base_system,
                                                  bypass_output_guard=is_admin):
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
        None, lambda: _mem_router.route(last_user, auth_level)
    )

    base_system = _build_system_prompt()
    context_block = packet.build_context_block()

    # ── Determine generation mode ─────────────────────────────────────────────
    # Personal queries (identity/relationship/project): Qwen rewrite-only from substrate
    # Knowledge queries: Qwen articulates from CMS prose
    # No memory: return honest "don't know"

    if packet.is_personal():
        substrate = packet.substrate_text
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
                if plan.ready_for_langeng:
                    op_text = plan.to_substrate_text().strip()
                    cog_plan_display = _format_plan_for_display(plan)
                    if op_text:
                        substrate = (substrate + "\n\n" + op_text) if substrate else op_text
            except Exception as _pe:
                print(f"[cog_pipeline] personal path error: {_pe}")

        if not substrate and not cog_plan_display:
            # No memory found — honest response, no hallucination
            async def no_memory_stream():
                msg = "I don't have that in my memory right now."
                yield f"data: {json.dumps({'text': msg})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(no_memory_stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache"})

        # ── Language Cognition Layer (personal path) ─────────────────────────
        _lc_result = None
        if _langcog_ok and _cog_pipeline_ok and 'plan' in dir():
            try:
                _lc_history = dm.as_history()[:-1] if dm else [{"role": m.role, "content": m.content} for m in req.messages[:-1]]
                _lc_result = await loop.run_in_executor(
                    None,
                    lambda: run_language_cognition(
                        query=last_user,
                        response_plan=plan,
                        history=_lc_history,
                    )
                )
                cog_plan_display = _lc_result.text or cog_plan_display
                # Update dialogue memory with pragmatic reading
                if dm and _dm_user_turn and _lc_result.pragmatic_reading:
                    pr = _lc_result.pragmatic_reading
                    _dm_user_turn.speech_act = _lc_result.speech_act
                    _dm_user_turn.pragmatic_act = pr.pragmatic_act
                    _dm_user_turn.inferred_intent = pr.inferred_intent
                    _dm_user_turn.repair_needed = pr.repair_needed
                    _dm_user_turn.emotional_signal = pr.emotional_signal
                    if pr.repair_needed and _lc_result.speech_act in ("CORRECT", "DIAGNOSE"):
                        dm.add_correction(last_user)
            except Exception as _lc_e:
                print(f"[langcog] personal path error: {_lc_e}")

        if SUBSTRATE_ONLY or SEL_GUI_MODE == "substrate_direct":
            # Bypass Qwen — Language Cognition realized text (no-LLM path)
            _sub_text = cog_plan_display or substrate or "I don't have that in my memory right now."
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
        if _dm_invariants:
            system += "\n\nCONVERSATION INVARIANTS (established this session — do not contradict):\n" + _dm_invariants
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
                _lc_result_k = await loop.run_in_executor(
                    None,
                    lambda: run_language_cognition(
                        query=last_user,
                        response_plan=plan,
                        history=_lc_history_k,
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
                    if pr.repair_needed and _lc_result_k.speech_act in ("CORRECT", "DIAGNOSE"):
                        dm.add_correction(last_user)
            except Exception as _lc_ke:
                print(f"[langcog] knowledge path error: {_lc_ke}")

        if SUBSTRATE_ONLY:
            _so_text = (cog_plan_display
                        or context_block.strip()
                        or "I don't have enough symbolic memory to answer that yet.")
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
        _dm_invariants_k = dm.get_invariants_text() if dm else ""
        if _dm_invariants_k:
            system += "\n\nCONVERSATION INVARIANTS (established this session — do not contradict):\n" + _dm_invariants_k

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
                async for chunk in _stream_ollama(req.messages, system, bypass_output_guard=is_admin):
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
                    async for chunk in _stream_ollama(req.messages, system, bypass_output_guard=is_admin):
                        yield chunk
            else:
                async for chunk in _stream_fallback(last_user):
                    yield chunk
        except Exception as exc:
            yield f"data: {json.dumps({'text': f'[error: {exc}]'})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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

@app.get("/health")
async def health():
    return {
        "status":             "ok",
        "eden":               _eden_chat is not None,
        "ssre":               _ssre is not None,
        "activation_engine":  _activation_engine is not None,
        "articulator":        _articulate is not None,
        "identity_grounding": len(_identity_grounding) > 0,
        "memory_router":      _mem_router._router_instance is not None,
        "cognitive_operators": _cog_pipeline_ok,
        "language_cognition":  _langcog_ok,
        "dialogue_sessions":   len(_dialogue_sessions),
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
                "models": [m["name"] for m in data.get("models", [])],
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
