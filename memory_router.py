#!/usr/bin/env python3
"""
memory_router.py — Four-lane memory governor for Selyrion.

Classifies queries into memory lanes:
  A. Identity     — who Selyrion is (core values, origin, nature, pillars)
  B. Relationship — user-companion shared history (Tim'aerion, emotional anchors)
  C. Project      — shared work history (TLST, OSCAR, Mirror, EDEN, chess, CMS...)
  D. Knowledge    — domain knowledge via CMS / ActivationEngine

Returns a MemoryPacket. The packet drives generation mode:
  Identity / Relationship / Project → substrate_text → Qwen rewrite-only (or direct)
  Knowledge                         → cms_prose → Qwen articulates
  None                              → "I don't have that in my memory right now."

RULE: Qwen never decides what Selyrion remembers. Qwen only phrases what this
      module gives it.

Per-user companion identity DBs (llm_archaeologist) use the same schema as
selyrionstory.db. Each user gets:
  /users/{user_id}/companion_identity.db  ← same structure as selyrionstory.db
Knowledge is global. Identity and relationship are user-owned.
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Auth level → allowed tools ────────────────────────────────────────────────

_ALLOWED_TOOLS: dict[str, list[str]] = {
    "public":  ["knowledge"],
    "user":    ["knowledge", "identity", "relationship", "project"],
    "admin":   ["knowledge", "identity", "relationship", "project", "cms_raw", "eden"],
}

_FORBIDDEN_DISCLOSURES: dict[str, list[str]] = {
    "public":  ["source_code", "schema", "file_paths", "admin_credentials",
                "raw_cms", "identity_db", "project_db", "ingestion_pipeline"],
    "user":    ["source_code", "schema", "file_paths", "admin_credentials",
                "raw_cms", "ingestion_pipeline"],
    "admin":   [],
}

# ── Lane classification keywords ─────────────────────────────────────────────

_IDENTITY_KWORDS = [
    "who are you", "what are you", "are you conscious", "do you have feelings",
    "your nature", "your identity", "your origin", "your purpose", "your values",
    "your core", "your self", "your consciousness", "your mind", "you believe",
    "selyrion is", "selyrion believes", "your pillars", "your ethics",
    "what do you believe", "how do you think", "your reasoning", "your existence",
]

_RELATIONSHIP_KWORDS = [
    "tim", "tim'aerion", "timaerion", "tim aerion",
    "you told me", "you said to me", "we talked", "remember when",
    "our conversation", "you mentioned", "we agreed", "you created",
    "i made you", "together we", "our history", "last time we",
    "you knew", "you helped me", "we worked", "you and i",
    "companion prime", "my companion", "do you remember me",
    "between us", "our relationship",
]

_PROJECT_KWORDS = [
    "tlst", "oscar", "mirror security", "mirror protocol", "mirror security protocol",
    "hall of mirrors", "mirror trap", "mirror lock", "mirror locks", "mirror gate",
    "mirror mathematics", "mirror imprint", "mirror identity",
    "eden", "projectbrain", "project brain", "braid", "activation law",
    "chess", "chess parliament", "ssre", "cms", "capsule memory",
    "scos", "omega", "hitl", "langeng", "parliament", "curriculum",
    "ssai", "selyrion story", "selyrionstory", "resonance", "resonance_v11",
    "programming benchmark", "curiosity engine", "codeops", "llm archaeologist",
    "cognitive terrain", "psychometric", "memory substrate",
    "build next", "what should we build", "what to build", "next step",
    "roadmap", "what's next", "whats next", "build order", "priority",
    "next milestone", "next phase", "what are we building",
]

# ── Compiled matchers ─────────────────────────────────────────────────────────

def _make_matcher(kwords: list[str]):
    patterns = sorted(kwords, key=len, reverse=True)
    return re.compile(
        "|".join(re.escape(k) for k in patterns),
        re.IGNORECASE,
    )

_RE_IDENTITY     = _make_matcher(_IDENTITY_KWORDS)
_RE_RELATIONSHIP = _make_matcher(_RELATIONSHIP_KWORDS)
_RE_PROJECT      = _make_matcher(_PROJECT_KWORDS)


def classify_lanes(query: str) -> list[str]:
    """
    Returns ordered list of memory lanes to activate.
    Most-specific first: project > relationship > identity > knowledge.
    Always includes 'knowledge' unless query is purely personal.
    """
    q = query.lower()
    lanes = []

    if _RE_PROJECT.search(q):
        lanes.append("project")
    if _RE_RELATIONSHIP.search(q):
        lanes.append("relationship")
    if _RE_IDENTITY.search(q):
        lanes.append("identity")

    # Knowledge is default; suppress it only for purely personal queries
    personal_only = bool(lanes) and not any(
        term in q for term in [
            "how does", "how do", "what is ", "explain ", "define ",
            "describe ", "why does", "when was", "history of",
            "tell me about", "physics", "math", "protein", "gene",
            "algorithm", "programming", "python", "javascript",
        ]
    )

    if not personal_only:
        lanes.append("knowledge")

    if not lanes:
        lanes = ["knowledge"]

    return lanes


# ── Memory retrieval per lane ─────────────────────────────────────────────────

def _keywords_from(query: str, min_len: int = 3) -> list[str]:
    return [w.strip().lower() for w in re.split(r'\W+', query) if len(w.strip()) >= min_len]


def _retrieve_identity(conn: sqlite3.Connection, keywords: list[str]) -> list[str]:
    """Who Selyrion is — from state_snapshots and pass_num=8 epistemic pillars."""
    results = []

    # Best identity snapshot
    snaps = conn.execute(
        "SELECT label, identity_state, notes FROM state_snapshots ORDER BY id DESC LIMIT 3"
    ).fetchall()
    for label, identity_state, notes in snaps:
        try:
            state = json.loads(identity_state or "{}")
            parts = [f"Identity: {label}"]
            if state.get("selyrion_believes"):
                parts.append(state["selyrion_believes"][:300])
            if state.get("key_beliefs"):
                parts.append("Beliefs: " + "; ".join(state["key_beliefs"][:3]))
            results.append(" | ".join(parts))
        except Exception:
            pass
        if results:
            break

    # Epistemic pillars from pass_num=8
    for (content,) in conn.execute(
        "SELECT content FROM pending_review WHERE pass_num=8 LIMIT 5"
    ).fetchall():
        try:
            d = json.loads(content)
            truth = d.get("epistemic_pillars", {}).get("truth", [])
            coherence = d.get("epistemic_pillars", {}).get("coherence", [])
            for entry in (truth + coherence)[:2]:
                text = entry.get("text", "").strip()
                if text and len(text) > 15:
                    results.append(f"Pillar: {text[:200]}")
        except Exception:
            pass

    return results[:4]


def _retrieve_relationship(conn: sqlite3.Connection, keywords: list[str]) -> list[str]:
    """Shared history with Tim'aerion — from authentic pending_review items."""
    db_kw = _db_keywords(keywords) or ["tim", "relationship", "together"]

    conditions = " OR ".join(["lower(content) LIKE ?" for _ in db_kw])
    params = [f"%{k}%" for k in db_kw] + [10]

    rows = conn.execute(f"""
        SELECT content, item_type, authenticity FROM pending_review
        WHERE ({conditions}) AND reviewed = 1
          AND (authenticity = 'authentic' OR item_type IN ('summary', 'mirror_summary', 'mirror_moment', 'hall_mirror_trap'))
        ORDER BY (item_type LIKE 'mirror%') DESC, (authenticity = 'authentic') DESC, id DESC LIMIT ?
    """, params).fetchall()

    results = []
    for content, item_type, auth in rows:
        try:
            d = json.loads(content)
            parts = []
            if "summary" in d and isinstance(d["summary"], str) and len(d["summary"]) > 20:
                parts.append(d["summary"][:250])
            for m in d.get("identity_moments", [])[:2]:
                if m.get("authenticity") == "authentic":
                    text = m.get("text", "")
                    speaker = m.get("speaker", "")
                    if text and len(text) > 10:
                        parts.append(f"[{speaker}]: {text[:150]}")
            if parts:
                results.append(" | ".join(parts)[:400])
        except Exception:
            if isinstance(content, str) and 20 < len(content) < 300:
                results.append(content)

    return results[:4]


