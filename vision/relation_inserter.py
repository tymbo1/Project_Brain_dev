"""
relation_inserter.py — Inserts visual relations into CMS with full provenance.

Deduplicates within session. Increments usage_count on conflict.
"""
import sqlite3
import uuid
from .predicate_normalizer import normalize


class RelationInserter:
    def __init__(self, conn: sqlite3.Connection, source_dataset: str = "coco"):
        self._conn           = conn
        self._source_dataset = source_dataset
        self._inserted       = 0
        self._deduped        = 0

    def insert(
        self,
        subject_id: str,
        raw_predicate: str,
        object_id: str,
        confidence: float = 1.0,
        frame_id: int | None = None,
        vis_timestamp: float | None = None,
        image_id: str | None = None,
    ) -> str | None:
        """
        Insert a visual relation. Returns relation ID or None if deduplicated.
        On duplicate (same subj/pred/obj/source), increments usage_count.
        """
        cms_pred, pred_type = normalize(raw_predicate)

        # Dedup check: same subject, normalized predicate, object, source
        existing = self._conn.execute("""
            SELECT id FROM relations
            WHERE subject_id = ? AND predicate = ? AND object_id = ?
              AND source_dataset = ?
        """, (subject_id, cms_pred, object_id, self._source_dataset)).fetchone()

        if existing:
            self._conn.execute("""
                UPDATE relations SET usage_count = COALESCE(usage_count, 1) + 1
                WHERE id = ?
            """, (existing[0],))
            self._deduped += 1
            return existing[0]

        rel_id = str(uuid.uuid4())
        self._conn.execute("""
            INSERT INTO relations (
                id, subject_id, predicate, object_id,
                source_dataset, raw_predicate, normalized_predicate,
                predicate_type, predicate_layer,
                confidence, edge_type,
                frame_id, vis_timestamp,
                domain_tags, usage_count
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, 'relational',
                ?, 'visual',
                ?, ?,
                'vision', 1
            )
        """, (
            rel_id, subject_id, cms_pred, object_id,
            self._source_dataset, raw_predicate, cms_pred,
            pred_type,
            confidence,
            frame_id, vis_timestamp,
        ))
        self._inserted += 1
        return rel_id

    @property
    def stats(self) -> dict:
        return {"inserted": self._inserted, "deduped": self._deduped}
