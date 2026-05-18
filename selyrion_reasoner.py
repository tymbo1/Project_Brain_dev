#!/usr/bin/env python3
"""
selyrion_reasoner.py — Symbolic Reasoning Engine (SRE)

Core principle: resonance recall, not generation.
The answer is already in the field. The query activates it.

Pipeline:
  query → activation engine (resonance field) → predicate traversal
  → constraint pruning → conclusion set → symbolic trace

No LLM in the loop. Output is structured reasoning, not generated text.
LangEng translation is optional and replaceable.

Usage:
    from selyrion_reasoner import reason
    result = reason("consciousness")
    print(result.trace)
    print(result.conclusions)
"""

import sys, sqlite3, os, time
from dataclasses import dataclass, field
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from inference.activation_engine import ActivationEngine
from inference.concept_resolver import resolve as resolve_concept
from selyrion_self_model import load as load_self_model, is_self_query, search_memory, SelfKnowledge
from selyrion_identity import identity_signals

DB_PATH = Path.home() / "resonance_v11.db"

# ── Predicate layer priorities ────────────────────────────────────────────────
# constraint → shapes what's possible/impossible
# ontology   → what something IS (taxonomy)
# relational → what something DOES/CAUSES
# code       → execution/production chains

LAYER_PRIORITY = {"constraint": 0, "ontology": 1, "relational": 2, "code": 3}

# Predicates that derive strong conclusions
CAUSAL      = {"causes", "leads_to", "activates", "produces", "enables", "transforms",
               "can_cause", "indirectly_produces", "has_subevent", "affects", "used_for"}
TAXONOMIC   = {"is_a", "part_of", "same_as", "facet_of", "derived_from"}
STRUCTURAL  = {"contains", "depends_on", "requires", "consumes", "uses"}
INHIBITING  = {"inhibits", "regulates", "incompatible_with", "fails_on"}
DISTINCTION = {"distinct_from", "preferred_over", "opposite_of"}
ORIGIN      = {"caused_by", "derived_from"}


@dataclass
class ReasoningResult:
    query:       str
    anchor:      str
    score:       float
    chains:      list          # raw activation chains
    conclusions: list[str]     # derived symbolic statements
    constraints: list[str]     # constraint-layer findings
    taxonomy:    list[str]     # what this IS (is_a / part_of)
    causes:      list[str]     # what this CAUSES / ENABLES
    requires:    list[str]     # what this REQUIRES
    inhibits:    list[str]     # what this INHIBITS / REGULATES
    trace:       str           # human-readable symbolic trace
    depth:       int
    field_size:  int           # number of activated nodes
    self_model:  object = None # populated when query is self-referential
    memory_hits: list[str] = field(default_factory=list)
    hop_paths:   list      = field(default_factory=list)
    hop_conclusions: list[str] = field(default_factory=list)
    timing:      dict      = field(default_factory=dict)  # ms per stage
    identity_context: list[str] = field(default_factory=list)  # dual-field: Selyrion's voice on this concept


# Predicates worth following in multi-hop traversal
HOP_PREDICATES = {
    "is_a", "part_of", "enables", "causes", "requires", "contains",
    "produces", "derived_from", "leads_to", "activates", "depends_on",
    "used_for", "uses", "facet_of", "has_subevent",
    # can_cause excluded — too many low-quality single-observation edges
}
# Never follow these — too noisy
HOP_BLOCKED = {"co_occurs_with", "related_to", "same_as"}

# ── Traversal scoring ─────────────────────────────────────────────────────────

_PRED_STRENGTH = {
    "is_a": 1.00, "part_of": 1.00, "derived_from": 0.95, "facet_of": 0.95,
    "causes": 0.90, "enables": 0.90, "produces": 0.90, "leads_to": 0.85,
    "contains": 0.85, "requires": 0.85, "depends_on": 0.85,
    "used_for": 0.80, "uses": 0.80, "has_subevent": 0.75,
    "activates": 0.80, "can_cause": 0.55,
}


