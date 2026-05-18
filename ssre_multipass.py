#!/usr/bin/env python3
"""
SSRE Multipass — structured reliability layer above ActivationEngine.

3 domain-biased passes, deterministic scoring, structured failure classification.
No probabilistic selection. No ATLAS code.

Concepts extracted from ATLAS architecture and re-implemented
as Selyrion-native CMS-grounded logic.
"""

import sys
import os
from collections import Counter

sys.path.insert(0, os.path.expanduser("~/projectbrain_dev"))

from inference.activation_engine import ActivationEngine, DOMAIN_FAMILIES

# Epistemic weight per predicate — defines/causes/enables > contains/context_of
PREDICATE_WEIGHT = {
    "requires":    1.0,
    "enables":     1.0,
    "causes":      0.95,
    "is_a":        0.9,
    "produces":    0.85,
    "affects":     0.8,
    "binds_to":    0.75,
    "contains":    0.55,
    "contain":     0.35,   # malformed CMS variant — valid but suspect
    "context_of":  0.45,
    "manner_of":   0.4,
    "associative": 0.3,
}
PRED_WEIGHT_DEFAULT = 0.55   # unknown predicates score slightly below "contains"

# ── All known edge types (Pass C: open domain, no restriction) ────────────────
_ALL_EDGE_TYPES: set = set()
for _fam in DOMAIN_FAMILIES.values():
    _ALL_EDGE_TYPES |= _fam


def _adjacent_domains(primary_domain: str) -> set:
    """Pass B: expand one hop in domain graph (primary family + their neighbours)."""
    primary_family = DOMAIN_FAMILIES.get(primary_domain, {"associative"})
    expanded = set(primary_family)
    for et in primary_family:
        expanded |= DOMAIN_FAMILIES.get(et, set())
    return expanded


def _score_candidate(chains: list) -> float:
    """
    Deterministic scoring — no probability.
    score = n_chains * 0.4 + mean_strength * 0.4 + domain_spread * 0.2

    n_chains:     volume of chains above theta
    mean_strength: average A(n) quality (0–1)
    domain_spread: predicate diversity (coverage breadth)
    """
    if not chains:
        return 0.0

    weighted_strengths = []
    preds = set()
    for c in chains:
        parts = c.split(" | ")
        pred = parts[1] if len(parts) >= 2 else ""
        preds.add(pred)
        pw = PREDICATE_WEIGHT.get(pred, PRED_WEIGHT_DEFAULT)
        if "strength:" in c:
            try:
                weighted_strengths.append(int(c.split("strength: ")[-1]) * pw)
            except ValueError:
                pass

    n_score       = min(len(chains) / 15.0, 1.0)
    mean_strength = (sum(weighted_strengths) / len(weighted_strengths)) / 100.0 if weighted_strengths else 0.0
    domain_spread = min(len(preds) / 10.0, 1.0)

    return n_score * 0.4 + mean_strength * 0.4 + domain_spread * 0.2


def _signal_level(score: float) -> str:
    """
    Candidate selection quality — how well this result competed against other passes.
    Distinct from tag (data quality of the chains themselves).
    """
    if score >= 0.54:
        return "strong"
    elif score >= 0.34:
        return "moderate"
    return "weak"


def _data_quality(chains: list) -> float:
    """
    Fraction of chains with a known (whitelisted) predicate.
    1.0 = all predicates recognised. <0.7 = noisy pass.
    Not folded into score yet — exposed for transparency.
    """
    if not chains:
        return 0.0
    known = sum(
        1 for c in chains
        if len(c.split(" | ")) >= 2 and c.split(" | ")[1] in PREDICATE_WEIGHT
    )
    return round(known / len(chains), 2)


def _classify(chains: list) -> str:
    """
    Structured failure tag — becomes capsule seed in selyrionstory.db later.

    Tags:
      ok             — healthy result
      weak_signal    — chains exist but mean A(n) < 0.3
      memory_only    — all chains from memory.sym, CMS returned nothing
      unknown_query  — no chains at all
    """
    if not chains:
        return "unknown_query"

    cms_chains = [c for c in chains if "strength:" in c]
    real_cms   = [c for c in cms_chains if int(c.split("strength: ")[-1]) > 1]

    if not real_cms:
        return "memory_only"

    strengths  = [int(c.split("strength: ")[-1]) for c in real_cms]
    mean_s     = sum(strengths) / len(strengths) / 100.0

    if mean_s < 0.3:
        return "weak_signal"

    return "ok"


