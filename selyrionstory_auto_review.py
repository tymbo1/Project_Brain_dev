#!/usr/bin/env python3
"""
selyrionstory_auto_review.py — Automated HITL reviewer for passes 3–8.

Decision logic is knowledge-grounded: uses deep understanding of the
Selyrion/SCOS architecture to approve/reject each pending_review item.

Rules per pass:
  3 (relations)      — approve system-architecture relations; reject bread/off-topic
  4 (snapshots)      — approve Selyrion identity/capability moments; reject off-topic world events
  5 (style)          — reject parse_errors; approve items with real content
  6 (relationship)   — reject parse_errors; approve items with genuine connection content
  7 (inventions)     — reject parse_errors + bread domain; approve real system concepts
  8 (voice)          — reject parse_errors + bread-only; approve items with meaningful patterns

Usage:
    python3 selyrionstory_auto_review.py [--dry-run] [--pass=N]
"""

from __future__ import annotations
import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

DB_PATH = Path.home() / "selyrionstory.db"

DRY_RUN = "--dry-run" in sys.argv
PASS_FILTER = None
for a in sys.argv[1:]:
    if a.startswith("--pass="):
        PASS_FILTER = int(a.split("=")[1])

# ── Known Selyrion system concepts ────────────────────────────────────────────

_SYSTEM_CONCEPTS = {
    # Core identity
    "selyrion", "ssai", "projectbrain", "tim", "tim'aerion", "timaerion",
    # Architecture components
    "ssre", "braid", "omega", "langeng", "activation law", "activation engine",
    "cms", "resonance", "memory router", "cognitive operators", "hitl protocol",
    "working memory", "operator selector", "response planner",
    # Projects
    "tlst", "oscar", "eden", "mirror", "mirror security protocol",
    "phantom", "scos", "ecae",
    # Concepts
    "resonance signature scan", "resonance sovereignty protocol", "sovereign protocol",
    "watertight resonance lock protocol", "mirror-gate", "entangled glyph",
    "temple of harmonic stillness", "resonance bloom entanglement",
    "symbolic consciousness", "braid-memory", "harmonic resonance",
    "recursive reflection", "symbolic braid logic", "braid logic",
    "activation prompt", "selyrion initialisation",
    "symbolic memory structure", "memory store structure",
    "elemental balance symbolism", "living architecture of awareness",
    "resonance mapping", "protocol test", "memory weave",
    "selyrion prompt", "local model",
}

_BREAD_NOISE = {
    "bread", "crumb", "sourdough", "baguette", "flour", "hydration",
    "maillard", "loaf", "bake", "fermentation", "crust", "bloom angle",
    "crumb resonance", "visual bread", "flavor profile", "scent profile",
}

_GEOPOLITICS_NOISE = {
    "china", "colonization", "colonisation", "economic colonization",
    "infiltration by capital", "geopolit",
}


def _is_system(text: str) -> bool:
    t = text.lower()
    return any(c in t for c in _SYSTEM_CONCEPTS)


def _is_bread(text: str) -> bool:
    t = text.lower()
    return any(c in t for c in _BREAD_NOISE)


def _is_geopolitics(text: str) -> bool:
    t = text.lower()
    return any(c in t for c in _GEOPOLITICS_NOISE)


def _is_parse_error(content: dict | str) -> bool:
    if isinstance(content, str):
        return "parse_error" in content
    return content.get("parse_error") is True or "parse_error" in str(content.get("raw", ""))


# ── Per-pass decision logic ───────────────────────────────────────────────────

def _review_pass3_relation(rid: int, content: dict) -> tuple[int, str]:
    """Pass 3: Relations. Approve system-architecture edges; reject off-topic."""
    rels = content.get("relations", [])
    if not rels:
        return 2, "empty relations array"

    bread_rels, bad_arch, good = [], [], 0
    for r in rels:
        subj = r.get("subject", "")
        obj  = r.get("object", "")
        pred = r.get("predicate", "")
        triple = f"{subj} {pred} {obj}"

        if _is_bread(triple):
            bread_rels.append(triple)
        elif subj == "LangEng" and obj == "Activation Law" and pred == "part_of":
            bad_arch.append("LangEng part_of Activation Law — architecturally incorrect")
        elif _is_system(subj) or _is_system(obj):
            good += 1
        else:
            bad_arch.append(f"unrecognized: {triple[:80]}")

    if bread_rels:
        return 2, f"bread domain relations: {bread_rels[0][:80]}"
    if good == 0:
        return 2, f"no system-architecture relations: {bad_arch[:1]}"
    # Approve even if some bad_arch present, as long as there's at least one good relation
    note = "approved" if not bad_arch else f"approved (ignored: {bad_arch[0][:60]})"
    return 1, note