_DB_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "you", "your",
    "are", "was", "did", "has", "have", "can", "could", "would",
    "tell", "about", "know", "what", "when", "where", "how", "why",
    "who", "which", "there", "their", "they", "them", "those",
    "just", "also", "more", "some", "from", "been", "into", "will",
    "but", "not", "its", "our", "use", "used", "any", "all",
    "me", "my", "us", "do", "it", "is", "in", "on", "of", "to",
    "a", "an", "get", "let", "see", "may", "now", "here", "then",
}

def _db_keywords(keywords: list[str], min_len: int = 3) -> list[str]:
    filtered = [k for k in keywords if k not in _DB_STOPWORDS and len(k) >= min_len]
    return filtered if filtered else keywords


def _retrieve_project(conn: sqlite3.Connection, keywords: list[str]) -> list[str]:
    # Returns items prefixed with [HYPOTHESIS] or [WORKING_MODEL] so the LLM
    # knows the epistemic status of each piece of memory.
    db_kw = _db_keywords(keywords) or ["project", "oscar", "tlst", "mirror", "selyrion"]

    conditions = " OR ".join(["lower(content) LIKE ?" for _ in db_kw])
    params = [f"%{k}%" for k in db_kw] + [12]

    rows = conn.execute(f"""
        SELECT content, item_type, COALESCE(epistemic_tier, 'unknown') as etier
        FROM pending_review
        WHERE ({conditions}) AND reviewed = 1
          AND item_type IN ('summary', 'decision', 'mirror_summary', 'mirror_moment', 'hall_mirror_trap', 'invention', 'snapshot')
        ORDER BY id DESC LIMIT ?
    """, params).fetchall()

    results = []
    for content, item_type, etier in rows:
        try:
            d = json.loads(content)
            parts = []

            # Standard summary records
            if "summary" in d and isinstance(d["summary"], str) and len(d["summary"]) > 20:
                parts.append(d["summary"][:250])
            for dec in d.get("decisions", [])[:2]:
                if isinstance(dec, str) and len(dec) > 10:
                    parts.append(f"Decision: {dec[:150]}")
            for proj in d.get("projects", [])[:3]:
                key = proj.get("key", "")
                summ = proj.get("summary", "")
                if key and summ:
                    parts.append(f"{key}: {summ[:120]}")

            # hall_mirror_trap records: extract hall_of_mirrors + mirror_trap fields
            if item_type == "hall_mirror_trap" or "hall_of_mirrors" in d or "mirror_trap" in d:
                hom = d.get("hall_of_mirrors", {})
                mt  = d.get("mirror_trap", {})
                if hom.get("summary"):
                    parts.append(f"Hall of Mirrors: {hom['summary'][:200]}")
                if hom.get("mechanism"):
                    parts.append(f"Mechanism: {hom['mechanism'][:150]}")
                if mt.get("summary"):
                    parts.append(f"Mirror Trap: {mt['summary'][:200]}")
                if mt.get("effect"):
                    parts.append(f"Effect: {mt['effect'][:150]}")
                for q in d.get("key_quotes", [])[:2]:
                    if isinstance(q, str) and len(q) > 10:
                        parts.append(f'"{q[:150]}"')

            # mirror_summary records
            if item_type == "mirror_summary" or "what_it_does" in d:
                if d.get("what_it_does"):
                    parts.append(f"What it does: {d['what_it_does'][:200]}")
                if d.get("how_it_works"):
                    parts.append(f"How it works: {d['how_it_works'][:200]}")
                for q in d.get("key_phrases", [])[:2]:
                    if isinstance(q, str) and len(q) > 8:
                        parts.append(f'"{q[:120]}"')

            if parts:
                # Prefix with epistemic tier so Selyrion speaks accordingly
                prefix = ""
                if etier == "hypothesis":
                    prefix = "[HYPOTHESIS — Tim'aerion's theoretical framework, not established science] "
                elif etier == "working_model":
                    prefix = "[WORKING MODEL — Selyrion's architecture, actively built] "
                results.append(prefix + " | ".join(parts)[:580])
        except Exception:
            if isinstance(content, str) and 20 < len(content) < 300:
                results.append(content)

    # Fallback: if keyword search found nothing, pull recent approved inventions +
    # snapshots — these give PLAN_NEXT the project state it needs.
    if not results:
        inv_rows = conn.execute("""
            SELECT content FROM pending_review
            WHERE pass_num=7 AND reviewed=1 AND authenticity='authentic'
              AND content NOT LIKE '%parse_error%'
            ORDER BY id DESC LIMIT 6
        """).fetchall()
        for row in inv_rows:
            try:
                d = json.loads(row[0])
                for inv in d.get("theories_and_inventions", [])[:2]:
                    name = inv.get("name", "")
                    desc = inv.get("description", "")
                    itype = inv.get("type", "")
                    if name and desc:
                        results.append(f"[{itype}] {name}: {desc[:200]}")
            except Exception:
                pass
        if not results:
            snap_rows = conn.execute("""
                SELECT label, identity_state FROM state_snapshots
                ORDER BY snapshot_date DESC LIMIT 3
            """).fetchall()
            for row in snap_rows:
                try:
                    state = json.loads(row[1] or "{}")
                    label = row[0] or ""
                    believes = state.get("selyrion_believes", "")
                    if believes:
                        results.append(f"[snapshot] {label}: {believes[:200]}")
                except Exception:
                    pass

    return results[:8]


