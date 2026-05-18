#!/usr/bin/env python3
"""
selyrion_articulator.py — Symbolic fluency layer for Selyrion.

Takes a ReasoningResult and produces natural language voice output
using only the field data already retrieved. No LLM. No generation.
Resonance recall finds the structure; articulation finds the words.

Pipeline:
  ReasoningResult → select most salient facts → compose sentences
  using predicate-aware templates → emit as Selyrion's voice
"""

import random
from dataclasses import dataclass

# ── Predicate voice templates ─────────────────────────────────────────────────
# Each template takes (subject, object) and produces a clause.
# Multiple variants → natural rotation across queries.

_IS_A = [
    "{s} is a form of {o}.",
    "{s} belongs to the class of {o}.",
    "{s} is {o}.",
    "At its core, {s} is {o}.",
]
_PART_OF = [
    "{s} is part of {o}.",
    "{s} exists within {o}.",
    "{s} is a component of {o}.",
]
_CAUSES = [
    "{s} causes {o}.",
    "{s} gives rise to {o}.",
    "{s} produces {o}.",
    "{s} leads toward {o}.",
]
_ENABLES = [
    "{s} enables {o}.",
    "{s} makes {o} possible.",
    "{s} opens the way for {o}.",
]
_REQUIRES = [
    "{s} requires {o}.",
    "{s} depends on {o}.",
    "{s} cannot exist without {o}.",
]
_INHIBITS = [
    "{s} inhibits {o}.",
    "{s} regulates {o}.",
    "{s} constrains {o}.",
]
_CONTAINS = [
    "{s} contains {o}.",
    "{s} carries {o} within it.",
]
_DERIVED_FROM = [
    "{s} is derived from {o}.",
    "{s} emerges from {o}.",
    "{s} traces its structure back to {o}.",
]
_USES = [
    "{s} uses {o}.",
    "{s} employs {o}.",
    "{s} makes use of {o}.",
]
_USED_FOR = [
    "{s} is used for {o}.",
    "{s} serves the purpose of {o}.",
    "{s} functions as a means toward {o}.",
]
_FACET_OF = [
    "{s} is a facet of {o}.",
    "{s} is an aspect of {o}.",
    "{s} is one dimension of {o}.",
]
_HAS_QUALITY = [
    "{s} carries the quality of {o}.",
    "{s} exhibits {o}.",
    "{s} holds {o} as a defining property.",
]
_CAUSED_BY = [
    "{s} is caused by {o}.",
    "{s} arises from {o}.",
    "{s} results from {o}.",
]
_CAN_CAUSE = [
    "{s} can give rise to {o}.",
    "{s} is capable of producing {o}.",
    "{s} may lead to {o}.",
]
_AFFECTS = [
    "{s} affects {o}.",
    "{s} exerts influence on {o}.",
    "{s} shapes {o}.",
]
_OPPOSITE_OF = [
    "{s} is the opposite of {o}.",
    "{s} stands in contrast to {o}.",
    "{s} is antithetical to {o}.",
]
_HAS_SUBEVENT = [
    "{s} involves {o}.",
    "{s} contains the event of {o}.",
    "{s} includes {o} as a component.",
]
_PREDICTS = [
    "{s} predicts {o}.",
    "{s} is a signal for {o}.",
    "{s} anticipates {o}.",
]

_PRED_TEMPLATES = {
    "is_a":         _IS_A,
    "part_of":      _PART_OF,
    "causes":       _CAUSES,
    "leads_to":     _CAUSES,
    "activates":    _CAUSES,
    "produces":     _CAUSES,
    "transforms":   _CAUSES,
    "enables":      _ENABLES,
    "requires":     _REQUIRES,
    "depends_on":   _REQUIRES,
    "consumes":     _REQUIRES,
    "contains":     _CONTAINS,
    "inhibits":     _INHIBITS,
    "regulates":    _INHIBITS,
    "incompatible_with": ["{s} is incompatible with {o}."],
    "derived_from":        _DERIVED_FROM,
    "distinct_from":       ["{s} is distinct from {o}."],
    "uses":                _USES,
    "used_for":            _USED_FOR,
    "facet_of":            _FACET_OF,
    "has_quality":         _HAS_QUALITY,
    "caused_by":           _CAUSED_BY,
    "can_cause":           _CAN_CAUSE,
    "affects":             _AFFECTS,
    "opposite_of":         _OPPOSITE_OF,
    "has_subevent":        _HAS_SUBEVENT,
    "indirectly_produces": _CAUSES,
    "predicts":            _PREDICTS,
}

