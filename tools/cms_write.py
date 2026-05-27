"""tools/cms_write.py — Write verified facts back into CMS."""
import sys, sqlite3, hashlib, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scos_tools import register_tool

CMS_DB = Path.home() / "resonance_v11.db"

@register_tool(
    name="cms_write",
    description="Write a verified fact into Selyrion's CMS knowledge graph",
    input_schema={
        "subject":    {"type": "str",   "required": True},
        "predicate":  {"type": "str",   "required": True},
        "object":     {"type": "str",   "required": True},
        "confidence": {"type": "float", "required": False},
        "domain":     {"type": "str",   "required": False},
        "source":     {"type": "str",   "required": False},
    }
)
def cms_write(inputs):
    subj  = inputs["subject"].strip().lower()[:120]
    pred  = inputs["predicate"].strip().lower()[:60]
    obj   = inputs["object"].strip().lower()[:120]
    conf  = float(inputs.get("confidence", 0.75))
    domain= inputs.get("domain", "general")
    source= inputs.get("source", "codeops:verified")
    try:
        conn = sqlite3.connect(CMS_DB, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")

        def ensure_anchor(concept):
            aid = "a." + hashlib.md5(concept.encode()).hexdigest()[:12]
            conn.execute("""
                INSERT OR IGNORE INTO anchors (id, canonical, maturity)
                VALUES (?,?,1.0)
            """, (aid, concept))
            row = conn.execute("SELECT id FROM anchors WHERE canonical=?",
                               (concept,)).fetchone()
            return row[0] if row else aid

        sid = ensure_anchor(subj)
        oid = ensure_anchor(obj)
        rid = "r." + hashlib.md5(f"{sid}{pred}{oid}".encode()).hexdigest()[:12]

        conn.execute("""
            INSERT INTO relations (id, subject_id, object_id, predicate,
                confidence, seen_count, domain_tags, source_dataset)
            VALUES (?,?,?,?,?,1,?,?)
            ON CONFLICT(id) DO UPDATE SET
                seen_count = seen_count + 1,
                confidence = MAX(confidence, excluded.confidence)
        """, (rid, sid, oid, pred, conf, domain, source))

        conn.commit(); conn.close()
        return {"status": "success", "relation_id": rid,
                "written": f"{subj} —[{pred}]→ {obj}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
