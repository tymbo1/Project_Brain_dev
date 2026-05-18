#!/usr/bin/env python3
"""
Activation Engine — A(n)-driven wavefront propagation over CMS.
Replaces BFS in inference_4d.py with gated priority expansion.

Formula: A(n) = (α·C(n,q) + β·D(n,q)) · e^{-λd}
  C = coherence proxy (log-normalised maturity — spreads full range 1→102B)
  D = domain relevance (edge_type match against active domain family)
  d = depth from seed node
  θ = adaptive threshold = f(query complexity)
"""

import math
import heapq
import re
import sqlite3
import os
from collections import Counter
from inference.concept_resolver import resolve as _resolve_concept

CMS_PATH = os.path.expanduser("~/resonance_v11.db")

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
    "linguistics":           0.82,   # HITL-reviewed LLM ingestion
    "medicine":              0.82,   # HITL-reviewed LLM ingestion
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
    "music":            {"creative", "contextual"},
    "art":              {"creative", "contextual"},
}
SEMANTIC_CROSS_DOMAIN_PENALTY = 0.05   # D cap when semantic domain mismatches query


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
THETA_BASE = 0.10   # base activation threshold

# ── ECAE bounds ───────────────────────────────────────────────────────────────
MAX_NEIGHBORS_PER_NODE = 20   # LIMIT in each SQL branch of _neighbors()
MAX_WAVEFRONT_NODES    = 150  # hard cap on visited set — prevents runaway expansion

# ── Domain-aware chain cap ────────────────────────────────────────────────────
# Domains with sufficient depth-pass coverage get a higher max_chains ceiling.
# Only add a domain here once it has at least 2 ingestion passes completed.
# The domain scoring (sem_gate, SEMANTIC_CROSS_DOMAIN_PENALTY) acts as guardrail.
DOMAIN_CHAIN_BOOST = {
    "linguistics": 25,
}