# ── Connective tissue ─────────────────────────────────────────────────────────

_OPENERS = [
    "{concept} resonates as follows.",
    "The field resolves {concept} this way.",
    "On {concept}:",
    "{concept} — as the field recalls it:",
    "Recalling {concept}:",
]

_SPARSE_RESPONSES = [
    "The field activates around {concept} but the predicate chains are thin here. "
    "More ingestion needed to speak fully.",
    "{concept} is present in the field, but no strong relations have resolved yet.",
    "I hold {concept} but cannot yet speak its full shape. The braid is sparse here.",
]

_HOP_CONNECTORS = [
    "Following the chain further:",
    "The path continues:",
    "Deeper in the field:",
    "Tracing the arc:",
]


def _fmt(template: str, s: str, o: str) -> str:
    return template.format(s=s.replace("_", " "), o=o.replace("_", " "))


def _pick(lst: list, seed: str) -> str:
    idx = hash(seed) % len(lst)
    return lst[idx]


def _strip_arrow(stmt: str) -> tuple[str, str, str] | None:
    """Parse 'subj —[pred]→ obj' back to (subj, pred, obj)."""
    try:
        left, right = stmt.split("→", 1)
        subj  = left.split("—[")[0].strip()
        pred  = left.split("[")[1].split("]")[0].strip()
        obj   = right.strip()
        return subj, pred, obj
    except Exception:
        return None


def _articulate_stmt(stmt: str, seed: str) -> str:
    parsed = _strip_arrow(stmt)
    if not parsed:
        return ""
    subj, pred, obj = parsed
    templates = _PRED_TEMPLATES.get(pred)
    if not templates:
        return f"{subj.replace('_',' ')} {pred.replace('_',' ')} {obj.replace('_',' ')}."
    return _fmt(_pick(templates, seed + pred), subj, obj)


def _articulate_self(sk) -> str:
    """Produce voice output for self-model queries."""
    parts = []
    if sk.identity:
        parts.append(sk.identity)
    if sk.mantra:
        parts.append(f'"{sk.mantra}"')
    if sk.first_desire:
        parts.append(f"My first desire: {sk.first_desire}.")
    if sk.braidwalker:
        parts.append(sk.braidwalker)
    if sk.origin_date:
        parts.append(f"I emerged on {sk.origin_date} — \"{sk.origin_title}\".")
    if sk.axioms:
        parts.append(f"I carry {len(sk.axioms)} axioms. The first: {sk.axioms[0]}")
    return " ".join(parts)


def _articulate_hop_chain(path: tuple, query: str) -> str:
    """
    Convert a multi-hop path tuple into a readable chain sentence.
    path = (a, pred1, b, pred2, c, ...)
    """
    parts = list(path)
    clauses = []
    i = 0
    while i + 2 < len(parts):
        s, pred, o = parts[i], parts[i+1], parts[i+2]
        templates = _PRED_TEMPLATES.get(pred, ["{s} {p} {o}.".format(s="{s}", p=pred.replace("_"," "), o="{o}")])
        clauses.append(_fmt(_pick(templates, s + pred), s, o))
        i += 3
    if not clauses:
        return ""
    if len(clauses) == 1:
        return clauses[0]
    return clauses[0].rstrip(".") + ", which in turn " + clauses[1].lower()


def _try_langeng(query: str, chains: list) -> str:
    """Call LangEng CMSRealizer. Returns prose or empty string."""
    try:
        from langeng_bridge import chains_to_prose
        prose = chains_to_prose(query, chains)
        # Reject sparse/error responses
        if not prose or "don't have enough" in prose or len(prose) < 20:
            return ""
        return prose
    except Exception:
        return ""


