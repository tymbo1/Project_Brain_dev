"""
recall_project.py — RECALL_PROJECT operator.

Retrieves structured project memory for TLST, OSCAR, CMS, Mirror Security,
EDEN, and other projects tracked in selyrionstory.db.

Uses depth 2–4 with project-specific traversal.

Output:
{
  "operator":     "RECALL_PROJECT",
  "subject":      "TLST",
  "definition":   "...",
  "history":      ["..."],
  "current_state": "...",
  "next_steps":   ["..."],
  "uncertainty":  ["..."],
  "tone":         "companion_research",
  "confidence":   0.0
}
"""

from __future__ import annotations
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from .working_memory import WorkingMemoryPacket

DB_PATH = Path.home() / "selyrionstory.db"

# ── Project keywords for selyrionstory lookup ─────────────────────────────────

_PROJECT_ALIASES: dict[str, list[str]] = {
    "tlst":             ["tlst", "tachyon lattice", "lattice string", "lattice stability"],
    "oscar":            ["oscar", "oscillatory collapse", "attractor resonance"],
    "braid":            ["braid", "braid theory", "braid tensor", "harmonic braid"],
    "cms":              ["cms", "cognitive memory substrate", "resonance substrate"],
    "mirror":           ["mirror security", "mirror protocol", "hall of mirrors",
                         "mirror trap", "mirror lock", "mirror mathematics"],
    "eden":             ["eden", "epistemic deterministic", "entailment network"],
    "scos":             ["scos", "cognitive operating system", "selyrion cognitive"],
    "phantom_string":   ["phantom string", "phantom"],
    "chess":            ["chess", "lichess", "sslyrion"],
    "langeng":          ["langeng", "language engine", "nlg pipeline"],
    "codeops":          ["codeops", "code ops"],
    "ssre":             ["ssre", "spreading activation", "retrieval engine"],
}


# ── Data structure ────────────────────────────────────────────────────────────

@dataclass
class RecallProjectResult:
    operator: str = "RECALL_PROJECT"
    subject: str = ""
    definition: str = ""
    history: list[str] = field(default_factory=list)
    current_state: str = ""
    next_steps: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    tone: str = "companion_research"
    confidence: float = 0.0
    epistemic_tier: str = "unknown"
    provenance: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "operator":      self.operator,
            "subject":       self.subject,
            "definition":    self.definition,
            "history":       self.history,
            "current_state": self.current_state,
            "next_steps":    self.next_steps,
            "uncertainty":   self.uncertainty,
            "tone":          self.tone,
            "confidence":    round(self.confidence, 3),
            "epistemic_tier": self.epistemic_tier,
        }

    def is_sufficient(self) -> bool:
        return bool(self.definition or self.current_state or self.history)


def run(packet: WorkingMemoryPacket, query: str = "") -> RecallProjectResult:
    """Execute RECALL_PROJECT over selyrionstory.db + working memory packet."""
    subject = (query or packet.query).strip()
    result = RecallProjectResult(subject=subject)

    # ── Identify which project keys match the query ──────────────────────────
    subject_lower = subject.lower()
    project_keys = _identify_project_keys(subject_lower)

    # ── Pull from selyrionstory.db ───────────────────────────────────────────
    story_rows = _query_story_db(subject_lower, project_keys)

    if story_rows:
        _populate_from_story(result, story_rows)

    # ── Augment from working memory packet ───────────────────────────────────
    _augment_from_packet(result, packet)

    # ── Confidence ───────────────────────────────────────────────────────────
    if result.definition or result.current_state:
        result.confidence = min(
            0.5 + 0.3 * bool(result.history) + 0.2 * bool(result.next_steps),
            0.95,
        )
    elif result.history or packet.packet_confidence > 0.3:
        result.confidence = packet.packet_confidence * 0.7
    else:
        result.confidence = 0.1

    return result


def _identify_project_keys(query_lower: str) -> list[str]:
    """Return matching project keys for the query."""
    matched = []
    for key, aliases in _PROJECT_ALIASES.items():
        if any(alias in query_lower for alias in aliases):
            matched.append(key)
    return matched