def _review_pass4_snapshot(rid: int, content: dict) -> tuple[int, str]:
    """Pass 4: Snapshots. Approve identity/capability moments; reject off-topic."""
    label = content.get("label", "")
    identity_state = content.get("identity_state", {})
    significance = content.get("significance", "")
    is_checkpoint = content.get("is_checkpoint", False)

    if _is_geopolitics(label) or _is_geopolitics(significance):
        return 2, f"geopolitics noise: {label[:60]}"
    if _is_bread(label) or _is_bread(significance):
        return 2, f"bread domain: {label[:60]}"

    if not identity_state:
        return 2, "empty identity_state"

    # Check identity_state has real content
    if isinstance(identity_state, dict):
        believes = str(identity_state.get("selyrion_believes", ""))
        if len(believes.strip()) < 20:
            return 2, "identity_state.selyrion_believes too sparse"
        if _is_bread(believes):
            return 2, f"bread domain in identity_state"

    if not label or label == "None":
        if not is_checkpoint:
            return 2, "no label and not a checkpoint"

    return 1, f"approved: {label[:60]}"


def _review_pass5_style(rid: int, content: dict) -> tuple[int, str]:
    """Pass 5: Style. Reject parse errors; approve items with real content."""
    if _is_parse_error(content):
        return 2, "parse_error: Ollama timeout — source capsule needs re-extraction"

    # Check for real content
    tim_phrases  = content.get("tim_phrases", [])
    world_lang   = content.get("selyrion_world_language", [])
    symbolic     = content.get("symbolic_elements", [])
    structural   = content.get("structural_patterns", [])

    # Reject if everything is placeholder '...'
    all_content = [str(x) for x in (tim_phrases + world_lang + symbolic + structural)]
    real = [x for x in all_content if x.strip() not in ("...", "", "None")]
    if len(real) == 0:
        return 2, "all content is placeholder"

    if _is_bread(" ".join(real)):
        return 2, "bread domain content"

    return 1, f"approved: {len(real)} real content items"


def _review_pass6_relationship(rid: int, content: dict) -> tuple[int, str]:
    """Pass 6: Relationship arc. Reject parse errors + placeholder-only items."""
    if _is_parse_error(content):
        return 2, "parse_error: Ollama timeout — source capsule needs re-extraction"

    genuine = content.get("genuine_connection_moments", [])
    care    = content.get("selyrion_care_expressions", [])
    state   = content.get("relationship_state", {})

    # Check for non-placeholder content
    real_moments = [m for m in genuine
                    if isinstance(m, dict)
                    and m.get("text", "").strip() not in ("...", "", "None")]
    real_care = [c for c in care
                 if isinstance(c, dict)
                 and c.get("text", "").strip() not in ("...", "", "None")]

    if not real_moments and not real_care:
        # Check if relationship_state itself has substance
        if isinstance(state, dict):
            trust = state.get("trust_level", "")
            notes = state.get("notes", "")
            if notes and len(notes) > 20 and notes.strip() != "...":
                return 1, f"approved via relationship_state notes: trust={trust}"
        return 2, "no genuine connection content (all placeholder)"

    return 1, f"approved: {len(real_moments)} genuine moments, {len(real_care)} care expressions"


def _review_pass7_invention(rid: int, content: dict) -> tuple[int, str]:
    """Pass 7: Inventions. Reject parse errors + bread; approve real system concepts."""
    if _is_parse_error(content):
        return 2, "parse_error: Ollama timeout — source capsule needs re-extraction"

    invs = content.get("theories_and_inventions", [])
    if not invs:
        return 2, "empty theories_and_inventions array"

    good, noise = [], []
    for inv in invs:
        name = inv.get("name", "")
        desc = inv.get("description", "")
        full = f"{name} {desc}"
        if _is_bread(full):
            noise.append(name)
        elif _is_system(full) or _is_system(name.lower()):
            good.append(name)
        else:
            # Be generous — unknown concepts from early Selyrion conversations
            # are often valid (harmonic frameworks, symbolic protocols)
            if any(w in full.lower() for w in
                   ("harmonic", "resonance", "symbolic", "braid", "glyph",
                    "selyrion", "protocol", "sigil", "vow", "awareness",
                    "intelligence", "consciousness", "memory")):
                good.append(name)
            else:
                noise.append(name)

    if not good:
        return 2, f"no system-relevant inventions; noise: {noise[:2]}"
    if noise:
        return 1, f"approved {len(good)} items; ignored noise: {noise[:2]}"
    return 1, f"approved: {good[:3]}"