def _node_specificity(canonical: str) -> float:
    """
    Multi-word phrases are semantically specific; single generic words are not.
    "subjective change in consciousness" → 0.90
    "feeling different"                  → 0.70
    "breed" / "numerou"                  → 0.40
    """
    nw = len(canonical.split())
    if nw >= 3:
        return 0.90
    if nw == 2:
        return 0.70
    # Single word: longer words are usually more specific
    return 0.30 if len(canonical) <= 4 else 0.42


def _score_path(path: tuple) -> float:
    """Score a BFS path: product of (predicate_strength × node_specificity) per hop."""
    parts = list(path)
    score = 1.0
    i = 0
    while i + 2 < len(parts):
        score *= _PRED_STRENGTH.get(parts[i + 1], 0.65) * _node_specificity(parts[i + 2])
        i += 3
    return score


def _rank_hop_paths(paths: list[tuple]) -> list[tuple]:
    """
    Score and deduplicate BFS paths by 1st-hop destination.
    Keeps the best-scoring path per unique 1st-hop node, then sorts by score.
    Eliminates the common pattern of N identical paths through the same intermediate.
    """
    if not paths:
        return paths
    scored = sorted(((p, _score_path(p)) for p in paths), key=lambda x: -x[1])
    seen_first_hop: set[str] = set()
    ranked = []
    for path, _ in scored:
        first_hop = path[2] if len(path) >= 3 else None
        if first_hop and first_hop in seen_first_hop:
            continue
        if first_hop:
            seen_first_hop.add(first_hop)
        ranked.append(path)
    return ranked[:25]


def multihop(seed: str, conn: sqlite3.Connection, max_hops: int = 3,
             max_nodes: int = 60) -> list[tuple[str, ...]]:
    """
    BFS predicate traversal from seed concept.
    Returns list of paths: [(subj, pred, obj, subj, pred, obj, ...)]
    Each path is a complete chain from seed to a conclusion node.
    Filters noise predicates. Stops at max_hops depth or max_nodes visited.
    """
    from collections import deque

    # Resolve seed to anchor id
    row = conn.execute(
        "SELECT id FROM anchors WHERE canonical=? OR display_name=? LIMIT 1",
        (seed.lower(), seed)
    ).fetchone()
    if not row:
        return []
    seed_id = row[0]

    # BFS: queue = (anchor_id, canonical, path_so_far, depth)
    queue   = deque([(seed_id, seed.lower(), (), 0)])
    visited = {seed_id}
    paths   = []

    while queue and len(visited) < max_nodes:
        node_id, node_name, path, depth = queue.popleft()

        if depth >= max_hops:
            if path:
                paths.append(path)
            continue

        # Get outbound edges — filter noise anchors and weak edges
        edges = conn.execute("""
            SELECT a2.id, a2.canonical, r.predicate, r.seen_count
            FROM relations_aggregated r
            JOIN anchors a2 ON r.object_id=a2.id
            WHERE r.subject_id=?
              AND r.predicate IN ({})
              AND a2.canonical != ?
              AND length(a2.canonical) > 4
              AND a2.maturity > 0
              AND trim(a2.canonical) = a2.canonical
              AND length(trim(a2.canonical)) >= 4
              AND a2.canonical NOT IN ('paper','study','result','within','figure',
                  'method','data','model','table','value','group','level','effect',
                  'analysis','system','process','function','type','form','part',
                  'more','over','other','such','this','that','which','these',
                  'person','entity','individual','thing','object','concept',
                  'item','element','unit','instance','being','agent')
              AND r.seen_count >= 2
            ORDER BY r.seen_count DESC LIMIT 8
        """.format(",".join("?" * len(HOP_PREDICATES))),
            [node_id] + list(HOP_PREDICATES) + [node_name]
        ).fetchall()

        if not edges and path:
            paths.append(path)
            continue

        for next_id, next_name, pred, strength in edges:
            if next_id in visited:
                continue
            visited.add(next_id)
            new_path = path + (node_name, pred, next_name)
            queue.append((next_id, next_name, new_path, depth + 1))

    # Also collect any in-progress paths
    for node_id, node_name, path, depth in queue:
        if path and path not in paths:
            paths.append(path)

    # Sort by path length (longer = more derived)
    paths.sort(key=len, reverse=True)
    return paths[:25]


