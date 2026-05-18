"""
anchor_resolver.py — Resolves COCO/VG detection labels to CMS anchor IDs.

Normalization map established by coco_anchor_analysis.py (100% coverage).
Creates new anchors for unknown labels with modality='vision'.
"""
import sqlite3
import uuid

# COCO label → preferred CMS canonical (from analysis — all 80 covered)
_NORMALIZE: dict[str, str] = {
    "tv":           "television",
    "cell phone":   "mobile phone",
    "couch":        "sofa",
    "hot dog":      "hotdog",
    "potted plant": "plant",
    "dining table": "table",
    "wine glass":   "glass",
    "hair drier":   "hair dryer",
    "sports ball":  "ball",
}


def normalize_label(label: str) -> str:
    label = label.lower().strip()
    return _NORMALIZE.get(label, label)


class AnchorResolver:
    """
    Resolves detection labels → CMS anchor IDs.
    Caches within session to avoid repeated DB hits.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn  = conn
        self._cache: dict[str, str] = {}

    def resolve(self, label: str) -> str:
        """Return existing anchor ID or create new vision anchor."""
        canonical = normalize_label(label)

        if canonical in self._cache:
            return self._cache[canonical]

        row = self._conn.execute(
            "SELECT id FROM anchors WHERE canonical = ?", (canonical,)
        ).fetchone()

        if row:
            anchor_id = row[0]
        else:
            anchor_id = str(uuid.uuid4())
            self._conn.execute("""
                INSERT INTO anchors (id, canonical, display_name, modality, state, maturity)
                VALUES (?, ?, ?, 'vision', 'emerging', 0.5)
            """, (anchor_id, canonical, label))
            print(f"  [anchor_resolver] created new anchor: {canonical!r}")

        self._cache[canonical] = anchor_id
        return anchor_id

    def resolve_many(self, labels: list[str]) -> dict[str, str]:
        """Resolve a list of labels, return {label: anchor_id}."""
        return {label: self.resolve(label) for label in labels}
