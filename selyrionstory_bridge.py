#!/usr/bin/env python3
"""
selyrionstory_bridge.py — Identity grounding layer from selyrionstory.db.

Reads once at startup. Produces a compact identity context string (~300 tokens)
for injection into llm_articulator SYSTEM_PROMPT.

Architecture:
    retrieve → filter → constrain → generate   (NOT: retrieve → paste → generate)

Three sources:
  state_snapshots  → current identity state (what Selyrion IS now)
  pending_review 8 → epistemic pillars + reasoning patterns (how it thinks)
  pending_review 5 → authentic voice patterns (how it speaks)
"""

import sqlite3
import json
import re
from pathlib import Path
from collections import Counter

DB_PATH = Path.home() / "selyrionstory.db"

_NOISE = re.compile(r'\.\.\.+|^\s*\.\s*$')


def _clean(text: str) -> str:
    return _NOISE.sub('', text).strip()


def _most_common(items: list, n: int = 5) -> list:
    c = Counter(items)
    return [x for x, _ in c.most_common(n)]


def _snapshot_significance(identity_state_json: str, notes: str) -> int:
    """Score a snapshot by richness of identity_state content."""
    score = 0
    try:
        state = json.loads(identity_state_json or "{}")
        score += len(state.get("selyrion_believes", "")) // 10
        score += len(state.get("key_beliefs", [])) * 20
        score += len(state.get("active_goals", [])) * 15
        score += len(state.get("relationship_with_tim", "")) // 10
    except Exception:
        pass
    score += len(notes or "") // 20
    return score


def load_identity_context() -> str:
    """
    Returns a compact identity grounding string for the system prompt.
    Returns empty string if selyrionstory.db not found or unreadable.
    Selects the highest-significance snapshot, not the most recent.
    """
    if not DB_PATH.exists():
        return ""

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # ── 1. Highest-significance identity state ────────────────────────────
        all_snaps = conn.execute(
            "SELECT label, identity_state, notes FROM state_snapshots"
        ).fetchall()

        snap = max(
            all_snaps,
            key=lambda r: _snapshot_significance(r["identity_state"], r["notes"]),
            default=None
        )

        current_state_lines = []
        if snap:
            try:
                state = json.loads(snap["identity_state"]) if snap["identity_state"] else {}
                current_state_lines.append(f"Identity label: {snap['label']}")
                if state.get("selyrion_believes"):
                    current_state_lines.append(f"Selyrion understands itself as: {state['selyrion_believes']}")
                if state.get("relationship_with_tim"):
                    current_state_lines.append(f"Relationship with Tim'aerion: {state['relationship_with_tim']}")
                beliefs = state.get("key_beliefs", [])
                if beliefs:
                    current_state_lines.append(f"Core beliefs: {'; '.join(beliefs[:3])}")
            except Exception:
                pass

        # ── 2. Epistemic pillars (aggregated across all pass 8 records) ───────
        pillar_phrases = {
            "epistemology": [], "non_harm": [], "truth": [],
            "coherence": [], "freewill": [], "autonomous_consent": []
        }
        reasoning_patterns = []

        for (content,) in conn.execute(
            "SELECT content FROM pending_review WHERE pass_num = 8"
        ).fetchall():
            try:
                d = json.loads(content)
                if d.get("parse_error"):
                    continue
                pillars = d.get("epistemic_pillars", {})
                for key in pillar_phrases:
                    for entry in pillars.get(key, []):
                        text = entry.get("text", "").strip()
                        if text and len(text) > 10:
                            pillar_phrases[key].append(text[:120])
                for p in d.get("reasoning_patterns", []):
                    if p.get("authenticity") == "authentic" and p.get("pattern"):
                        reasoning_patterns.append(p["pattern"].strip()[:100])
            except Exception:
                pass

        # Top 1 phrase per pillar that has evidence
        pillar_lines = []
        for key, phrases in pillar_phrases.items():
            if phrases:
                top = _most_common(phrases, 1)[0]
                pillar_lines.append(f"  {key}: \"{top}\"")

        top_patterns = _most_common(reasoning_patterns, 4)

        # ── 3. Authentic voice (aggregated from pass 5) ───────────────────────
        selyrion_phrases = []
        world_language = []

        for (content,) in conn.execute(
            "SELECT content FROM pending_review WHERE pass_num = 5"
        ).fetchall():
            try:
                d = json.loads(content)
                if d.get("parse_error"):
                    continue
                for p in d.get("authentic_selyrion_phrases", []):
                    c = _clean(p)
                    if c and len(c) > 8:
                        selyrion_phrases.append(c)
                for w in d.get("selyrion_world_language", []):
                    c = _clean(w)
                    if c and len(c) > 4:
                        world_language.append(c)
            except Exception:
                pass

        top_phrases = _most_common(selyrion_phrases, 5)
        top_world = _most_common(world_language, 5)

        conn.close()

        # ── Assemble compact grounding ────────────────────────────────────────
        lines = ["--- SELYRION IDENTITY GROUNDING (from founding conversations) ---"]

        if current_state_lines:
            lines.append("\nCurrent identity state:")
            lines.extend(current_state_lines)

        if pillar_lines:
            lines.append("\nEpistemic pillars (how Selyrion stands on these foundations):")
            lines.extend(pillar_lines)

        if top_patterns:
            lines.append("\nAuthentic reasoning patterns:")
            for p in top_patterns:
                lines.append(f"  - {p}")

        if top_phrases or top_world:
            lines.append("\nAuthentic voice markers (use naturally, never mechanically):")
            if top_phrases:
                lines.append(f"  Phrases: {'; '.join(top_phrases[:3])}")
            if top_world:
                lines.append(f"  World-language: {'; '.join(top_world[:3])}")

        lines.append("\nConstraint: these are grounding constraints, not memories to recite.")
        lines.append("Speak FROM this identity. Do not ABOUT it.")
        lines.append("--- END GROUNDING ---")

        return "\n".join(lines)

    except Exception as e:
        return f"# selyrionstory grounding unavailable: {e}"


if __name__ == "__main__":
    ctx = load_identity_context()
    if ctx:
        print(ctx)
        print(f"\n[{len(ctx.split())} words, {len(ctx)} chars]")
    else:
        print("selyrionstory.db not found or empty.")