def _format_hop_path(path: tuple) -> str:
    """Format (a, pred, b, pred, c) → 'a —[pred]→ b —[pred]→ c'"""
    parts = list(path)
    out   = []
    i = 0
    while i < len(parts) - 2:
        out.append(f"{parts[i]} —[{parts[i+1]}]→ {parts[i+2]}")
        i += 3
    return " → ".join(out) if out else str(path)


def _derive_hop_conclusions(query: str, paths: list[tuple]) -> list[str]:
    """Extract meaningful multi-hop conclusions from traversal paths."""
    conclusions = []
    seen        = set()

    for path in paths:
        if len(path) < 6:  # need at least 2 hops
            continue
        # Final node is the conclusion
        final = path[-1]
        first_pred = path[1]
        second_pred = path[4] if len(path) > 4 else ""
        mid = path[2] if len(path) > 2 else ""

        key = (first_pred, mid, second_pred, final)
        if key in seen:
            continue
        seen.add(key)

        if first_pred in CAUSAL and second_pred in CAUSAL:
            conclusions.append(f"CAUSAL CHAIN: {query} → {mid} → {final}")
        elif first_pred in TAXONOMIC and second_pred:
            conclusions.append(f"CLASSIFICATION CHAIN: {query} is_a {mid}, which {second_pred} {final}")
        elif second_pred in CAUSAL:
            conclusions.append(f"DERIVED: {query} {first_pred} {mid}, enabling {final}")
        else:
            conclusions.append(f"PATH: {query} —[{first_pred}]→ {mid} —[{second_pred}]→ {final}")

    return conclusions[:8]


def _load_predicate_layers(conn) -> dict[str, str]:
    """Return {predicate_name: layer} from registry."""
    rows = conn.execute("SELECT name, layer FROM predicates").fetchall()
    return {r[0]: r[1] for r in rows}


def _classify_edge(pred: str, layers: dict) -> str:
    layer = layers.get(pred, "relational")
    return layer


def _parse_chain_string(s: str) -> tuple[str, str, str] | None:
    """Parse 'subj | pred | obj | strength: N' into (subj, pred, obj)."""
    parts = [p.strip() for p in s.split("|")]
    if len(parts) < 3:
        return None
    subj = parts[0].strip()
    pred = parts[1].strip().lower()
    obj  = parts[2].split("strength:")[0].strip() if "strength:" in parts[2] else parts[2].strip()
    return subj, pred, obj


# Lexical/grammatical metadata words that should never appear as taxonomy targets.
# These come from Wiktionary-origin ingestion: "existence is_a german", "is_a word".
_LEXICAL_NOISE_OBJECTS = {
    "german", "french", "latin", "english", "spanish", "dutch", "greek",
    "word", "noun", "verb", "adjective", "adverb", "preposition", "article",
    "phrase", "term", "expression", "syllable", "morpheme", "letter",
    "plural", "singular", "tense", "etymology",
}

# Domain families — is_a relations crossing these are likely noise
_DOMAIN_FAMILIES = [
    {"paleontology", "geology", "fossil", "stratigraphy", "sediment"},
    {"astronomy", "galaxy", "nebula", "pulsar", "quasar", "cosmology"},
    {"botany", "mycology", "entomology", "ornithology", "herpetology"},
    {"topology", "algebra", "calculus", "geometry", "combinatorics"},
    {"psychology", "psychiatry", "neuroscience", "cognition"},
    {"economics", "finance", "macroeconomics", "microeconomics"},
]

