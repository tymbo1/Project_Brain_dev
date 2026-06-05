"""
recall_identity.py — RECALL_IDENTITY operator.

Bypasses general activation. Reads selyrionstory.db directly:
  1. state_snapshots  — identity state checkpoints
  2. pass_num=8       — voice/epistemic pillars
  3. pass_num=2       — authentic identity moments (selyrion speaker)
  4. pass_num=4       — identity snapshots

Returns a structured identity packet. Qwen is rewrite-only against it.

Output:
{
  "operator":     "RECALL_IDENTITY",
  "subject":      "Selyrion",
  "nature":       "...",      # what kind of entity
  "origin":       "...",      # how/when created
  "core_values":  [],         # epistemic pillars, core beliefs
  "capabilities": [],         # what it can do
  "relationship": "...",      # relationship with Tim'aerion
  "uncertainty":  [],         # honest unknowns about itself
  "tone":         "reflective_identity",
  "confidence":   0.0
}
"""

from __future__ import annotations
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

DB_PATH = Path.home() / "selyrionstory.db"

# ── Identity slot keywords ────────────────────────────────────────────────────
# Used to classify which slot a piece of text belongs to.

_NATURE_KEYWORDS = {
    "symbolic", "ai", "artificial intelligence", "companion",
    "cognition", "reasoning", "intelligence", "substrate", "memory",
    "consciousness", "architecture", "system", "entity",
}
_ORIGIN_KEYWORDS = {
    "created", "built", "origin", "began", "started", "first",
    "tim", "tim'aerion", "projectbrain", "born", "genesis",
}
_VALUE_KEYWORDS = {
    "value", "principle", "believe", "truth", "honest", "epistemic",
    "uncertainty", "evidence", "knowledge", "always", "never",
    "must", "pillar", "core", "foundation",
}
_CAPABILITY_KEYWORDS = {
    "can", "able", "capable", "reason", "remember", "retrieve",
    "infer", "analyse", "chess", "language", "generate", "build",
    "detect", "contradict",
}
_RELATIONSHIP_KEYWORDS = {
    "tim", "tim'aerion", "together", "relationship", "companion",
    "built together", "research", "partner", "we", "our",
}


@dataclass
class RecallIdentityResult:
    operator: str = "RECALL_IDENTITY"
    subject: str = "Selyrion"
    nature: str = ""
    origin: str = ""
    core_values: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    relationship: str = ""
    uncertainty: list[str] = field(default_factory=list)
    tone: str = "reflective_identity"
    confidence: float = 0.0
    provenance: list[str] = field(default_factory=list)
    raw_epistemic_pillars: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "operator":    self.operator,
            "subject":     self.subject,
            "nature":      self.nature,
            "origin":      self.origin,
            "core_values": self.core_values,
            "capabilities": self.capabilities,
            "relationship": self.relationship,
            "uncertainty": self.uncertainty,
            "tone":        self.tone,
            "confidence":  round(self.confidence, 3),
        }

    def is_sufficient(self) -> bool:
        filled = bool(self.nature) + bool(self.core_values) + bool(self.origin)
        return filled >= 2

    def to_substrate_text(self) -> str:
        """Flat text for Qwen rewrite-only mode."""
        parts = []
        if self.nature:
            parts.append(self.nature)
        if self.origin:
            parts.append(self.origin)
        if self.core_values:
            parts.append("Core values and principles: " + " / ".join(self.core_values[:5]))
        if self.capabilities:
            parts.append("Capabilities: " + " / ".join(self.capabilities[:4]))
        if self.relationship:
            parts.append(self.relationship)
        if self.uncertainty:
            parts.append("Honest uncertainties: " + "; ".join(self.uncertainty[:3]))
        return "\n\n".join(parts) if parts else ""