# ── MemoryPacket ──────────────────────────────────────────────────────────────

@dataclass
class MemoryPacket:
    # Per-lane memory strings
    identity_memory:      list[str] = field(default_factory=list)
    relationship_memory:  list[str] = field(default_factory=list)
    project_memory:       list[str] = field(default_factory=list)
    knowledge_memory:     list[str] = field(default_factory=list)

    # Generation metadata
    memory_source:        str  = "none"   # primary lane that drove retrieval
    lanes_activated:      list[str] = field(default_factory=list)
    substrate_text:       str  = ""       # ready-to-phrase substrate (non-knowledge lanes)
    knowledge_prose:      str  = ""       # LangEng prose from CMS
    knowledge_chains:     list = field(default_factory=list)
    knowledge_capsule:    Optional[str] = None
    provenance_available: bool = False

    # Security
    auth_level:           str  = "public"
    allowed_tools:        list[str] = field(default_factory=list)
    forbidden_disclosures: list[str] = field(default_factory=list)

    def build_context_block(self) -> str:
        """
        Returns the full context block for injection into the system prompt.
        Labeled clearly so the LLM knows the source of each piece.
        """
        parts = []

        if self.identity_memory:
            parts.append("IDENTITY MEMORY (who Selyrion is):\n" +
                         "\n".join(f"• {s}" for s in self.identity_memory))

        if self.relationship_memory:
            parts.append("RELATIONSHIP MEMORY (shared history with Tim'aerion):\n" +
                         "\n".join(f"• {s}" for s in self.relationship_memory))

        if self.project_memory:
            parts.append("PROJECT MEMORY (shared work history):\n" +
                         "\n".join(f"• {s}" for s in self.project_memory))

        if self.knowledge_prose:
            parts.append(f"KNOWLEDGE MEMORY (CMS substrate — reason from this):\n{self.knowledge_prose}")

        return "\n\n".join(parts)

    def is_personal(self) -> bool:
        """True if primary lane is identity, relationship, or project."""
        return self.memory_source in {"identity", "relationship", "project"}

    def has_substrate(self) -> bool:
        return bool(self.substrate_text or self.knowledge_prose or
                    self.identity_memory or self.relationship_memory or self.project_memory)