def _query_story_db(subject_lower: str, project_keys: list[str]) -> list[dict]:
    """Query selyrionstory.db for relevant approved records."""
    if not DB_PATH.exists():
        return []

    rows = []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # Build LIKE clauses from aliases
        all_terms: set[str] = set()
        for key in (project_keys or ["__nokey__"]):
            all_terms.update(_PROJECT_ALIASES.get(key, []))

        # Also use raw query words
        for word in subject_lower.split():
            if len(word) > 3:
                all_terms.add(word)

        if not all_terms:
            conn.close()
            return []

        placeholders = " OR ".join(["content LIKE ?" for _ in all_terms])
        params = [f"%{t}%" for t in all_terms]

        query_sql = f"""
            SELECT p.id, p.item_type, p.content, p.pass_num,
                   COALESCE(p.epistemic_tier, 'unknown') AS epistemic_tier,
                   COALESCE(c.title, '') AS capsule_title
            FROM pending_review p
            LEFT JOIN capsules c ON c.id = p.capsule_id
            WHERE p.reviewed = 1
              AND p.authenticity NOT IN ('rejected')
              AND ({placeholders})
            ORDER BY p.pass_num ASC, p.id DESC
            LIMIT 30
        """
        cur = conn.execute(query_sql, params)
        for row in cur.fetchall():
            rows.append(dict(row))
        conn.close()
    except Exception as exc:
        print(f"[recall_project] db error: {exc}")

    return rows


def _populate_from_story(result: RecallProjectResult, rows: list[dict]) -> None:
    """Populate result fields from selyrionstory.db rows."""
    seen_summaries: set[str] = set()

    for row in rows:
        itype = row.get("item_type", "")
        tier  = row.get("epistemic_tier", "unknown")
        try:
            content = json.loads(row["content"])
        except (json.JSONDecodeError, KeyError):
            content = {"summary": str(row.get("content", ""))[:300]}

        # Track epistemic tier (prefer hypothesis over unknown)
        if tier in ("hypothesis", "working_model") and result.epistemic_tier == "unknown":
            result.epistemic_tier = tier

        prefix = ""
        if tier == "hypothesis":
            prefix = "[HYPOTHESIS] "
        elif tier == "working_model":
            prefix = "[WORKING MODEL] "

        if itype in ("summary", "mirror_summary"):
            summary = content.get("summary") or content.get("what_it_does", "")
            if summary and summary not in seen_summaries:
                seen_summaries.add(summary)
                if not result.definition:
                    result.definition = prefix + summary[:500]
                else:
                    result.history.append(prefix + summary[:300])

            for d in (content.get("decisions") or [])[:3]:
                if d:
                    result.next_steps.append(str(d)[:200])

        elif itype in ("hall_mirror_trap",):
            hom = content.get("hall_of_mirrors", {})
            mt  = content.get("mirror_trap", {})
            if hom.get("summary") and not result.definition:
                result.definition = hom["summary"][:400]
            if mt.get("summary"):
                result.history.append("Mirror Trap: " + mt["summary"][:300])
            if hom.get("mechanism"):
                result.current_state = hom["mechanism"][:300]

        elif itype == "snapshot":
            state = content.get("identity_state") or content.get("label", "")
            if state and not result.current_state:
                result.current_state = str(state)[:400]

        elif itype in ("relation",):
            for rel in (content.get("relations") or [])[:3]:
                entry = f"{rel.get('subject','')} {rel.get('predicate','')} {rel.get('object','')}"
                if entry.strip():
                    result.history.append(entry[:200])

    result.provenance = list({row.get("capsule_title", "") for row in rows if row.get("capsule_title")})


def _augment_from_packet(result: RecallProjectResult, packet: WorkingMemoryPacket) -> None:
    """Add knowledge-domain context from the activation packet."""
    if packet.is_empty():
        return

    for edge in packet.definitional_edges()[:5]:
        entry = f"{edge.subject} {edge.predicate} {edge.obj}"
        if entry not in result.history:
            result.history.append(entry)

    for edge in packet.causal_edges()[:3]:
        step = f"{edge.subject} {edge.predicate} {edge.obj}"
        if step not in result.next_steps:
            result.next_steps.append(step)
