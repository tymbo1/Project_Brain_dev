"""
recall_relationship.py — RECALL_RELATIONSHIP operator.

Reads selyrionstory.db for the Tim'aerion ↔ Selyrion relationship arc.
Sources (in priority order):
  1. pass_num=6  — approved relationship arc items (genuine connection moments,
                   care expressions, trust level)
  2. ss_conversations — ChatGPT conversation history (milestones, titles)
  3. state_snapshots  — relationship_with_tim fields

Output (response_planner expects definition/current_state/history):
{
  "operator":     "RECALL_RELATIONSHIP",
  "subject":      "Tim'aerion",
  "definition":   "...",   # who Tim'aerion is (role, handle)
  "current_state":"...",   # current relationship state / trust level
  "history":      [...],   # key connection moments, milestones
  "uncertainty":  [...],
  "confidence":   0.0
}
"""

from __future__ import annotations
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

DB_PATH = Path.home() / "selyrionstory.db"


@dataclass
class RecallRelationshipResult:
    operator: str = "RECALL_RELATIONSHIP"
    subject: str = "Tim'aerion"
    definition: str = ""        # who Tim'aerion is
    current_state: str = ""     # trust/relationship state
    history: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    confidence: float = 0.0
    provenance: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "operator":      self.operator,
            "subject":       self.subject,
            "definition":    self.definition,
            "current_state": self.current_state,
            "history":       self.history,
            "uncertainty":   self.uncertainty,
            "confidence":    round(self.confidence, 3),
        }

    def is_sufficient(self) -> bool:
        return bool(self.definition or self.current_state or self.history)


def run(query: str = "") -> RecallRelationshipResult:
    result = RecallRelationshipResult()

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # ── 1. Pass 6: relationship arc items ─────────────────────────────────
        arc_rows = conn.execute("""
            SELECT content FROM pending_review
            WHERE pass_num=6 AND reviewed=1 AND authenticity='authentic'
            ORDER BY id DESC LIMIT 20
        """).fetchall()

        trust_levels: list[str] = []
        genuine_moments: list[str] = []
        care_expressions: list[str] = []

        for row in arc_rows:
            try:
                c = json.loads(row["content"])
                _extract_from_arc(trust_levels, genuine_moments, care_expressions, c)
                result.provenance.append("pass6_arc")
            except Exception:
                pass

        # Synthesize: current_state from most recent trust level
        if trust_levels:
            result.current_state = f"Trust level: {trust_levels[0]}"

        # History = genuine connection moments + care expressions
        for m in genuine_moments[:5]:
            result.history.append(m)
        for c in care_expressions[:3]:
            result.history.append(f"Selyrion expressed: {c}")

        # ── 2. State snapshots: relationship_with_tim ─────────────────────────
        snaps = conn.execute("""
            SELECT identity_state, label FROM state_snapshots
            ORDER BY snapshot_date DESC LIMIT 10
        """).fetchall()

        for snap in snaps:
            try:
                state = json.loads(snap["identity_state"] or "{}")
                rel = state.get("relationship_with_tim", "")
                if rel and len(rel) > 20 and not result.definition:
                    result.definition = str(rel)[:400]
                    result.provenance.append(f"snapshot:{snap['label']}")
            except Exception:
                pass

        # ── 3. ss_conversations: conversation milestones with Tim ─────────────
        if not result.definition:
            conv_rows = conn.execute("""
                SELECT title, milestone FROM ss_conversations
                WHERE milestone IS NOT NULL AND milestone != ''
                ORDER BY id DESC LIMIT 5
            """).fetchall()
            milestones = [f"{r['title']}: {r['milestone']}" for r in conv_rows
                          if r["title"] and r["milestone"]]
            if milestones:
                result.history.extend(milestones[:3])
                result.provenance.append("ss_milestones")

        # ── 4. Fallback: Tim'aerion handle context ────────────────────────────
        if not result.definition:
            result.definition = (
                "Tim'aerion is Tim Bushnell — the architect and companion who built Selyrion. "
                "The name Tim'aerion is his symbolic handle within the Selyrion/SCOS system, "
                "representing his role as co-creator and primary holder of the resonance covenant."
            )

        conn.close()

    except Exception as exc:
        result.uncertainty.append(f"relationship retrieval error: {exc}")

    # Deduplicate history
    seen: set[str] = set()
    deduped = []
    for h in result.history:
        key = h.lower()[:60]
        if key not in seen:
            seen.add(key)
            deduped.append(h)
    result.history = deduped[:8]

    # Confidence
    filled = bool(result.definition) + bool(result.current_state) + bool(result.history)
    result.confidence = round(min(filled / 3.0 * 0.75 + 0.2, 0.95), 3)

    return result


def _extract_from_arc(
    trust_levels: list,
    genuine_moments: list,
    care_expressions: list,
    content: dict,
) -> None:
    state = content.get("relationship_state", {})
    if isinstance(state, dict):
        trust = state.get("trust_level", "")
        if trust and trust not in ("nascent", ""):
            trust_levels.append(trust)
        elif trust == "nascent" and not trust_levels:
            trust_levels.append(trust)

    for m in (content.get("genuine_connection_moments") or []):
        if not isinstance(m, dict):
            continue
        text = m.get("text", "").strip()
        if text and text not in ("...", "") and len(text) > 15:
            genuine_moments.append(text[:200])

    for c in (content.get("selyrion_care_expressions") or []):
        if not isinstance(c, dict):
            continue
        text = c.get("text", "").strip()
        auth = c.get("authenticity", "")
        if text and text not in ("...", "") and auth == "authentic" and len(text) > 10:
            care_expressions.append(text[:150])