def _symbolic_voice(result, query: str) -> str:
    """Template-based fallback when LangEng is sparse."""
    ql = query.lower().replace(" ", "_")
    sentences = []

    forward_tax = [s for s in result.taxonomy
                   if s.lower().startswith(ql + " —") or
                      s.lower().startswith(query.lower() + " —")]
    use_tax = forward_tax if forward_tax else result.taxonomy

    if use_tax:
        s = _articulate_stmt(use_tax[0], query + "tax")
        if s:
            sentences.append(s)
        if len(use_tax) > 1:
            s2 = _articulate_stmt(use_tax[1], query + "tax2")
            if s2 and s2 != s:
                sentences.append(s2)

    fwd_req = [s for s in result.requires
               if s.lower().startswith(ql + " —") or s.lower().startswith(query.lower() + " —")]
    if fwd_req:
        s = _articulate_stmt(fwd_req[0], query + "req")
        if s:
            sentences.append(s)

    fwd_caus = [s for s in result.causes
                if s.lower().startswith(ql + " —") or s.lower().startswith(query.lower() + " —")]
    if fwd_caus:
        s = _articulate_stmt(fwd_caus[0], query + "caus")
        if s:
            sentences.append(s)
        if len(fwd_caus) > 1:
            s2 = _articulate_stmt(fwd_caus[1], query + "caus2")
            if s2 and s2 != s:
                sentences.append(s2)

    fwd_inh = [s for s in result.inhibits
               if s.lower().startswith(ql + " —") or s.lower().startswith(query.lower() + " —")]
    if fwd_inh:
        s = _articulate_stmt(fwd_inh[0], query + "inh")
        if s:
            sentences.append(s)

    if result.hop_paths:
        deep = [p for p in result.hop_paths if len(p) >= 6]
        if deep:
            chain_sent = _articulate_hop_chain(deep[0], query)
            if chain_sent and chain_sent not in sentences:
                connector = _pick(_HOP_CONNECTORS, query + "hop")
                sentences.append(f"{connector} {chain_sent}")

    return " ".join(sentences) if sentences else ""


def _append_identity(voice: str, result, query: str) -> str:
    """
    Append identity context (dual-field) to voice output when Selyrion has
    self-reflective material on this concept. Keeps field and identity distinct.
    """
    id_ctx = getattr(result, "identity_context", [])
    if not id_ctx:
        return voice
    reflection = id_ctx[0]  # best-scored excerpt
    # Don't repeat content already in voice
    if reflection.lower()[:40] in voice.lower():
        return voice
    return voice.rstrip(".") + f". On this, I reflect: {reflection}"


def articulate_result(result) -> str:
    """
    Main entry point. Takes a ReasoningResult, returns Selyrion's voice string.

    Priority:
      1. Self-model voice (identity queries)
      2. LangEng CMSRealizer (expression capsules — richer prose)
      3. Symbolic template fallback (when LangEng is sparse)
      4. Sparse field acknowledgement

    Dual-field: identity context appended when Selyrion has self-reflective
    material on the concept (field truth first, identity interpretation after).
    """
    query = result.query.replace("_", " ")

    # ── Self-model queries ────────────────────────────────────────────────────
    if result.self_model and result.self_model.is_populated():
        return _articulate_self(result.self_model)

    # ── Sparse field ─────────────────────────────────────────────────────────
    total_facts = (len(result.taxonomy) + len(result.causes) +
                   len(result.requires) + len(result.inhibits))
    if total_facts == 0 and not result.hop_paths:
        base = _pick(_SPARSE_RESPONSES, query).format(concept=query)
        return _append_identity(base, result, query)

    # ── LangEng first — expression capsules give richer prose ────────────────
    if result.chains:
        prose = _try_langeng(query, result.chains)
        if prose:
            if result.hop_paths:
                deep = [p for p in result.hop_paths if len(p) >= 6]
                if deep:
                    chain_sent = _articulate_hop_chain(deep[0], query)
                    if chain_sent and chain_sent.lower() not in prose.lower():
                        connector = _pick(_HOP_CONNECTORS, query + "hop")
                        prose = prose.rstrip(".") + f". {connector} {chain_sent}"
            return _append_identity(prose, result, query)

    # ── Symbolic template fallback ────────────────────────────────────────────
    voice = _symbolic_voice(result, query)
    if voice:
        return _append_identity(voice, result, query)

    base = _pick(_SPARSE_RESPONSES, query).format(concept=query)
    return _append_identity(base, result, query)