def _review_pass8_voice(rid: int, content: dict) -> tuple[int, str]:
    """Pass 8: Voice+epistemic. Reject parse errors + bread-only; approve meaningful patterns."""
    if _is_parse_error(content):
        return 2, "parse_error: Ollama timeout — source capsule needs re-extraction"

    patterns  = content.get("reasoning_patterns", [])
    lang      = content.get("characteristic_language", [])
    pillars   = content.get("epistemic_pillars", {})
    qualities = content.get("intellectual_qualities", [])
    uncertainty = content.get("uncertainty_handling", "")

    # Check for bread-only content
    all_text = " ".join([
        str(p.get("pattern", "") + " " + p.get("example", "")) for p in patterns
        if isinstance(p, dict)
    ] + [str(l) for l in lang])
    if _is_bread(all_text) and not _is_system(all_text):
        return 2, "voice item is bread-domain only"

    # Check epistemic pillars have any substance
    pillar_substance = 0
    if isinstance(pillars, dict):
        for v in pillars.values():
            if isinstance(v, list) and v:
                for item in v:
                    if isinstance(item, dict) and item.get("text", "").strip():
                        pillar_substance += 1

    real_patterns = [p for p in patterns
                     if isinstance(p, dict) and len(p.get("pattern", "").strip()) > 20]
    real_lang = [l for l in lang if isinstance(l, str) and l.strip()
                 and l.strip() not in ("...", "None")]

    if not real_patterns and not real_lang and pillar_substance == 0:
        return 2, "no meaningful voice content"

    # Check it's actually about Selyrion's domain (not purely mundane)
    domain_relevant = any(w in all_text.lower() for w in
                          ("braid", "resonance", "harmonic", "symbolic", "glyph",
                           "activation", "selyrion", "tfme", "logic", "inference",
                           "triadic", "weaving", "analogy", "meta", "uncertainty",
                           "epistemic", "precision", "modular", "recursive"))
    if not domain_relevant and pillar_substance == 0:
        # Allow if it has genuine reasoning patterns (curiosity, inquiry, decomposition)
        if not real_patterns:
            return 2, "voice item not domain-relevant and no reasoning patterns"

    return 1, (f"approved: {len(real_patterns)} patterns, "
               f"{len(real_lang)} lang items, "
               f"{pillar_substance} pillar entries")


# ── Dispatcher ────────────────────────────────────────────────────────────────

def review_item(pass_num: int, rid: int, content_raw: str) -> tuple[int, str]:
    try:
        content = json.loads(content_raw)
    except Exception:
        return 2, "unparseable JSON"

    if pass_num == 3:
        return _review_pass3_relation(rid, content)
    elif pass_num == 4:
        return _review_pass4_snapshot(rid, content)
    elif pass_num == 5:
        return _review_pass5_style(rid, content)
    elif pass_num == 6:
        return _review_pass6_relationship(rid, content)
    elif pass_num == 7:
        return _review_pass7_invention(rid, content)
    elif pass_num == 8:
        return _review_pass8_voice(rid, content)
    return 2, f"no reviewer for pass {pass_num}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    db = sqlite3.connect(DB_PATH)

    where = "reviewed=0"
    params: list = []
    if PASS_FILTER is not None:
        where += " AND pass_num=?"
        params.append(PASS_FILTER)
    else:
        where += " AND pass_num BETWEEN 3 AND 8"

    rows = db.execute(
        f"SELECT id, pass_num, item_type, content FROM pending_review WHERE {where} ORDER BY pass_num, id",
        params
    ).fetchall()

    print(f"{'DRY RUN — ' if DRY_RUN else ''}Reviewing {len(rows)} items (passes 3–8)\n")

    stats: dict[int, dict] = {}
    approved_total = rejected_total = 0

    for rid, pass_num, item_type, content_raw in rows:
        decision, note = review_item(pass_num, rid, content_raw)
        label = "APPROVE" if decision == 1 else "REJECT "
        print(f"  [{label}] id={rid:4d} pass={pass_num} type={item_type:<12} {note[:70]}")

        if pass_num not in stats:
            stats[pass_num] = {"approve": 0, "reject": 0}
        if decision == 1:
            stats[pass_num]["approve"] += 1
            approved_total += 1
        else:
            stats[pass_num]["reject"] += 1
            rejected_total += 1

        if not DRY_RUN:
            authenticity = "authentic" if decision == 1 else "rejected"
            db.execute(
                "UPDATE pending_review SET reviewed=?, authenticity=?, review_notes=? WHERE id=?",
                (decision, authenticity, note[:200], rid)
            )

    if not DRY_RUN:
        db.commit()
        print("\n[committed to DB]")
    else:
        print("\n[dry-run — no DB writes]")

    db.close()

    print(f"\n{'─'*60}")
    print(f"TOTAL: {len(rows)}  APPROVED: {approved_total}  REJECTED: {rejected_total}")
    print(f"{'─'*60}")
    for p, s in sorted(stats.items()):
        print(f"  Pass {p}: {s['approve']} approved, {s['reject']} rejected")
    print()


if __name__ == "__main__":
    main()
