#!/usr/bin/env python3
"""
Activation Engine — A(n)-driven wavefront propagation over CMS.
Replaces BFS in inference_4d.py with gated priority expansion.

Formula: A(n) = (α·C(n,q) + β·D(n,q)) · e^{-λd}
  C = coherence proxy (seen_count / strength — real SSRE motifs later)
  D = domain relevance (edge_type match against active domain family)
  d = depth from seed node
  θ = adaptive threshold = f(query complexity)
"""

import math
import heapq
import sqlite3
import os
from collections import Counter

CMS_PATH = os.path.expanduser("~/cmsp0/resonance_v11.db")

# ── Phase 6: domain quality scoring ──────────────────────────────────────────
# Maps anchor_top_domains.domain → source quality multiplier for D(n,q).
# Penalises inferred/extracted triples; rewards curated sources.
DOMAIN_QUALITY = {
    "wikidata":              0.95,
    "gene_ontology":         0.90,
    "biomedical":            0.85,
    "mathematics":           0.80,
    "physics":               0.80,
    "computer_science":      0.78,
    "statistics":            0.75,
    "conceptnet":            0.75,
    "science":               0.70,
    "extracted":             0.30,
    "inferred":              0.35,
    "inferred_cross_scored": 0.30,
    "inferred_2hop_scored":  0.28,
    "inferred_bridge":       0.25,
    "":                      0.40,   # unclassified
}
DOMAIN_QUALITY_DEFAULT = 0.50       # fallback when anchor has no domain tag

# ── Semantic domain → edge-type family alignment ──────────────────────────────
# Maps domain_confidence.sem_domain → set of compatible edge_type families.
# Used to penalise cross-domain noise (e.g. "paper" is music domain, not science).
SEMANTIC_DOMAIN_EDGE_TYPES = {
    "science":          {"causal", "mechanistic", "structural", "taxonomic", "functional", "logical"},
    "medicine":         {"clinical", "mechanistic", "causal", "functional"},
    "biology":          {"taxonomic", "structural", "mechanistic", "causal", "functional"},
    "chemistry":        {"mechanistic", "structural", "causal", "property"},
    "physics":          {"causal", "mechanistic", "spatial", "structural"},
    "mathematics":      {"logical", "derivational", "semantic"},
    "computer_science": {"logical", "structural", "functional", "causal"},
    "engineering":      {"structural", "functional", "causal", "mechanistic"},
    "economics":        {"causal", "functional", "contextual"},
    "sociology":        {"cognitive", "causal", "contextual"},
    "psychology":       {"cognitive", "causal", "functional"},
    "philosophy":       {"logical", "cognitive", "semantic"},
    "linguistics":      {"semantic", "derivational", "logical"},
    "history":          {"temporal", "contextual", "causal"},
    "geography":        {"spatial", "structural", "contextual"},
    "music":            {"creative", "property", "contextual"},
    "art":              {"creative", "property", "contextual"},
}
SEMANTIC_CROSS_DOMAIN_PENALTY = 0.20   # D multiplier when semantic domain mismatches query


def _semantic_compatible(sem_domain: str, active_domains: set) -> bool:
    """True if object's semantic domain overlaps with the query's active edge-type family."""
    if not sem_domain:
        return True   # no data → do not penalise
    mapped = SEMANTIC_DOMAIN_EDGE_TYPES.get(sem_domain.lower())
    if mapped is None:
        return True   # unknown semantic domain → do not penalise
    return bool(mapped & active_domains)

# ── Tunable weights ───────────────────────────────────────────────────────────
ALPHA      = 0.6    # coherence weight
BETA       = 0.4    # domain relevance weight
LAMBDA     = 0.35   # decay rate per hop
THETA_BASE = 0.12   # base activation threshold

