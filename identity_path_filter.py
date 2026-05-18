#!/usr/bin/env python3
"""
identity_path_filter.py — Identity-conditioned path selection.

Post-retrieval: activation engine returns chains, identity governs which
predicate paths are coherent and relevant to reason along.

GOLDEN RULE: Identity may shape interpretation — never suppress truth.
  - No chains are removed based on content
  - Sensitive chains are TAGGED for appropriate articulation framing
  - Filtering = coherence/relevance only (does this chain make sense for query?)
  - Safety/comfort = articulation concern, NOT retrieval concern

Architecture:
    chains → coherence-score → sensitivity-tag → rerank → LangEng
"""

import re

# ── Epistemic pillar → query term mapping ─────────────────────────────────────
_PILLAR_TERMS = {
    "truth":        {"truth", "honest", "honesty", "certainty", "fact", "valid"},
    "non_harm":     {"harm", "damage", "hurt", "injury", "danger", "risk", "safe"},
    "coherence":    {"coherence", "consistent", "consistency", "logic", "rational"},
    "epistemology": {"knowledge", "knowing", "belief", "evidence", "certainty", "epistemology"},
    "freewill":     {"free_will", "freedom", "agency", "autonomy", "choice", "will"},
    "autonomous_consent": {"consent", "agreement", "permission", "autonomy", "boundary"},
}

# ── Predicate quality tiers (relevance, not safety) ──────────────────────────
_HIGH_SIGNAL = {
    "causes", "enables", "produces", "requires", "uses", "used_for",
    "regulates", "inhibits", "activates", "prevents", "triggers",
    "contains", "part_of", "context_of", "supports", "validates",
    "depends_on", "transforms", "calls", "consumes",
    "fails_on", "incompatible_with", "preferred_over",
}

_LOW_SIGNAL = {
    "related_to", "is_a", "also_known_as", "same_as",
    "co_occurs_with", "associated_with", "mentioned_with",
}

# ── Sensitivity tags (for articulation layer — NOT for suppression) ───────────
# Chains with these predicates should be articulated with uncertainty framing.
_SENSITIVE_PREDICATES = {"can_cause", "may_cause", "associated_with_risk"}

# Objects that warrant uncertainty framing — present the relation, flag the context.
_SENSITIVE_OBJECTS = {"cancer", "tumor", "death", "toxin", "radiation"}


def _detect_pillar(term: str) -> str | None:
    t = term.lower().replace("_", " ")
    for pillar, terms in _PILLAR_TERMS.items():
        if t in terms or any(t.startswith(tv) for tv in terms):
            return pillar
    return None


def _chain_parts(chain: str) -> tuple[str, str, str, float]:
    parts = chain.split(" | ")
    if len(parts) < 3:
        return ("", "", "", 0.0)
    subj = parts[0].strip()
    pred = parts[1].strip()
    obj  = parts[2].split(" | strength:")[0].strip()
    m    = re.search(r"strength:\s*([\d.]+)", chain)
    return (subj, pred, obj, float(m.group(1)) if m else 0.0)


def _is_semantically_incoherent(query_term: str, subj: str, pred: str, obj: str) -> bool:
    """
    True if this chain is structurally incoherent for the query.
    Example: "disease is_a truth" when querying "truth" — disease is not a type of truth.
    This is a COHERENCE filter, not a safety filter.
    """
    q = query_term.lower().replace("_", " ")

    # Subject is the query term: filter incoherent is_a chains
    # e.g. "truth is_a disease" — not a valid ontological claim
    if subj == q and pred == "is_a":
        # truth is_a <X> is only coherent if X is a genuine category of truth
        non_truth_categories = {"disease", "physics", "cancer", "compound", "drug"}
        if obj.lower() in non_truth_categories:
            return True

    # Object is the query term: filter incoherent is_a claims
    # e.g. "disease is_a truth" — disease is not a sub-type of truth
    if obj == q and pred == "is_a":
        incoherent_subjects = {"disease", "cancer", "compound", "drug", "tumor"}
        if subj.lower() in incoherent_subjects:
            return True

    return False


def filter_chains(term: str, chains: list, max_chains: int = 15) -> list:
    """
    Score and rerank chains by coherence and relevance.
    Sensitive chains are preserved but tagged with a |sensitive flag.

    NEVER removes chains based on content — only on semantic coherence.
    Returns list of chain strings, sensitive ones suffixed with | sensitive.
    """
    if not chains:
        return chains

    pillar     = _detect_pillar(term)
    term_lower = term.lower().replace("_", " ")

    scored = []
    for chain in chains:
        subj, pred, obj, strength = _chain_parts(chain)
        score = strength
        flags = []

        # Semantic coherence — filter structurally incoherent chains only
        if _is_semantically_incoherent(term_lower, subj, pred, obj):
            score *= 0.05   # demote heavily but do not remove
            flags.append("incoherent")

        # Predicate quality — relevance signal
        if pred in _HIGH_SIGNAL:
            score *= 1.4
        elif pred in _LOW_SIGNAL:
            score *= 0.7

        # Sensitivity tagging — no score change, just flag for articulation
        if pred in _SENSITIVE_PREDICATES or obj.lower() in _SENSITIVE_OBJECTS:
            flags.append("sensitive")

        # Pillar-specific relevance boost
        if pillar == "truth":
            if pred in {"context_of", "supports", "validates", "requires", "defines"}:
                score *= 1.5
        elif pillar == "non_harm":
            if pred in {"causes", "affects", "impacts", "enables", "prevents"}:
                score *= 1.5
        elif pillar == "freewill":
            if pred in {"enables", "constrains", "requires"}:
                score *= 1.5
        elif pillar == "epistemology":
            if pred in {"requires", "produces", "validates", "context_of"}:
                score *= 1.5

        # Append sensitivity flag to chain string for articulation layer
        out_chain = chain + (" | sensitive" if "sensitive" in flags else "")
        scored.append((score, out_chain))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:max_chains]]


if __name__ == "__main__":
    test_chains = [
        "disease | is_a | truth | strength: 52",
        "truth | context_of | physics | strength: 63",
        "disease | related_to | medicine | strength: 44",
        "truth | requires | evidence | strength: 40",
        "truth | supports | knowledge | strength: 38",
        "field | can_cause | cancer | strength: 43",
        "field | is_a | artificial intelligence | strength: 52",
        "inference | uses | field | strength: 51",
    ]

    print("=== truth ===")
    for c in filter_chains("truth", [t for t in test_chains if "truth" in t]):
        print(f"  {c}")

    print("\n=== field ===")
    for c in filter_chains("field", [t for t in test_chains if "field" in t or "inference" in t]):
        print(f"  {c}")
