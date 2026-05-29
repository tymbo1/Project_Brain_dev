"""
tools/knowledge_ops.py — SCOS-specific knowledge tools with no Claude equivalent.

These are unique to Selyrion's architecture:
  cms_knowledge_check  — pre-task CMS query (wraps codeops/cms_query)
  gap_search           — knowledge-gap-driven web/local search
  contradiction_check  — detect contradictory relations in CMS
  confidence_estimate  — estimate Selyrion's confidence on a topic
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scos_tools import register_tool


# ── cms_knowledge_check ───────────────────────────────────────────────────────

@register_tool(
    "cms_knowledge_check",
    "Check whether Selyrion already has CMS knowledge sufficient to solve a task. Returns confidence score, relevant anchors, and context for LLM injection. Call this BEFORE attempting any task.",
    {
        "problem":      {"type": "string", "required": True,  "desc": "Natural language description of the task or problem"},
        "error_class":  {"type": "string", "required": False, "desc": "Error class if this is a fix task (e.g. 'syntax', 'runtime')"},
        "subtype":      {"type": "string", "required": False, "desc": "Error subtype (e.g. 'missing_module')"},
        "domain_hint":  {"type": "string", "required": False, "desc": "CMS domain to scope search (e.g. 'computer_science')"},
    }
)
def cms_knowledge_check(inputs: dict) -> dict:
    try:
        from codeops import cms_query
        return cms_query.check(
            problem=inputs["problem"],
            error_class=inputs.get("error_class", ""),
            subtype=inputs.get("subtype", ""),
            domain_hint=inputs.get("domain_hint", ""),
        )
    except Exception as e:
        return {"status": "error", "error": str(e),
                "has_knowledge": False, "confidence": 0.0}


# ── gap_search ────────────────────────────────────────────────────────────────

@register_tool(
    "gap_search",
    "Search for knowledge or code that Selyrion is missing. Scans local files, conversation history, and web. Ingests findings into selyrioncode.db. Use when cms_knowledge_check returns low confidence.",
    {
        "concept":       {"type": "string", "required": True,  "desc": "The concept or capability to search for"},
        "call_patterns": {"type": "array",  "required": False, "desc": "Code patterns to look for e.g. ['def solve', 'import solver']"},
        "synonyms":      {"type": "array",  "required": False, "desc": "Alternative terms for the concept"},
        "layers":        {"type": "array",  "required": False, "desc": "Which layers to search: local|conversations|web|github (default: all)"},
    }
)
def gap_search(inputs: dict) -> dict:
    try:
        from selyrion_gap_search import search_gap
        layers = inputs.get("layers", ["local", "conversations", "web"])
        result = search_gap(
            gap_concept=inputs["concept"],
            call_patterns=inputs.get("call_patterns", []),
            synonyms=inputs.get("synonyms", []),
            layers=layers,
        )
        return {"status": "success", "concept": inputs["concept"],
                "found": result, "layers_searched": layers}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── contradiction_check ───────────────────────────────────────────────────────

@register_tool(
    "contradiction_check",
    "Detect contradictory relations in CMS for a given concept — e.g. X enables Y and X inhibits Y simultaneously. Returns contradiction pairs for HITL review.",
    {
        "concept":  {"type": "string",  "required": True,  "desc": "Concept/anchor to check for contradictions"},
        "domain":   {"type": "string",  "required": False, "desc": "Restrict to this domain"},
        "limit":    {"type": "integer", "required": False, "desc": "Max relation pairs to check (default 50)"},
    }
)
def contradiction_check(inputs: dict) -> dict:
    import sqlite3
    DB = Path.home() / "resonance_v11.db"
    if not DB.exists():
        return {"status": "error", "error": "CMS not available"}

    concept = inputs["concept"].lower().replace(" ", "_")
    limit   = int(inputs.get("limit", 50))

    OPPOSITES = {
        "enables":    {"inhibits", "prevents", "blocks", "disables"},
        "inhibits":   {"enables", "promotes", "causes"},
        "causes":     {"prevents", "inhibits"},
        "requires":   {"incompatible_with"},
        "is_a":       {"distinct_from", "opposite_of"},
        "similar_to": {"opposite_of", "distinct_from"},
    }

    try:
        conn = sqlite3.connect(DB)
        # Find anchor
        row = conn.execute(
            "SELECT id FROM anchors WHERE canonical LIKE ? LIMIT 1",
            (f"%{concept}%",)
        ).fetchone()
        if not row:
            conn.close()
            return {"status": "not_found", "concept": concept}

        aid = row[0]
        rels = conn.execute("""
            SELECT predicate, object_id, a.canonical, confidence
            FROM relations_aggregated r
            LEFT JOIN anchors a ON a.id = r.object_id
            WHERE r.subject_id = ?
            ORDER BY confidence DESC LIMIT ?
        """, (aid, limit)).fetchall()
        conn.close()

        # Find contradictions
        pred_objects: dict[str, set] = {}
        for pred, oid, oname, conf in rels:
            pred_objects.setdefault(pred, set()).add(oname or oid)

        contradictions = []
        for pred, objects in pred_objects.items():
            opposites = OPPOSITES.get(pred, set())
            for opp in opposites:
                if opp in pred_objects:
                    shared = objects & pred_objects[opp]
                    if shared:
                        contradictions.append({
                            "pred_a": pred, "pred_b": opp,
                            "shared_objects": list(shared),
                        })
                    else:
                        contradictions.append({
                            "pred_a": pred, "objects_a": list(objects)[:5],
                            "pred_b": opp,  "objects_b": list(pred_objects[opp])[:5],
                            "type": "opposing_predicates_on_same_subject",
                        })

        return {
            "status":         "success",
            "concept":        concept,
            "relations_checked": len(rels),
            "contradictions": contradictions,
            "clean":          len(contradictions) == 0,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── confidence_estimate ───────────────────────────────────────────────────────

@register_tool(
    "confidence_estimate",
    "Estimate Selyrion's epistemic confidence on a topic based on CMS density, maturity, and relation coherence. Returns a 0-1 score with breakdown.",
    {
        "topic":   {"type": "string", "required": True,  "desc": "Topic or concept to assess"},
        "domain":  {"type": "string", "required": False, "desc": "CMS domain to restrict search"},
    }
)
def confidence_estimate(inputs: dict) -> dict:
    import sqlite3
    DB = Path.home() / "resonance_v11.db"
    if not DB.exists():
        return {"status": "error", "error": "CMS not available"}

    topic  = inputs["topic"].lower().replace(" ", "_")
    domain = inputs.get("domain", "")

    try:
        conn = sqlite3.connect(DB)

        # Anchor presence + maturity
        q = "SELECT id, maturity, relation_count FROM anchors WHERE canonical LIKE ?"
        args = [f"%{topic}%"]
        if domain:
            q += " AND domain_tags LIKE ?"
            args.append(f"%{domain}%")
        q += " ORDER BY maturity DESC LIMIT 10"
        anchors = conn.execute(q, args).fetchall()

        if not anchors:
            conn.close()
            return {"status": "success", "topic": topic, "confidence": 0.0,
                    "breakdown": {"anchor_score": 0, "maturity_score": 0, "density_score": 0},
                    "verdict": "no CMS knowledge on this topic"}

        anchor_score  = min(len(anchors) / 5.0, 1.0)
        avg_maturity  = sum(a[1] for a in anchors) / len(anchors)
        maturity_score = min(avg_maturity / 100.0, 1.0)
        avg_rels      = sum(a[2] for a in anchors) / len(anchors)
        density_score  = min(avg_rels / 20.0, 1.0)

        confidence = round(anchor_score * 0.3 + maturity_score * 0.4 + density_score * 0.3, 3)

        verdict = (
            "strong knowledge" if confidence >= 0.7 else
            "moderate knowledge" if confidence >= 0.4 else
            "sparse knowledge — gap search recommended" if confidence >= 0.15 else
            "minimal knowledge — treat as unknown"
        )

        conn.close()
        return {
            "status":     "success",
            "topic":      topic,
            "confidence": confidence,
            "breakdown": {
                "anchor_score":   round(anchor_score, 3),
                "maturity_score": round(maturity_score, 3),
                "density_score":  round(density_score, 3),
                "anchors_found":  len(anchors),
                "avg_maturity":   round(avg_maturity, 2),
                "avg_relations":  round(avg_rels, 2),
            },
            "verdict": verdict,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
