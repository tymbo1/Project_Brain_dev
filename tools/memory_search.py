"""tools/memory_search.py — CMS semantic retrieval tool."""
import sys, sqlite3, json, subprocess, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scos_tools import register_tool

CMS_DB = Path.home() / "resonance_v11.db"

# Words that are task verbs / intent framing, not CMS concepts
_INTENT_NOISE = {
    "pick", "picks", "picked", "picking",
    "find", "finds", "found", "finding",
    "show", "shows", "list", "lists", "give", "gives",
    "make", "makes", "advance", "improve", "upgrade",
    "enhance", "things", "github", "online", "thing",
    "from", "with", "into", "your", "that", "this",
    "using", "about", "what", "which", "where", "when",
    "have", "need", "want", "like", "know", "tell",
}


def _extract_concepts_llm(query: str) -> list[str]:
    """
    Use qwen3:4b to extract 3-6 concrete searchable concepts from a natural-language query.
    Returns raw tokens on failure so the pipeline degrades gracefully.
    """
    ollama = shutil.which("ollama")
    if not ollama:
        return []

    prompt = (
        "Extract 3-6 concrete knowledge concepts from this query for a knowledge graph search. "
        "Return ONLY a JSON array of short noun phrases (2-3 words max each). "
        "No verbs, no task framing, no explanation.\n\n"
        f"Query: {query}\n\n"
        "Example output: [\"machine learning\", \"neural network\", \"reinforcement learning\"]"
    )
    try:
        proc = subprocess.run(
            [ollama, "run", "qwen3:4b", prompt],
            capture_output=True, text=True, timeout=30
        )
        text = proc.stdout.strip()
        # Find JSON array in output
        start = text.find("[")
        end   = text.rfind("]") + 1
        if start >= 0 and end > start:
            concepts = json.loads(text[start:end])
            return [c.lower().strip() for c in concepts if isinstance(c, str) and len(c) > 2]
    except Exception:
        pass
    return []


def _anchor_lookup(conn, token: str, limit: int = 8) -> list:
    """Find anchor rows matching a token, preferring exact prefix over substring."""
    # Prefer anchors that start with the token (more precise)
    rows = conn.execute(
        "SELECT id, canonical, maturity FROM anchors WHERE canonical LIKE ? ORDER BY maturity DESC LIMIT ?",
        (f"{token}%", limit)
    ).fetchall()
    if len(rows) < 3:
        # Supplement with substring matches
        sub = conn.execute(
            "SELECT id, canonical, maturity FROM anchors WHERE canonical LIKE ? ORDER BY maturity DESC LIMIT ?",
            (f"%{token}%", limit)
        ).fetchall()
        seen = {r[0] for r in rows}
        rows += [r for r in sub if r[0] not in seen]
    return rows[:limit]


@register_tool(
    name="memory_search",
    description="Search Selyrion's CMS for anchors and relations on a topic",
    input_schema={
        "query":  {"type": "str", "required": True},
        "domain": {"type": "str", "required": False},
        "limit":  {"type": "int", "required": False},
    }
)
def memory_search(inputs):
    query  = inputs["query"].lower()
    limit  = inputs.get("limit", 10)
    domain = inputs.get("domain", "")

    try:
        conn = sqlite3.connect(CMS_DB, timeout=5)
        conn.execute("PRAGMA query_only=1")

        # ── Step 1: build search tokens ───────────────────────────────────────
        # Try LLM concept extraction first; fall back to filtered tokens
        concepts = _extract_concepts_llm(query)
        if concepts:
            search_tokens = concepts
        else:
            search_tokens = [
                w for w in query.split()
                if len(w) > 3 and w not in _INTENT_NOISE
            ][:6]

        # ── Step 2: anchor lookup per token ───────────────────────────────────
        anchor_rows = []
        seen_ids    = set()
        for token in search_tokens:
            for r in _anchor_lookup(conn, token):
                if r[0] not in seen_ids:
                    anchor_rows.append(r)
                    seen_ids.add(r[0])

        anchor_ids  = [r[0] for r in anchor_rows]
        anchors_out = [{"concept": r[1], "maturity": r[2]} for r in anchor_rows[:8]]
        relations   = []

        # ── Step 3a: relations from matched anchors ────────────────────────────
        if anchor_ids:
            placeholders  = ",".join("?" * len(anchor_ids))
            domain_clause = "AND r.domain_tags LIKE ?" if domain else ""
            params        = anchor_ids + anchor_ids
            if domain:
                params.append(f"%{domain}%")
            params.append(limit)
            rows = conn.execute(f"""
                SELECT a1.canonical, r.predicate, a2.canonical, r.confidence, r.seen_count
                FROM relations_aggregated r
                JOIN anchors a1 ON r.subject_id = a1.id
                JOIN anchors a2 ON r.object_id  = a2.id
                WHERE (r.subject_id IN ({placeholders}) OR r.object_id IN ({placeholders}))
                {domain_clause}
                ORDER BY r.seen_count DESC, r.confidence DESC LIMIT ?
            """, params).fetchall()
            relations = [{"subject": r[0], "predicate": r[1], "object": r[2],
                          "confidence": r[3], "seen": r[4]} for r in rows]

        # ── Step 3b: fallback — top domain relations if still empty ───────────
        if not relations and domain:
            rows = conn.execute("""
                SELECT a1.canonical, r.predicate, a2.canonical, r.confidence, r.seen_count
                FROM relations_aggregated r
                JOIN anchors a1 ON r.subject_id = a1.id
                JOIN anchors a2 ON r.object_id  = a2.id
                WHERE r.domain_tags LIKE ?
                ORDER BY r.seen_count DESC, r.confidence DESC LIMIT ?
            """, (f"%{domain}%", limit)).fetchall()
            relations = [{"subject": r[0], "predicate": r[1], "object": r[2],
                          "confidence": r[3], "seen": r[4]} for r in rows]

        conn.close()
        return {
            "status":         "success",
            "query":          query,
            "search_tokens":  search_tokens,
            "relations":      relations,
            "anchors":        anchors_out,
            "count":          len(relations),
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "relations": [], "anchors": []}
