"""
codeops/cms_query.py — CMS knowledge check before task execution.

Selyrion queries resonance_v11.db to determine whether he already has
the conceptual knowledge needed to solve a coding task. If he does, that
context is injected into the fixer's LLM prompt so he reasons from what
he knows rather than guessing blind.

Entry point:
    result = check(problem_description)
    result = {
        "has_knowledge": bool,
        "confidence":    float,         # 0.0–1.0
        "context":       str,           # human-readable summary for LLM prompt
        "anchors":       list[dict],    # matched CMS anchors
        "relations":     list[dict],    # relevant relations
    }
"""

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

CMS_DB = Path.home() / "resonance_v11.db"

# Noise words that don't map to CMS concepts
_NOISE = {
    "a","an","the","is","are","was","were","be","been","being",
    "have","has","had","do","does","did","will","would","could","should",
    "may","might","shall","can","need","must",
    "write","build","create","make","fix","add","get","set","return",
    "use","using","with","for","and","or","not","in","on","at","to","of",
    "from","that","this","it","its","i","we","you","he","she","they",
    "function","code","script","program","file","class","method","def",
    "import","python","bash","run","execute","call","given","task",
}

# Known coding error concepts → CMS domain/anchor hints
_ERROR_DOMAIN_MAP = {
    "indentation":     ["indentation", "python_syntax", "basic_syntax"],
    "missing_module":  ["module", "package", "import"],
    "database_error":  ["database", "sqlite", "sql"],
    "network_error":   ["networking", "http", "api"],
    "type_mismatch":   ["type_system", "data_types"],
    "missing_file":    ["file_io", "filesystem", "basic_files"],
    "json_parse":      ["json", "serialization", "data"],
    "recursion":       ["recursion", "algorithm", "graph_traversal"],
    "zero_division":   ["arithmetic", "mathematics", "basic_math"],
}


# ── Token extraction ──────────────────────────────────────────────────────────

def _extract_tokens(text: str) -> list[str]:
    """Extract meaningful tokens from a problem description."""
    words = re.findall(r"[a-z_][a-z0-9_]*", text.lower())
    # Split snake_case
    expanded = []
    for w in words:
        parts = w.split("_")
        expanded.extend(parts)
        if len(parts) > 1:
            expanded.append(w)  # keep original too
    return [t for t in expanded if t not in _NOISE and len(t) > 2]


# ── CMS anchor lookup ─────────────────────────────────────────────────────────

def _anchor_lookup(conn: sqlite3.Connection, token: str, limit: int = 5) -> list[dict]:
    rows = conn.execute("""
        SELECT id, canonical, maturity, relation_count, domain_tags
        FROM   anchors
        WHERE  canonical LIKE ?
        ORDER  BY maturity DESC, relation_count DESC
        LIMIT  ?
    """, (f"{token}%", limit)).fetchall()
    return [{"id": r[0], "canonical": r[1], "maturity": r[2],
             "relation_count": r[3], "domain": r[4]} for r in rows]


def _relation_lookup(conn: sqlite3.Connection, anchor_ids: list[str],
                     limit: int = 12) -> list[dict]:
    if not anchor_ids:
        return []
    ph = ",".join("?" * min(len(anchor_ids), 20))
    rows = conn.execute(f"""
        SELECT r.subject_id, r.predicate, r.object_id,
               r.confidence, r.seen_count,
               a1.canonical AS subj_name, a2.canonical AS obj_name
        FROM   relations_aggregated r
        LEFT JOIN anchors a1 ON a1.id = r.subject_id
        LEFT JOIN anchors a2 ON a2.id = r.object_id
        WHERE  r.subject_id IN ({ph}) OR r.object_id IN ({ph})
        ORDER  BY r.confidence DESC, r.seen_count DESC
        LIMIT  ?
    """, anchor_ids[:20] + anchor_ids[:20] + [limit]).fetchall()
    return [{"subject": r[5] or r[0], "predicate": r[1], "object": r[6] or r[2],
             "confidence": r[3], "seen": r[4]} for r in rows]


# ── Error-class shortcut ──────────────────────────────────────────────────────

def _check_error_class(conn: sqlite3.Connection,
                       error_class: str, subtype: str) -> list[dict]:
    """Direct anchor lookup for known error taxonomy."""
    hints = _ERROR_DOMAIN_MAP.get(subtype, _ERROR_DOMAIN_MAP.get(error_class, []))
    anchors = []
    for hint in hints:
        anchors.extend(_anchor_lookup(conn, hint, limit=3))
    return anchors