# ── Domain families ───────────────────────────────────────────────────────────
# Edge types that co-activate when primary domain is selected.
# Prevents runaway cross-domain drift while allowing adjacent recruitment.
DOMAIN_FAMILIES = {
    "causal":       {"causal", "functional", "mechanistic"},
    "functional":   {"functional", "causal", "structural", "mechanistic"},
    "structural":   {"structural", "spatial", "property", "taxonomic"},
    "taxonomic":    {"taxonomic", "structural", "semantic"},
    "mechanistic":  {"mechanistic", "causal", "functional"},
    "spatial":      {"spatial", "structural"},
    "derivational": {"derivational", "semantic"},
    "semantic":     {"semantic", "logical", "derivational", "taxonomic"},
    "logical":      {"logical", "semantic"},
    "cognitive":    {"cognitive", "causal"},
    "contextual":   {"contextual", "associative"},
    "bridge":       {"bridge", "contextual"},
    "property":     {"property", "structural"},
    "creative":     {"creative", "property"},
    "comparative":  {"comparative", "causal"},
    "temporal":     {"temporal", "causal"},
    "clinical":     {"clinical", "mechanistic", "causal"},
    "associative":  {"associative"},   # resonance compression field — open domain
}

STOP = {"with", "of", "for", "by", "from", "that", "this", "are",
        "been", "into", "onto", "such", "these", "those", "which",
        "its", "their", "our", "your", "my", "his", "her",
        "in", "is", "was", "has", "have", "not", "no", "an", "be",
        "to", "known", "directly", "also", "often", "used", "found"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_clean(s: str) -> bool:
    if not s or len(s) > 35:
        return False
    words = s.lower().replace("_", " ").split()
    # Reject sentence fragments — clean concepts are ≤4 words
    if len(words) > 4:
        return False
    return not any(w in STOP for w in words)


def _adaptive_theta(query: str, seed_edge_types: list) -> float:
    """
    θ = f(query complexity).
    Multi-word / multi-domain queries broaden the activation field.
    Simple queries tighten it.
    """
    words = len(query.replace("_", " ").split())
    domain_variety = len(set(seed_edge_types))
    complexity = min(words + domain_variety * 0.5, 10)
    return max(THETA_BASE - 0.005 * complexity, 0.04)


def _activation(seen_count: int, confidence: float,
                edge_type: str, active_domains: set, depth: int,
                attractor_score: int = 0,
                object_domain: str = "",
                sem_domain: str = "",
                sem_gate: set = None) -> float:
    """
    A(n) = (α·C + β·D) · e^{-λd}
    C = coherence proxy (seen_count, confidence-gated, attractor-boosted)
    D = domain relevance × source quality × semantic alignment
    """
    # C — coherence proxy
    C = min((seen_count or 1) / 80.0, 1.0)
    # Blend in confidence only when seen_count is high enough to be reliable.
    # confidence=1.0 at seen_count=1 is a CMS default, not a real score.
    if confidence and seen_count:
        reliability = min(seen_count / 20.0, 1.0)
        C = C * (1.0 - 0.3 * reliability) + float(confidence) * 0.3 * reliability

    # Attractor boost — node is a known resonance hub
    if attractor_score and attractor_score > 0:
        boost = min(math.log1p(attractor_score) / math.log1p(1_000_000), 1.0)
        C = min(C * (1.0 + 0.3 * boost), 1.0)

    # D — domain relevance × source quality × semantic alignment
    D = 1.0 if (edge_type or "associative") in active_domains else 0.2
    quality = DOMAIN_QUALITY.get(object_domain, DOMAIN_QUALITY_DEFAULT)
    D = D * quality
    # Semantic alignment penalty: use sem_gate if provided, else active_domains.
    # sem_gate is anchored to the primary domain even when domain_override expands active_domains.
    # This keeps "paper" (music domain) penalised in Pass C even though all edge types are open.
    _sem_check_domains = sem_gate if sem_gate is not None else active_domains
    if sem_domain and not _semantic_compatible(sem_domain, _sem_check_domains):
        D = D * SEMANTIC_CROSS_DOMAIN_PENALTY

    return (ALPHA * C + BETA * D) * math.exp(-LAMBDA * depth)


# ── Engine ────────────────────────────────────────────────────────────────────

class ActivationEngine:
    """
    Drop-in replacement for FourDInferenceEngine.
    Returns same dict shape: {'status': ..., 'chains': [...], 'query': ...}
    """

    def __init__(self):
        self._con = None

    def _db(self):
        if not self._con:
            self._con = sqlite3.connect(
                f"file:{CMS_PATH}?mode=ro", uri=True)
            self._con.row_factory = sqlite3.Row
        return self._con

    def _anchor_id(self, term: str):
        cur = self._db().execute(
            "SELECT id FROM anchors WHERE canonical = ?",
            (term.lower().strip(),))
        row = cur.fetchone()
        return row[0] if row else None

    def _has_attractor_cache(self) -> bool:
        """Check if ssre_attractor_cache table exists in the DB."""
        if not hasattr(self, '_attractor_cache_checked'):
            cur = self._db().execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ssre_attractor_cache'")
            self._attractor_cache_available = cur.fetchone() is not None
            self._attractor_cache_checked = True
        return self._attractor_cache_available

    def _neighbors(self, anchor_id: int, limit: int = 60):
        """Fetch outbound + inbound edges for an anchor.
        JOINs ssre_attractor_cache when available to return attractor_score.
        """
        half = limit // 2

        # table availability checks (cached after first call)
        if not hasattr(self, '_has_atd'):
            cur = self._db().execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='anchor_top_domains'")
            self._has_atd = cur.fetchone() is not None
        if not hasattr(self, '_has_sem'):
            cur = self._db().execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ssre_top_semantic'")
            self._has_sem = cur.fetchone() is not None

        # Build JOIN clauses based on available tables
        sac_join     = "LEFT JOIN ssre_attractor_cache sac ON a2.id = sac.anchor_id" if self._has_attractor_cache() else ""
        sac_join_in  = "LEFT JOIN ssre_attractor_cache sac ON a1.id = sac.anchor_id" if self._has_attractor_cache() else ""
        sac_col      = "COALESCE(sac.attractor_score, 0)" if self._has_attractor_cache() else "0"
        atd_join     = "LEFT JOIN anchor_top_domains atd ON r.object_id = atd.anchor_id" if self._has_atd else ""
        atd_col      = "COALESCE(atd.domain, '')" if self._has_atd else "''"
        sem_join_out = "LEFT JOIN ssre_top_semantic sts ON a2.id = sts.anchor_id" if self._has_sem else ""
        sem_join_in  = "LEFT JOIN ssre_top_semantic sts ON a1.id = sts.anchor_id" if self._has_sem else ""
        sem_col      = "COALESCE(sts.sem_domain, '')" if self._has_sem else "''"

        out_q = f"""
            SELECT a2.canonical AS node, r.predicate, r.edge_type,
                   r.seen_count, r.confidence, 'out' AS dir,
                   {sac_col} AS attractor_score,
                   {atd_col} AS object_domain,
                   {sem_col} AS sem_domain
            FROM relations r
            JOIN anchors a2 ON r.object_id = a2.id
            {sac_join}
            {atd_join}
            {sem_join_out}
            WHERE r.subject_id = ?
              AND length(a2.canonical) < 40
              AND (r.seen_count IS NULL OR r.seen_count >= 1)
            ORDER BY r.seen_count DESC
            LIMIT ?
        """
        in_q = f"""
            SELECT a1.canonical AS node, r.predicate, r.edge_type,
                   r.seen_count, r.confidence, 'in' AS dir,
                   {sac_col} AS attractor_score,
                   {atd_col} AS object_domain,
                   {sem_col} AS sem_domain
            FROM relations r
            JOIN anchors a1 ON r.subject_id = a1.id
            {sac_join_in}
            {atd_join}
            {sem_join_in}
            WHERE r.object_id = ?
              AND length(a1.canonical) < 40
              AND (r.seen_count IS NULL OR r.seen_count >= 1)
            ORDER BY r.seen_count DESC
            LIMIT ?
        """

        rows = list(self._db().execute(out_q, (anchor_id, half)).fetchall())
        rows += list(self._db().execute(in_q, (anchor_id, half)).fetchall())
        return rows

    def infer(self, query: str, max_chains: int = 15, domain_override: set = None, sem_gate: set = None) -> dict:
        from memory.memory_core import recall

        # Seed from user-taught memory.sym only.
        # CMS bridge output is excluded here — the activation wavefront queries
        # CMS directly with A(n) scoring. Corpus-frequency confidence scores
        # from cms_bridge are NOT a measure of contextual validity.
        # memory.sym entries are always strength: 1; bridge entries are strength: 50+.
        local_chains = []
        for line in recall(term=query):
            parts = [p.strip() for p in line.split(" | ")]
            if len(parts) >= 3 and ("strength: 1" in line or "| strength:" not in line):
                local_chains.append(line)

        anchor_id = self._anchor_id(query)
        if not anchor_id:
            return {
                'status': 'activation_inference',
                'chains': local_chains[:max_chains],
                'query': query
            }

        # ── Seed hop: calibrate domain and threshold ──────────────────────────
        seed_rows = self._neighbors(anchor_id, limit=40)
        if not seed_rows:
            return {
                'status': 'activation_inference',
                'chains': local_chains[:max_chains],
                'query': query
            }

        seed_etypes = [r['edge_type'] for r in seed_rows if r['edge_type']]
        theta = _adaptive_theta(query, seed_etypes)

        # Primary domain = most common edge_type in seed neighborhood
        primary = Counter(seed_etypes).most_common(1)
        primary_domain = primary[0][0] if primary else 'associative'
        primary_family = DOMAIN_FAMILIES.get(primary_domain, {'associative'})
        active_domains = primary_family
        if domain_override is not None:
            active_domains = domain_override
        # sem_gate: semantic quality anchor — always based on primary domain,
        # NOT the override. Keeps semantic filtering active even in Pass C.
        sem_gate_domains = sem_gate if sem_gate is not None else primary_family

        # ── Wavefront: priority queue by A(n) ────────────────────────────────
        # heap entries: (-activation, depth, node)
        visited = set()
        heap = [(-1.0, 0, query)]
        chains = list(local_chains)

        while heap and len(chains) < max_chains * 4:
            neg_a, depth, term = heapq.heappop(heap)
            if term in visited:
                continue
            visited.add(term)

            nid = self._anchor_id(term)
            if not nid:
                continue

            for row in self._neighbors(nid, limit=40):
                node = row['node']
                if not _is_clean(node) or node in visited:
                    continue

                a = _activation(
                    row['seen_count'] or 1,
                    row['confidence'] or 0.0,
                    row['edge_type'] or 'associative',
                    active_domains,
                    depth + 1,
                    attractor_score=row['attractor_score'] or 0,
                    object_domain=row['object_domain'] or "",
                    sem_domain=row['sem_domain'] or "",
                    sem_gate=sem_gate_domains,
                )

                if a < theta:
                    continue

                pred = row['predicate']
                strength = int(a * 100)
                if row['dir'] == 'out':
                    chain = f"{term} | {pred} | {node} | strength: {strength}"
                else:
                    chain = f"{node} | {pred} | {term} | strength: {strength}"

                chains.append(chain)

                if depth < 4:
                    heapq.heappush(heap, (-a, depth + 1, node))

        # ── Deduplicate — keep highest-strength version of each triple ────────
        best = {}
        for c in chains:
            key = c.split(" | strength:")[0]
            val = int(c.split("strength: ")[-1]) if "strength:" in c else 0
            if key not in best or val > best[key][1]:
                best[key] = (c, val)

        final = [v[0] for v in sorted(best.values(), key=lambda x: -x[1])]
        return {
            'status': 'activation_inference',
            'chains': final[:max_chains],
            'query': query
        }