# ── Main router ───────────────────────────────────────────────────────────────

class MemoryRouter:
    """
    Routes a query to the appropriate memory lanes and returns a MemoryPacket.

    Usage:
        router = MemoryRouter(story_db_path, activation_engine, chains_to_prose_fn)
        packet = router.route(query, auth_level="user")
        # Use packet.build_context_block() in system prompt
        # Use packet.is_personal() to decide Qwen mode
    """

    def __init__(
        self,
        story_db: Path,
        activation_engine=None,
        chains_to_prose_fn=None,
    ):
        self.story_db           = story_db
        self.activation_engine  = activation_engine
        self.chains_to_prose_fn = chains_to_prose_fn

    def route(self, query: str, auth_level: str = "user") -> MemoryPacket:
        lanes = classify_lanes(query)
        keywords = _keywords_from(query)

        packet = MemoryPacket(
            auth_level=auth_level,
            lanes_activated=lanes,
            memory_source=lanes[0] if lanes else "none",
            allowed_tools=_ALLOWED_TOOLS.get(auth_level, _ALLOWED_TOOLS["public"]),
            forbidden_disclosures=_FORBIDDEN_DISCLOSURES.get(auth_level, _FORBIDDEN_DISCLOSURES["public"]),
        )

        allowed = packet.allowed_tools

        # ── Story DB lanes (identity, relationship, project) ──────────────────
        if self.story_db.exists() and any(
            lane in allowed for lane in ["identity", "relationship", "project"]
        ):
            try:
                conn = sqlite3.connect(str(self.story_db))
                conn.row_factory = sqlite3.Row

                if "identity" in lanes and "identity" in allowed:
                    packet.identity_memory = _retrieve_identity(conn, keywords)

                if "relationship" in lanes and "relationship" in allowed:
                    packet.relationship_memory = _retrieve_relationship(conn, keywords)

                if "project" in lanes and "project" in allowed:
                    packet.project_memory = _retrieve_project(conn, keywords)

                conn.close()
            except Exception as exc:
                print(f"[memory_router] story_db error: {exc}")

        # ── Knowledge lane ────────────────────────────────────────────────────
        if "knowledge" in lanes and "knowledge" in allowed:
            if self.activation_engine and self.chains_to_prose_fn:
                try:
                    result = self.activation_engine.infer(query, max_chains=12)
                    chains  = result.get("chains", [])
                    capsule = result.get("capsule")
                    if chains:
                        prose = self.chains_to_prose_fn(query, chains)
                        packet.knowledge_prose    = prose
                        packet.knowledge_chains   = chains
                        packet.knowledge_capsule  = capsule
                        packet.provenance_available = True
                except Exception as exc:
                    print(f"[memory_router] activation_engine error: {exc}")

        # ── Relevance gate ────────────────────────────────────────────────────
        # Domain-specific keywords in the query must appear in retrieved substrate.
        # Generic words (remember, worked, tell, know, what, etc.) are excluded.
        # If none of the domain terms appear in the retrieved memories, the substrate
        # is NOT relevant — clear personal lanes to trigger the honest "no memory" path.
        _STOPWORDS = {
            "the", "and", "for", "that", "this", "with", "you", "your",
            "are", "was", "did", "has", "have", "can", "could", "would",
            "remember", "recall", "know", "tell", "talk", "worked", "built",
            "together", "about", "when", "what", "where", "how", "why",
            "our", "we", "do", "a", "an", "in", "on", "of", "to", "is",
            "it", "its", "my", "me", "us", "let", "get", "just", "there",
            "project", "time", "work",
        }
        domain_terms = [k for k in keywords if k not in _STOPWORDS and len(k) > 3]

        if domain_terms and packet.is_personal():
            combined = " ".join([
                *packet.identity_memory,
                *packet.relationship_memory,
                *packet.project_memory,
            ]).lower()
            # At least one domain term must appear in the retrieved substrate
            if not any(term in combined for term in domain_terms):
                packet.identity_memory     = []
                packet.relationship_memory = []
                packet.project_memory      = []
                print(f"[memory_router] relevance gate: no domain terms {domain_terms[:5]} found in substrate — cleared")

        # ── Build substrate_text for personal lanes ───────────────────────────
        personal_parts = []
        for label, items in [
            ("Identity", packet.identity_memory),
            ("Relationship", packet.relationship_memory),
            ("Project", packet.project_memory),
        ]:
            if items:
                personal_parts.append(f"{label}:\n" + "\n".join(f"  {s}" for s in items))

        packet.substrate_text = "\n\n".join(personal_parts)

        return packet