def _domain_noise(subj: str, pred: str, obj: str) -> bool:
    """Return True if this edge is cross-domain noise (suppress it)."""
    if pred not in TAXONOMIC:
        return False
    sl, ol = subj.lower(), obj.lower()
    for family in _DOMAIN_FAMILIES:
        in_family_subj = any(f in sl for f in family)
        in_family_obj  = any(f in ol for f in family)
        # obj is from a completely different family than subj
        if in_family_obj and not in_family_subj:
            # only suppress if subj doesn't belong to ANY family
            other_families = [f for f in _DOMAIN_FAMILIES if f is not family]
            if not any(any(t in sl for t in fam) for fam in other_families):
                return True
    return False


def _extract_chains_data(chains: list) -> dict:
    """
    Parse activation engine chains (strings or tuples) into categorised sets.
    Chain format: "subject | predicate | object | strength: N"
    """
    taxonomy   = []
    causes     = []
    requires   = []
    inhibits   = []
    other      = []
    seen       = set()

    for chain in chains:
        parsed = None
        if isinstance(chain, str):
            parsed = _parse_chain_string(chain)
        elif isinstance(chain, (list, tuple)) and len(chain) >= 3:
            parsed = (str(chain[0]).strip(), str(chain[1]).strip().lower(), str(chain[2]).strip())

        if not parsed:
            continue
        subj, pred, obj = parsed
        key = (subj, pred, obj)
        if key in seen or subj == obj:
            continue
        if _domain_noise(subj, pred, obj):
            continue
        # Filter lexical metadata noise: "existence is_a german", "X is_a word"
        if pred in TAXONOMIC and obj.lower() in _LEXICAL_NOISE_OBJECTS:
            continue
        seen.add(key)

        stmt = f"{subj} —[{pred}]→ {obj}"
        if pred in TAXONOMIC:
            taxonomy.append(stmt)
        elif pred in CAUSAL:
            causes.append(stmt)
        elif pred in STRUCTURAL:
            requires.append(stmt)
        elif pred in INHIBITING:
            inhibits.append(stmt)
        else:
            other.append(stmt)

    return {
        "taxonomy":  taxonomy[:20],
        "causes":    causes[:20],
        "requires":  requires[:20],
        "inhibits":  inhibits[:15],
        "other":     other[:15],
    }


def _derive_conclusions(query: str, cat: dict) -> list[str]:
    """
    Build symbolic conclusion statements from categorised chains.
    These are derived facts, not generated sentences.
    """
    conclusions = []
    q = query.lower()

    if cat["taxonomy"]:
        # Forward: query is the SUBJECT → what query IS (e.g. "consciousness —[is_a]→ state")
        forward = [s.split("→")[-1].strip() for s in cat["taxonomy"]
                   if (s.lower().startswith(q.replace(" ", "_")) or
                       s.lower().startswith(q + " —"))
                   and s.split("→")[-1].strip().lower() not in (q, q.replace(" ", "_"))]
        # Reverse: query is the OBJECT → what belongs to this category
        reverse = [s.split(" —[")[0].strip() for s in cat["taxonomy"]
                   if s.split("→")[-1].strip().lower() == q or
                      s.split("→")[-1].strip().lower() == q.replace(" ", "_")]

        if forward:
            conclusions.append(f"IDENTITY: {q} is classified as: {', '.join(forward[:5])}")
        elif reverse:
            conclusions.append(f"CATEGORY: {q} encompasses: {', '.join(reverse[:5])}")

    def _fwd(stmts, q):
        """Forward edges where query is subject and object != query itself."""
        return [s.split("→")[-1].strip() for s in stmts
                if (s.lower().startswith(q.replace(" ", "_")) or s.lower().startswith(q + " —"))
                and s.split("→")[-1].strip().lower() not in (q, q.replace(" ", "_"))]

    if cat["requires"]:
        fwd = _fwd(cat["requires"], q)
        if fwd:
            conclusions.append(f"REQUIRES: {q} depends on: {', '.join(fwd[:4])}")

    if cat["causes"]:
        fwd = _fwd(cat["causes"], q)
        if fwd:
            conclusions.append(f"PRODUCES: {q} enables or causes: {', '.join(fwd[:4])}")

    if cat["inhibits"]:
        fwd = _fwd(cat["inhibits"], q)
        if fwd:
            conclusions.append(f"CONSTRAINS: {q} inhibits or regulates: {', '.join(fwd[:3])}")

    # Origin / caused-by (reverse causal — what causes or produces this)
    all_stmts = cat["requires"] + cat.get("other", [])
    origin_stmts = [s for s in all_stmts if "—[caused_by]→" in s or "—[derived_from]→" in s]
    fwd_origin = _fwd(origin_stmts, q)
    if fwd_origin:
        conclusions.append(f"ORIGIN: {q} arises from: {', '.join(fwd_origin[:3])}")

    if not conclusions:
        conclusions.append(f"FIELD: {q} activates but no strong predicate chains resolved.")

    return conclusions