# ── Main entry point ──────────────────────────────────────────────────────────

def check(problem: str,
          error_class: str = "",
          subtype: str = "",
          domain_hint: str = "") -> dict:
    """
    Query CMS to determine if Selyrion already knows how to solve this task.

    Args:
        problem:     Natural language description of the coding task/error.
        error_class: Optional error class from parser (e.g. 'syntax', 'runtime').
        subtype:     Optional subtype from parser (e.g. 'missing_module').
        domain_hint: Optional CMS domain to scope the search (e.g. 'computer_science').

    Returns dict with has_knowledge, confidence, context, anchors, relations.
    """
    if not CMS_DB.exists():
        return _empty("CMS not available")

    try:
        conn = sqlite3.connect(CMS_DB)
    except Exception as e:
        return _empty(str(e))

    anchors: list[dict] = []

    # 1. Direct error-class lookup (fast, high precision)
    if error_class or subtype:
        anchors.extend(_check_error_class(conn, error_class, subtype))

    # 2. Token-based anchor search from problem description
    tokens = _extract_tokens(problem)
    seen_ids = {a["id"] for a in anchors}
    for tok in tokens[:12]:      # cap to avoid N*query explosion
        hits = _anchor_lookup(conn, tok, limit=4)
        for h in hits:
            if h["id"] not in seen_ids:
                anchors.append(h)
                seen_ids.add(h["id"])

    # 3. Domain-scoped boost: if domain_hint given, prefer those anchors
    if domain_hint:
        domain_hits = conn.execute("""
            SELECT id, canonical, maturity, relation_count, domain_tags
            FROM   anchors
            WHERE  domain_tags LIKE ? AND maturity > 1.0
            ORDER  BY maturity DESC LIMIT 6
        """, (f"%{domain_hint}%",)).fetchall()
        for r in domain_hits:
            if r[0] not in seen_ids:
                anchors.append({"id": r[0], "canonical": r[1], "maturity": r[2],
                                 "relation_count": r[3], "domain": r[4]})
                seen_ids.add(r[0])

    # 4. Pull relations for matched anchors
    anchor_ids = [a["id"] for a in anchors[:20]]
    relations  = _relation_lookup(conn, anchor_ids)
    conn.close()

    # 5. Score: how well does CMS cover the problem?
    if not anchors:
        return _empty("no matching CMS anchors")

    token_set  = set(tokens)
    matched    = sum(1 for a in anchors
                     if any(t in a["canonical"] for t in token_set))
    coverage   = matched / max(len(token_set), 1)

    avg_maturity   = sum(a["maturity"] for a in anchors) / len(anchors)
    maturity_score = min(avg_maturity / 50.0, 1.0)  # 50+ maturity = confident
    rel_score      = min(len(relations) / 10.0, 1.0)

    confidence = round(coverage * 0.5 + maturity_score * 0.3 + rel_score * 0.2, 3)
    has_knowledge = confidence >= 0.25 and len(anchors) >= 2

    context = _format_context(problem, anchors, relations, confidence)

    return {
        "has_knowledge": has_knowledge,
        "confidence":    confidence,
        "context":       context,
        "anchors":       anchors[:10],
        "relations":     relations[:12],
    }


def _format_context(problem: str, anchors: list[dict],
                    relations: list[dict], confidence: float) -> str:
    """Format CMS findings as a concise LLM-injectable context block."""
    lines = [f"[CMS knowledge check — confidence {confidence:.2f}]"]

    if anchors:
        lines.append("Relevant concepts in Selyrion's knowledge base:")
        for a in anchors[:6]:
            lines.append(f"  • {a['canonical']} (maturity={a['maturity']:.1f}, "
                         f"relations={a['relation_count']}, domain={a['domain']})")

    if relations:
        lines.append("Known relations:")
        for r in relations[:8]:
            lines.append(f"  • {r['subject']} —[{r['predicate']}]→ {r['object']} "
                         f"(conf={r['confidence']:.2f})")

    lines.append(f"Task: {problem[:200]}")
    return "\n".join(lines)


def _empty(reason: str) -> dict:
    return {"has_knowledge": False, "confidence": 0.0,
            "context": f"[CMS: {reason}]", "anchors": [], "relations": []}