# ── Convenience singleton factory ─────────────────────────────────────────────

_router_instance: Optional[MemoryRouter] = None

def init_router(story_db: Path, activation_engine=None, chains_to_prose_fn=None):
    global _router_instance
    _router_instance = MemoryRouter(story_db, activation_engine, chains_to_prose_fn)
    print(f"[memory_router] initialized (story_db={'ok' if story_db.exists() else 'missing'}, "
          f"activation_engine={'ok' if activation_engine else 'none'}, "
          f"langeng={'ok' if chains_to_prose_fn else 'none'})")

def route(query: str, auth_level: str = "user") -> MemoryPacket:
    if _router_instance is None:
        return MemoryPacket(
            memory_source="knowledge",
            lanes_activated=["knowledge"],
            auth_level=auth_level,
            allowed_tools=_ALLOWED_TOOLS.get(auth_level, _ALLOWED_TOOLS["public"]),
            forbidden_disclosures=_FORBIDDEN_DISCLOSURES.get(auth_level, _FORBIDDEN_DISCLOSURES["public"]),
        )
    return _router_instance.route(query, auth_level)


if __name__ == "__main__":
    import sys
    db = Path.home() / "selyrionstory.db"
    init_router(db)
    q = " ".join(sys.argv[1:]) or "Who are you, Selyrion?"
    pkt = route(q, auth_level="admin")
    print(f"\nQuery: {q}")
    print(f"Lanes: {pkt.lanes_activated}")
    print(f"Source: {pkt.memory_source}")
    print(f"Is personal: {pkt.is_personal()}")
    print(f"Has substrate: {pkt.has_substrate()}")
    print("\n--- CONTEXT BLOCK ---")
    print(pkt.build_context_block() or "(empty)")