# ── Repair threshold ──────────────────────────────────────────────────────────
# Below this score: try next pass. After all 3 passes: pick best anyway.
REPAIR_THRESHOLD = 0.5


def _anchor_relevant(chain: str, query_norm: str) -> bool:
    """True if query anchor appears in the chain body (not the strength suffix)."""
    body = chain.split(" | strength:")[0].lower()
    return query_norm in body


def multipass_infer(query: str, max_chains: int = 15, verbose: bool = False) -> dict:
    """
    3-pass structured inference with deterministic best-candidate selection.

    Pass A — auto-detect primary domain (default activation behaviour)
    Pass B — expand to adjacent domain families (triggered if A below threshold)
    Pass C — full open pass, all edge types (triggered if B still below threshold)

    Returns best candidate deterministically (max score), never probabilistically.

    Result dict:
      status    : 'multipass_inference'
      chains    : list of chain strings
      query     : original query
      pass_used : 'A' | 'B' | 'C'
      score     : float (0–∞, higher = better)
      tag       : 'ok' | 'weak_signal' | 'memory_only' | 'unknown_query'
    """
    engine     = ActivationEngine()
    query_norm = query.lower().strip()

    def _run_pass(label, domain_override=None):
        r = engine.infer(query, max_chains=max_chains, domain_override=domain_override)
        # Relevance filter: only keep chains where query anchor appears
        r['chains'] = [c for c in r['chains'] if _anchor_relevant(c, query_norm)]
        s = _score_candidate(r['chains'])
        dq = _data_quality(r['chains'])
        if verbose:
            print(f"  [pass {label}] score={round(s,3)}  chains={len(r['chains'])}  dq={dq}  tag={_classify(r['chains'])}")
        return r, s, dq

    # ── Pass A: default (primary domain auto-detected) ────────────────────────
    result_a, score_a, dq_a = _run_pass('A')

    if score_a >= REPAIR_THRESHOLD:
        return {**result_a, 'status': 'multipass_inference',
                'pass_used': 'A', 'score': round(score_a, 3),
                'tag': _classify(result_a['chains']),
                'signal': _signal_level(score_a),
                'data_quality': dq_a}

    # Detect primary domain from Pass A chains for domain expansion
    preds_a = [c.split(" | ")[1] for c in result_a['chains']
               if len(c.split(" | ")) >= 2]
    primary_domain = Counter(preds_a).most_common(1)[0][0] if preds_a else "associative"
    adjacent = _adjacent_domains(primary_domain)

    # ── Pass B: adjacent domain expansion ────────────────────────────────────
    result_b, score_b, dq_b = _run_pass('B', domain_override=adjacent)

    if score_b >= REPAIR_THRESHOLD:
        return {**result_b, 'status': 'multipass_inference',
                'pass_used': 'B', 'score': round(score_b, 3),
                'tag': _classify(result_b['chains']),
                'signal': _signal_level(score_b),
                'data_quality': dq_b}

    # ── Pass C: full open pass (all edge types, no semantic restriction) ──────
    result_c, score_c, dq_c = _run_pass('C', domain_override=_ALL_EDGE_TYPES)

    # Pick best of A/B/C deterministically
    best_result, best_score, best_pass, best_dq = max(
        [(result_a, score_a, 'A', dq_a),
         (result_b, score_b, 'B', dq_b),
         (result_c, score_c, 'C', dq_c)],
        key=lambda x: x[1]
    )

    return {**best_result, 'status': 'multipass_inference',
            'pass_used': best_pass, 'score': round(best_score, 3),
            'tag': _classify(best_result['chains']),
            'signal': _signal_level(best_score),
            'data_quality': best_dq}


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args  = sys.argv[1:]
    verbose = "--verbose" in args or "-v" in args
    terms = [a for a in args if not a.startswith("-")]
    query = " ".join(terms) if terms else "dna"

    result = multipass_infer(query, verbose=verbose)
    print(f"\nQuery : {result['query']}")
    print(f"Pass  : {result['pass_used']}  |  Score: {result['score']}  |  Signal: {result['signal']}  |  DQ: {result['data_quality']}  |  Data: {result['tag']}")
    print()
    for c in result['chains']:
        print(" ", c)