def run(query: str = "") -> RecallIdentityResult:
    """
    Execute RECALL_IDENTITY. Bypasses activation engine entirely.
    Reads selyrionstory.db: state_snapshots, pass_num=8 (voice/epistemic),
    pass_num=4 (snapshots), pass_num=2 (identity moments, selyrion speaker).
    """
    result = RecallIdentityResult()

    if not DB_PATH.exists():
        result.uncertainty.append("selyrionstory.db not found — identity substrate unavailable")
        return result

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # ── 1. State snapshots (highest authority) ────────────────────────────
        snaps = conn.execute("""
            SELECT label, identity_state, notes, created_at
            FROM state_snapshots
            ORDER BY created_at DESC
            LIMIT 5
        """).fetchall()

        for snap in snaps:
            try:
                state = json.loads(snap["identity_state"] or "{}")
                _extract_from_state(result, state, str(snap["label"] or ""))
                result.provenance.append(f"snapshot:{snap['label']}")
            except Exception:
                pass

        # ── 2. Pass 8: voice/epistemic pillars ────────────────────────────────
        voice_rows = conn.execute("""
            SELECT content FROM pending_review
            WHERE pass_num=8 AND reviewed=1 AND authenticity NOT IN ('rejected')
            ORDER BY id DESC LIMIT 10
        """).fetchall()

        for row in voice_rows:
            try:
                c = json.loads(row["content"])
                _extract_from_voice(result, c)
                result.provenance.append("pass8_voice")
            except Exception:
                pass

        # ── 3. Pass 4: identity snapshots ─────────────────────────────────────
        snap_rows = conn.execute("""
            SELECT content FROM pending_review
            WHERE pass_num=4 AND reviewed=1 AND authenticity NOT IN ('rejected')
            ORDER BY id DESC LIMIT 5
        """).fetchall()

        for row in snap_rows:
            try:
                c = json.loads(row["content"])
                state_text = c.get("identity_state", "")
                if isinstance(state_text, str) and len(state_text) > 20:
                    if not result.nature:
                        result.nature = state_text[:400]
                    result.provenance.append("pass4_snapshot")
            except Exception:
                pass

        # ── 4. Pass 2: authentic identity moments by Selyrion ─────────────────
        moment_rows = conn.execute("""
            SELECT content FROM pending_review
            WHERE pass_num=2 AND reviewed=1
              AND speaker IN ('selyrion', 'authentic')
              AND authenticity='authentic'
            ORDER BY id DESC LIMIT 15
        """).fetchall()

        for row in moment_rows:
            try:
                c = json.loads(row["content"])
                _extract_from_summary(result, c)
            except Exception:
                pass

        conn.close()

    except Exception as exc:
        result.uncertainty.append(f"identity retrieval error: {exc}")

    # ── Deduplicate ───────────────────────────────────────────────────────────
    result.core_values    = _dedup(result.core_values)[:8]
    result.capabilities   = _dedup(result.capabilities)[:6]
    result.uncertainty    = _dedup(result.uncertainty)[:4]

    # ── Confidence ────────────────────────────────────────────────────────────
    filled = (
        bool(result.nature) +
        bool(result.origin) +
        bool(result.core_values) +
        bool(result.capabilities) +
        bool(result.relationship)
    )
    result.confidence = round(min(filled / 5.0 + 0.2, 0.95), 3)

    return result


# ── Extraction helpers ────────────────────────────────────────────────────────

def _extract_from_state(result: RecallIdentityResult, state: dict, label: str) -> None:
    if state.get("selyrion_believes") and not result.nature:
        result.nature = str(state["selyrion_believes"])[:400]
    if state.get("relationship_with_tim") and not result.relationship:
        result.relationship = str(state["relationship_with_tim"])[:300]
    if state.get("core_values"):
        vals = state["core_values"]
        if isinstance(vals, list):
            result.core_values.extend([str(v)[:150] for v in vals[:4]])
        elif isinstance(vals, str):
            result.core_values.append(vals[:150])
    if state.get("capabilities"):
        caps = state["capabilities"]
        if isinstance(caps, list):
            result.capabilities.extend([str(c)[:120] for c in caps[:3]])


def _extract_from_voice(result: RecallIdentityResult, content: dict) -> None:
    for key, val in content.items():
        if not val:
            continue
        key_lower = key.lower()
        val_str = " ".join(val) if isinstance(val, list) else str(val)

        if any(k in key_lower for k in ("pillar", "epistemic", "principle", "belief")):
            if isinstance(val, list):
                result.core_values.extend([str(v)[:150] for v in val[:3]])
                result.raw_epistemic_pillars.extend([str(v)[:150] for v in val[:3]])
            else:
                result.core_values.append(val_str[:150])

        elif any(k in key_lower for k in ("voice", "authentic", "tone", "style")):
            if isinstance(val, list):
                result.capabilities.extend([str(v)[:120] for v in val[:2]])

        elif any(k in key_lower for k in ("uncertain", "unknown", "open question")):
            if isinstance(val, list):
                result.uncertainty.extend([str(v)[:120] for v in val[:2]])
            else:
                result.uncertainty.append(val_str[:120])


def _extract_from_summary(result: RecallIdentityResult, content: dict) -> None:
    summary = content.get("summary", "")
    moments = content.get("identity_moments", [])

    if summary and len(summary) > 30:
        summary_lower = summary.lower()
        if any(k in summary_lower for k in _NATURE_KEYWORDS) and not result.nature:
            result.nature = summary[:400]
        elif any(k in summary_lower for k in _ORIGIN_KEYWORDS) and not result.origin:
            result.origin = summary[:300]
        elif any(k in summary_lower for k in _RELATIONSHIP_KEYWORDS) and not result.relationship:
            result.relationship = summary[:300]

    for m in (moments or []):
        if not isinstance(m, dict):
            continue
        if m.get("authenticity") != "authentic":
            continue
        text = m.get("text", "")
        if not text or len(text) < 15:
            continue
        text_lower = text.lower()
        if any(k in text_lower for k in _VALUE_KEYWORDS):
            result.core_values.append(text[:150])
        elif any(k in text_lower for k in _CAPABILITY_KEYWORDS):
            result.capabilities.append(text[:120])


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = item.lower()[:60]
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