# ── Domain families ───────────────────────────────────────────────────────────
# Edge types that co-activate when primary domain is selected.
# Prevents runaway cross-domain drift while allowing adjacent recruitment.
DOMAIN_FAMILIES = {
    "causal":       {"causal", "functional", "mechanistic"},
    "functional":   {"functional", "causal", "structural", "mechanistic"},
    "structural":   {"structural", "spatial", "property", "taxonomic"},
    "taxonomic":    {"taxonomic", "structural", "semantic", "functional", "property"},
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
        "to", "known", "directly", "also", "often", "used", "found",
        # deictic / positional references — never valid concept anchors
        "here", "there", "where", "when", "then", "now", "thus",
        "hence", "above", "below", "ibid", "etc"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_clean(s: str) -> bool:
    if not s or len(s) > 35:
        return False
    # Reject leading artifact prefixes: "_ word" or "- word"
    if re.match(r'^[_\-]\s', s):
        return False
    # Reject trailing lone apostrophe (truncated token: "god'" "bed'")
    if s.endswith("'") and not re.search(r"'s?\b", s[:-1]):
        return False
    words = s.lower().replace("_", " ").split()
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


MATURITY_LOG_MAX = math.log1p(1.023e11)  # log1p(medicine maturity ~102B)

PUBMED_COOCCUR_PENALTY = 0.15   # D cap for co_occurs_with edges sourced from pubmed/mesh

def _activation(maturity: float,
                edge_type: str, active_domains: set, depth: int,
                attractor_score: int = 0,
                object_domain: str = "",
                sem_domain: str = "",
                sem_gate: set = None,
                rel_domain: str = "") -> float:
    """
    A(n) = (α·C + β·D) · e^{-λd}
    C = coherence proxy (log-normalised maturity — spreads full range 1→102B)
    D = domain relevance × source quality × semantic alignment
    """
    # C — log-normalised maturity: preserves spread across 6 orders of magnitude
    # medicine(102B)→1.0, protein(48M)→0.70, single-obs(1)→0.03
    C = min(math.log1p(maturity or 0.0) / MATURITY_LOG_MAX, 1.0)

    # Attractor boost — node is a known resonance hub
    if attractor_score and attractor_score > 0:
        boost = min(math.log1p(attractor_score) / math.log1p(1_000_000), 1.0)
        C = min(C * (1.0 + 0.3 * boost), 1.0)

    # D — domain relevance × source quality × semantic alignment
    D = 1.0 if (edge_type or "associative") in active_domains else 0.2
    quality = DOMAIN_QUALITY.get(object_domain, DOMAIN_QUALITY_DEFAULT)
    D = D * quality

    # Pubmed co-occurrence penalty: statistical co-occurrence from pubmed/mesh is
    # high-volume noise that crowds out curated semantic edges. Cap D but keep the
    # edge alive so genuine pubmed biomedical relations still pass threshold.
    if edge_type == "associative" and "pubmed" in rel_domain:
        D = min(D, PUBMED_COOCCUR_PENALTY)

    # Semantic alignment penalty: use sem_gate if provided, else active_domains.
    _sem_check_domains = sem_gate if sem_gate is not None else active_domains
    if sem_domain and not _semantic_compatible(sem_domain, _sem_check_domains):
        C = C * SEMANTIC_CROSS_DOMAIN_PENALTY
        D = min(D, SEMANTIC_CROSS_DOMAIN_PENALTY)

    return (ALPHA * C + BETA * D) * math.exp(-LAMBDA * depth)


# ── Engine ────────────────────────────────────────────────────────────────────

class ActivationEngine:
    """
    Drop-in replacement for FourDInferenceEngine.
    Returns same dict shape: {'status': ..., 'chains': [...], 'query': ...}
    """

    # Neighbor results for nodes seen in more than one query (per-session cache).
    _nbr_cache: dict = {}

    # Domain lookup dicts loaded once at first instantiation.
    # Eliminates per-query GROUP BY subquery JOINs (1.5M + 1.1M row scans).
    _atd_dict:  dict | None = None   # anchor_id → top domain
    _sem_dict:  dict | None = None   # anchor_id → sem_domain
    _sac_dict:  dict | None = None   # anchor_id → attractor_score
    _dicts_loaded: bool = False

    def __init__(self):
        self._con = None
        self._id_cache: dict = {}   # canonical → (anchor_id, relation_count)
        if not ActivationEngine._dicts_loaded:
            self._load_domain_dicts()

    def _load_domain_dicts(self):
        """Load domain lookup tables into class-level dicts once."""
        import time as _t
        t0 = _t.perf_counter()
        db = self._db()

        # anchor_top_domains: one domain per anchor (take MAX to pick deterministically)
        atd = {}
        for row in db.execute("SELECT anchor_id, domain FROM anchor_top_domains"):
            aid = row[0]
            if aid not in atd:
                atd[aid] = row[1] or ""
        ActivationEngine._atd_dict = atd

        # ssre_top_semantic: one sem_domain per anchor
        sem = {}
        for row in db.execute("SELECT anchor_id, sem_domain FROM ssre_top_semantic"):
            aid = row[0]
            if aid not in sem:
                sem[aid] = row[1] or ""
        ActivationEngine._sem_dict = sem

        # ssre_attractor_cache: small table, load fully
        sac = {}
        try:
            for row in db.execute("SELECT anchor_id, attractor_score FROM ssre_attractor_cache"):
                sac[row[0]] = row[1]
        except Exception:
            pass
        ActivationEngine._sac_dict = sac

        ActivationEngine._dicts_loaded = True
        elapsed = round((_t.perf_counter() - t0) * 1000)
        print(f"  [engine] domain dicts loaded: {len(atd):,} atd + {len(sem):,} sem + {len(sac):,} sac  ({elapsed}ms)", flush=True)

    def _db(self):
        if not self._con:
            self._con = sqlite3.connect(
                f"file:{CMS_PATH}?mode=ro", uri=True)
            self._con.row_factory = sqlite3.Row
            self._con.execute("PRAGMA cache_size=-32000")
            self._con.execute("PRAGMA temp_store=MEMORY")
        return self._con

    def _anchor_id(self, term: str):
        key = term.lower().strip()
        if key in self._id_cache:
            return self._id_cache[key]
        # Fast path: exact canonical match
        cur = self._db().execute(
            "SELECT id, relation_count FROM anchors WHERE canonical = ?", (key,))
        row = cur.fetchone()
        if row:
            result = (row[0], row[1])
            self._id_cache[key] = result
            return result
        # Cascade path: fuzzy + LLaMA concept extraction
        resolved = _resolve_concept(key, use_llm=True)
        result = (resolved[0], resolved[1]) if resolved else (None, 0)
        self._id_cache[key] = result
        return result

    def _has_attractor_cache(self) -> bool:
        """Check if ssre_attractor_cache table exists in the DB."""
        if not hasattr(self, '_attractor_cache_checked'):
            cur = self._db().execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ssre_attractor_cache'")
            self._attractor_cache_available = cur.fetchone() is not None
            self._attractor_cache_checked = True
        return self._attractor_cache_available

    def _enrich_rows(self, rows, id_col: str) -> list[dict]:
        """Post-process SQL rows: inject domain/sem/attractor from in-memory dicts."""
        atd = ActivationEngine._atd_dict or {}
        sem = ActivationEngine._sem_dict or {}
        sac = ActivationEngine._sac_dict or {}
        result = []
        for r in rows:
            aid = r[id_col]
            result.append({
                'node':           r['node'],
                'predicate':      r['predicate'],
                'edge_type':      r['edge_type'],
                'maturity':       r['maturity'],
                'dir':            r['dir'],
                'attractor_score': sac.get(aid, 0),
                'object_domain':  atd.get(aid, ''),
                'sem_domain':     sem.get(aid, ''),
                'rel_domain':     r['rel_domain'],
            })
        return result

    def _neighbors(self, anchor_id: int, limit: int = MAX_NEIGHBORS_PER_NODE * 3):
        """Fetch outbound + inbound edges for an anchor.
        Domain/sem/attractor enrichment done via in-memory dicts (no JOIN subqueries).
        """
        cache_key = (anchor_id, limit)
        if cache_key in ActivationEngine._nbr_cache:
            return ActivationEngine._nbr_cache[cache_key]

        half = limit // 2

        # Simplified queries — no domain JOIN subqueries
        out_q = """
            SELECT a2.id AS obj_id, a2.canonical AS node, r.predicate, r.edge_type,
                   a2.maturity AS maturity, 'out' AS dir,
                   COALESCE(r.domain_tags, '') AS rel_domain
            FROM relations_aggregated r
            JOIN anchors a2 ON r.object_id = a2.id
            WHERE r.subject_id = ?
              AND length(a2.canonical) < 40
            ORDER BY a2.maturity DESC
            LIMIT ?
        """
        in_q = """
            SELECT a1.id AS obj_id, a1.canonical AS node, r.predicate, r.edge_type,
                   a1.maturity AS maturity, 'in' AS dir,
                   COALESCE(r.domain_tags, '') AS rel_domain
            FROM relations_aggregated r
            JOIN anchors a1 ON r.subject_id = a1.id
            WHERE r.object_id = ?
              AND length(a1.canonical) < 40
            ORDER BY a1.maturity DESC
            LIMIT ?
        """

        # Two-pass fetch: top-maturity rows + specific non-generic edges.
        # Pass 1: top half rows by maturity
        raw_out = self._db().execute(out_q, (anchor_id, half)).fetchall()
        raw_in  = self._db().execute(in_q,  (anchor_id, half)).fetchall()

        def _dedup(raw, limit):
            seen: set = set()
            result = []
            for r in raw:
                key = (r['node'], r['predicate'])
                if key not in seen:
                    seen.add(key)
                    result.append(r)
                    if len(result) >= limit:
                        break
            return result, seen

        rows, seen_out = _dedup(raw_out, half)
        in_rows, seen_in = _dedup(raw_in, half)

        # Pass 2: specific (non-generic) edges only.
        _generic = "r.predicate NOT IN ('related_to', 'co_occurs_with')"
        spec_out_q = out_q.replace(
            "WHERE r.subject_id = ?",
            f"WHERE r.subject_id = ? AND {_generic}"
        )
        spec_in_q = in_q.replace(
            "WHERE r.object_id = ?",
            f"WHERE r.object_id = ? AND {_generic}"
        )
        spec_limit = max(min(half, MAX_NEIGHBORS_PER_NODE * 2), 20)
        for r in self._db().execute(spec_out_q, (anchor_id, spec_limit)).fetchall():
            key = (r['node'], r['predicate'])
            if key not in seen_out:
                seen_out.add(key)
                rows.append(r)
        for r in self._db().execute(spec_in_q, (anchor_id, spec_limit)).fetchall():
            key = (r['node'], r['predicate'])
            if key not in seen_in:
                seen_in.add(key)
                in_rows.append(r)

        rows += in_rows

        # Enrich with domain/sem/attractor from in-memory dicts (no SQL JOINs needed)
        enriched = self._enrich_rows(rows, 'obj_id')
        ActivationEngine._nbr_cache[cache_key] = enriched
        return enriched

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

        # Hub nodes (>100K relations) take seconds to query — don't expand them,
        # just record the edges to them.
        EXPAND_MAX_RELATIONS = 100_000

        anchor_id, seed_rc = self._anchor_id(query)
        if not anchor_id:
            return {
                'status': 'activation_inference',
                'chains': local_chains[:max_chains],
                'query': query
            }

        # Apply domain chain boost if the seed anchor belongs to a dense domain.
        seed_sem = self._db().execute(
            "SELECT sem_domain FROM ssre_top_semantic WHERE anchor_id = ? LIMIT 1",
            (anchor_id,)
        ).fetchone()
        if seed_sem and seed_sem[0] in DOMAIN_CHAIN_BOOST:
            max_chains = max(max_chains, DOMAIN_CHAIN_BOOST[seed_sem[0]])

        # ── Seed hop: calibrate domain and threshold ──────────────────────────
        seed_rows = self._neighbors(anchor_id, limit=MAX_NEIGHBORS_PER_NODE * 2)
        if not seed_rows:
            return {
                'status': 'activation_inference',
                'chains': local_chains[:max_chains],
                'query': query
            }

        seed_etypes = [r['edge_type'] for r in seed_rows if r['edge_type']]
        theta = _adaptive_theta(query, seed_etypes)

        # Primary domain = most common *specific* edge_type in seed neighborhood.
        # Exclude 'associative' (the default for generic related_to edges) from
        # domain calibration — it's noisy and would otherwise drown out the real
        # semantic structure (taxonomic, functional, mechanistic, etc.).
        specific_etypes = [e for e in seed_etypes if e and e != 'associative']
        primary = Counter(specific_etypes).most_common(1)
        primary_domain = primary[0][0] if primary else 'associative'
        primary_family = DOMAIN_FAMILIES.get(primary_domain, {'associative'})
        # Always include 'associative' so generic edges still pass
        active_domains = primary_family | {'associative'}
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

        while heap and len(chains) < max_chains * 4 and len(visited) < MAX_WAVEFRONT_NODES:
            neg_a, depth, term = heapq.heappop(heap)
            if term in visited:
                continue
            visited.add(term)

            nid, rc = self._anchor_id(term)
            if not nid:
                continue

            # Hub guard: don't expand massive nodes — they take seconds to query
            # and spread activation to unrelated concepts. Still record edges TO them.
            if rc > EXPAND_MAX_RELATIONS and term != query:
                continue

            for row in self._neighbors(nid, limit=MAX_NEIGHBORS_PER_NODE):
                node = row['node']
                if not _is_clean(node) or node in visited:
                    continue

                a = _activation(
                    row['maturity'] or 0.0,
                    row['edge_type'] or 'associative',
                    active_domains,
                    depth + 1,
                    attractor_score=row['attractor_score'] or 0,
                    object_domain=row['object_domain'] or "",
                    sem_domain=row['sem_domain'] or "",
                    sem_gate=sem_gate_domains,
                    rel_domain=row['rel_domain'] or "",
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

        # ── Collect direct semantic edges of query node (guaranteed slots) ──────
        # Build these separately so they're always included in the final output,
        # displacing pure-noise wavefront chains if necessary.
        # Includes: outbound non-generic predicates + inbound non-generic predicates.
        seed_chains = {}  # key → (chain_str, strength)
        _generic_preds = ('related_to', 'context_of')
        for row in seed_rows:
            node = row['node']
            pred = row['predicate']
            if pred in _generic_preds or not _is_clean(node):
                continue
            a = _activation(
                row['maturity'] or 0.0,
                row['edge_type'] or 'associative',
                active_domains, 1,
                attractor_score=row['attractor_score'] or 0,
                object_domain=row['object_domain'] or "",
                sem_domain=row['sem_domain'] or "",
                sem_gate=sem_gate_domains,
            )
            if a < theta * 0.5:
                continue
            strength = max(int(a * 100), 1)
            if row['dir'] == 'out':
                key = f"{query} | {pred} | {node}"
            else:
                key = f"{node} | {pred} | {query}"
            chain = f"{key} | strength: {strength}"
            if key not in seed_chains or strength > seed_chains[key][1]:
                seed_chains[key] = (chain, strength)

        # ── Deduplicate wavefront chains ──────────────────────────────────────
        best = {}
        for c in chains:
            key = c.split(" | strength:")[0]
            val = int(c.split("strength: ")[-1]) if "strength:" in c else 0
            if key not in best or val > best[key][1]:
                best[key] = (c, val)

        # Merge: seed chains always included; fill remaining slots from wavefront
        n_seed = len(seed_chains)
        n_wavefront = max(max_chains - n_seed, max_chains // 2)
        wavefront_chains = [v[0] for v in sorted(best.values(), key=lambda x: -x[1])
                            if v[0].split(" | strength:")[0] not in seed_chains][:n_wavefront]
        seed_list = [v[0] for v in sorted(seed_chains.values(), key=lambda x: -x[1])]
        final = seed_list + wavefront_chains

        capsule = self._get_capsule(anchor_id, final)

        return {
            'status': 'activation_inference',
            'chains': final[:max_chains],
            'query': query,
            'capsule': capsule,
        }

    # Maps canonical concept type names → capsule ID
    _TYPE_TO_CAPSULE = {
        "person":           "biographical_response",
        "historical_figure":"biographical_response",
        "scientist":        "biographical_response",
        "philosopher":      "biographical_response",
        "artist":           "biographical_response",
        "process":          "mechanistic_response",
        "mechanism":        "mechanistic_response",
        "reaction":         "mechanistic_response",
        "system":           "mechanistic_response",
        "function":         "mechanistic_response",
        "concept":          "definitional_response",
        "theory":           "definitional_response",
        "field":            "definitional_response",
        "substance":        "definitional_response",
        "molecule":         "definitional_response",
        "polymer":          "definitional_response",
        "compound":         "definitional_response",
        "organic matter":   "definitional_response",
        "naturalist":       "biographical_response",
        "biologist":        "biographical_response",
        "physician":        "biographical_response",
        "chemist":          "biographical_response",
        "mathematician":    "biographical_response",
        "organism":         "mechanistic_response",
        "reaction":         "mechanistic_response",
        "enzyme":           "mechanistic_response",
        "protein":          "mechanistic_response",
    }

    _MECHANISTIC_PREDS = {"causes", "enables", "produces", "requires",
                          "regulates", "inhibits", "activates", "uses",
                          "used_by", "used_for", "prevents", "triggers"}
    _DEFINITIONAL_PREDS = {"is_a", "also_known_as", "defined_as",
                           "subtype_of", "instance_of"}
    _COMPOSITIONAL_PREDS = {"contains", "part_of", "composed_of", "component_of"}

    def _get_capsule(self, anchor_id: str, chains: list) -> str | None:
        """
        Select response capsule from predicate distribution in activation chains.
        Predicates are the cleanest signal — mirrors LangEng intent detection.
        """
        if not chains:
            return self._fetch_capsule_fragment("sparse_response")

        # Count predicates across all chains
        preds = Counter()
        for chain in chains:
            parts = chain.split(" | ")
            if len(parts) >= 2:
                preds[parts[1].strip()] += 1

        pred_set = set(preds.keys())

        if pred_set & self._MECHANISTIC_PREDS:
            capsule_id = "mechanistic_response"
        elif pred_set & self._DEFINITIONAL_PREDS:
            capsule_id = "definitional_response"
        elif pred_set & self._COMPOSITIONAL_PREDS:
            capsule_id = "definitional_response"
        elif len(chains) < 5:
            capsule_id = "sparse_response"
        else:
            capsule_id = "relational_response"

        return self._fetch_capsule_fragment(capsule_id)

    def _fetch_capsule_fragment(self, capsule_id: str) -> str | None:
        """Retrieve attractor body and increment reinforcement counter."""
        try:
            db = sqlite3.connect(CMS_PATH)
            frag = db.execute("""
                SELECT f.text FROM fragments f
                JOIN fragment_links fl ON fl.fragment_id = f.id
                WHERE fl.anchor_id = ? AND fl.relation = 'attractor_body'
                LIMIT 1
            """, (capsule_id,)).fetchone()
            db.close()

            if frag:
                db2 = sqlite3.connect(CMS_PATH)
                db2.execute("""
                    UPDATE relations_aggregated
                    SET seen_count = seen_count + 1
                    WHERE subject_id = ? AND predicate = 'is_a'
                    AND object_id = 'response_pattern' AND edge_type = 'meta'
                """, (capsule_id,))
                db2.commit()
                db2.close()
                return f"[{capsule_id}] {frag[0]}"
        except Exception:
            pass
        return None