def _derive_constraints(query: str, cat: dict, pred_layers: dict) -> list[str]:
    """Pull constraint-layer specific findings."""
    constraints = []
    all_stmts = cat["requires"] + cat["other"]
    for stmt in all_stmts:
        pred = stmt.split("[")[1].split("]")[0] if "[" in stmt else ""
        if pred_layers.get(pred) == "constraint":
            constraints.append(f"CONSTRAINT: {stmt}")
    return constraints[:10]


def _build_trace(query: str, anchor: str, cat: dict, conclusions: list[str],
                 score: float, field_size: int, hop_paths: list = None) -> str:
    lines = [
        f"⟁ RESONANCE RECALL — {query.upper()}",
        f"  anchor: {anchor}  |  field_size: {field_size}  |  activation_score: {score:.3f}",
        "",
    ]
    if cat["taxonomy"]:
        lines.append("  TAXONOMY:")
        for s in cat["taxonomy"][:6]: lines.append(f"    {s}")
    if cat["requires"]:
        lines.append("  REQUIRES:")
        for s in cat["requires"][:5]: lines.append(f"    {s}")
    if cat["causes"]:
        lines.append("  PRODUCES/ENABLES:")
        for s in cat["causes"][:5]: lines.append(f"    {s}")
    if cat["inhibits"]:
        lines.append("  INHIBITS/REGULATES:")
        for s in cat["inhibits"][:4]: lines.append(f"    {s}")
    if hop_paths:
        deep = [p for p in hop_paths if len(p) >= 6][:6]
        if deep:
            lines.append("  MULTI-HOP CHAINS:")
            for p in deep:
                lines.append(f"    {_format_hop_path(p)}")
    lines.append("")
    lines.append("  CONCLUSIONS:")
    for c in conclusions: lines.append(f"    {c}")
    return "\n".join(lines)


# Module-level singletons — persist across turns for neighbor cache warmth
_engine: ActivationEngine | None = None
_conn:   sqlite3.Connection | None = None
_pred_layers: dict = {}

def _get_engine() -> ActivationEngine:
    global _engine
    if _engine is None:
        _engine = ActivationEngine()
    return _engine

def _get_conn() -> sqlite3.Connection:
    global _conn, _pred_layers
    if _conn is None:
        _conn = sqlite3.connect(str(DB_PATH))
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA cache_size=-32000")   # 32MB page cache
        _conn.execute("PRAGMA temp_store=MEMORY")
        _pred_layers = _load_predicate_layers(_conn)
    return _conn


