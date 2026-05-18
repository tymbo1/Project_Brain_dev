#!/usr/bin/env python3
"""
CMS Bridge — read-only access to resonance_v11.db for ProjectBrain.
Returns triples in memory.sym format so the inference stack needs no changes.
"""
import sqlite3
import os

CMS_PATH = os.path.expanduser("~/cmsp0/resonance_v11.db")

def _get_anchor_id(cur, term: str):
    cur.execute("SELECT id FROM anchors WHERE canonical = ?", (term.lower().strip(),))
    row = cur.fetchone()
    return row[0] if row else None

def query_cms(term: str, limit: int = 150) -> list:
    """
    Query CMS for triples involving term.
    Returns list of strings in memory.sym format:
      'subject | predicate | object | strength: X'
    Read-only — never writes to CMS.
    """
    try:
        con = sqlite3.connect(f"file:{CMS_PATH}?mode=ro", uri=True)
        cur = con.cursor()

        anchor_id = _get_anchor_id(cur, term)
        if not anchor_id:
            con.close()
            return []

        half = limit // 2

        # Preferred clean predicates — ordered by reliability
        CLEAN_PREDICATES = (
            "is_a", "instance_of", "part_of", "has_a", "has_property",
            "related_to", "associative", "similar_to", "opposite_of",
            "used_for", "capable_of", "defined_as", "causes", "enables",
            "leads_to", "requires"
        )
        pred_priority = ", ".join(f"'{p}'" for p in CLEAN_PREDICATES)

        # Two separate queries — uses idx_rel_subj and idx_rel_obj indexes
        # Filters: short canonicals only, order by seen_count DESC to prefer reliable data
        # Exclude is_a from CMS — too noisy from crowdsourced sources
        # Local memory.sym provides is_a via teach
        cur.execute("""
            SELECT a1.canonical, r.predicate, a2.canonical, r.confidence
            FROM relations r
            JOIN anchors a1 ON r.subject_id = a1.id
            JOIN anchors a2 ON r.object_id = a2.id
            WHERE r.subject_id = ?
              AND r.predicate NOT IN ('is_a', 'IsA', 'instance_of')
              AND length(a2.canonical) < 35
              AND length(a1.canonical) < 35
            ORDER BY r.seen_count DESC, r.confidence DESC
            LIMIT ?
        """, (anchor_id, half))
        as_subject = cur.fetchall()

        cur.execute("""
            SELECT a1.canonical, r.predicate, a2.canonical, r.confidence
            FROM relations r
            JOIN anchors a1 ON r.subject_id = a1.id
            JOIN anchors a2 ON r.object_id = a2.id
            WHERE r.object_id = ?
              AND r.predicate NOT IN ('is_a', 'IsA', 'instance_of')
              AND length(a1.canonical) < 35
              AND length(a2.canonical) < 35
            ORDER BY r.seen_count DESC, r.confidence DESC
            LIMIT ?
        """, (anchor_id, half))
        as_object = cur.fetchall()

        con.close()

        # Stop words that signal literary/corpus fragments rather than clean concepts
        STOP = {"with", "of", "for", "by", "from", "that", "this", "are",
                "been", "into", "onto", "such", "these", "those", "which",
                "its", "their", "our", "your", "my", "his", "her",
                "in", "is", "was", "has", "have", "not", "no", "an", "be"}

        def _is_clean(canonical: str) -> bool:
            if not canonical:
                return False
            words = canonical.lower().replace("_", " ").split()
            if len(words) > 4:
                return False
            return not any(w in STOP for w in words)

        results = []
        seen = set()
        for rows in (as_subject, as_object):
            for subj, pred, obj, conf in rows:
                if not subj or not pred or not obj:
                    continue
                if not _is_clean(obj) or not _is_clean(subj):
                    continue
                key = (subj, pred, obj)
                if key in seen:
                    continue
                seen.add(key)
                # Confidence in CMS = corpus co-occurrence frequency, not contextual validity.
                # Use a flat moderate score; proper ranking is done by activation_engine A(n).
                results.append(f"{subj} | {pred} | {obj} | strength: 50")

        return results

    except Exception:
        return []