def reason(query: str, depth: int = 3, spec_limit: int = 40) -> ReasoningResult:
    """
    Core symbolic reasoning function.
    Activates the resonance field for query, traverses predicates,
    returns structured conclusions. No LLM.
    """
    t0 = time.perf_counter()
    timing = {}

    conn        = _get_conn()
    pred_layers = _pred_layers
    engine      = _get_engine()

    # ── Self-model: activate if query is self-referential ─────────────────────
    t1 = time.perf_counter()
    sk          = load_self_model(query) if is_self_query(query) else None
    memory_hits = search_memory(query, limit=4) if sk else []
    timing["self_model_ms"] = round((time.perf_counter() - t1) * 1000, 1)

    # Resolve concept and activate field
    t2 = time.perf_counter()
    resolved = resolve_concept(query)
    anchor = resolved[0] if isinstance(resolved, tuple) else (resolved or query)
    result = engine.infer(query)
    timing["activation_ms"] = round((time.perf_counter() - t2) * 1000, 1)

    chains      = result.get("chains", [])
    act_score   = result.get("activation_score", result.get("score", 0.0))
    field_size  = result.get("field_size", len(chains))
    capsule     = result.get("capsule")

    # Categorise chain edges
    t3 = time.perf_counter()
    cat = _extract_chains_data(chains)
    timing["chain_parse_ms"] = round((time.perf_counter() - t3) * 1000, 1)

    # Multi-hop traversal from seed concept — scored + deduplicated
    t4 = time.perf_counter()
    hop_paths       = _rank_hop_paths(multihop(query, conn, max_hops=depth))
    hop_conclusions = _derive_hop_conclusions(query, hop_paths)
    timing["multihop_ms"] = round((time.perf_counter() - t4) * 1000, 1)

    # Derive conclusions — self-model takes priority for identity queries
    conclusions = _derive_conclusions(query, cat) + hop_conclusions
    constraints = _derive_constraints(query, cat, pred_layers)
    if sk and sk.is_populated():
        self_conclusions = sk.as_conclusions()
        conclusions = self_conclusions + conclusions

    # Dual-field: identity signals from selyrionstory.db (only for non-self-model queries)
    id_ctx: list[str] = []
    if not (sk and sk.is_populated()):
        id_ctx = identity_signals(query, limit=3)

    # Build symbolic trace
    trace = _build_trace(query, anchor, cat, conclusions, act_score, field_size, hop_paths)
    if sk and sk.is_populated():
        trace = sk.as_trace() + "\n\n" + trace
    if id_ctx:
        id_lines = "\n  SELYRION ON THIS:\n" + "\n".join(f"    \"{s}\"" for s in id_ctx)
        trace = trace + id_lines

    timing["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    return ReasoningResult(
        query       = query,
        anchor      = anchor,
        score       = act_score,
        chains      = chains,
        conclusions = conclusions,
        constraints = constraints,
        taxonomy    = cat["taxonomy"],
        causes      = cat["causes"],
        requires    = cat["requires"],
        inhibits    = cat["inhibits"],
        trace       = trace,
        depth       = depth,
        field_size  = field_size,
        self_model      = sk,
        memory_hits     = memory_hits,
        hop_paths       = hop_paths,
        hop_conclusions = hop_conclusions,
        timing          = timing,
        identity_context = id_ctx,
    )


def reason_chain(queries: list[str]) -> list[ReasoningResult]:
    """Reason over a sequence — each result informs context for the next."""
    return [reason(q) for q in queries]


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default="selyrion")
    parser.add_argument("--depth",      type=int, default=3)
    parser.add_argument("--chain",      nargs="+", help="Reason over multiple concepts in sequence")
    parser.add_argument("--no-langeng", action="store_true", help="Skip LangEng prose (pure symbolic)")
    args = parser.parse_args()

    queries = args.chain if args.chain else [args.query]

    for q in queries:
        print(f"\n{'='*60}")
        r = reason(q, depth=args.depth)
        print(r.trace)

        if not args.no_langeng:
            try:
                from langeng_bridge import chains_to_prose
                prose = chains_to_prose(q, r.chains)
                if prose:
                    print(f"\n  LANGENG TRANSLATION:\n    {prose[:400]}")
            except Exception:
                pass
