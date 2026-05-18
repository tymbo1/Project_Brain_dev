"""
predicate_normalizer.py — Maps raw VG/RelTR predicates to CMS canonical predicates.

Raw predicate always preserved in raw_predicate column.
Normalized predicate written to predicate and normalized_predicate columns.
"""

# Raw VG predicate → (cms_canonical, predicate_type)
PREDICATE_MAP: dict[str, tuple[str, str]] = {
    # action
    "holding":       ("interacts_with", "action"),
    "grasping":      ("interacts_with", "action"),
    "carrying":      ("interacts_with", "action"),
    "eating":        ("interacts_with", "action"),
    "drinking":      ("interacts_with", "action"),
    "using":         ("interacts_with", "action"),
    "riding":        ("interacts_with", "action"),
    "driving":       ("interacts_with", "action"),
    "playing":       ("interacts_with", "action"),
    "kicking":       ("interacts_with", "action"),
    "throwing":      ("interacts_with", "action"),
    # spatial
    "on":            ("spatial_on",       "spatial"),
    "on top of":     ("spatial_on",       "spatial"),
    "sitting on":    ("spatial_on",       "spatial"),
    "standing on":   ("spatial_on",       "spatial"),
    "lying on":      ("spatial_on",       "spatial"),
    "under":         ("spatial_under",    "spatial"),
    "below":         ("spatial_under",    "spatial"),
    "beneath":       ("spatial_under",    "spatial"),
    "next to":       ("spatial_adjacent", "spatial"),
    "beside":        ("spatial_adjacent", "spatial"),
    "near":          ("spatial_adjacent", "spatial"),
    "by":            ("spatial_adjacent", "spatial"),
    "in front of":   ("spatial_adjacent", "spatial"),
    "behind":        ("spatial_adjacent", "spatial"),
    "above":         ("spatial_on",       "spatial"),
    "over":          ("spatial_on",       "spatial"),
    "below":         ("spatial_under",    "spatial"),
    "left of":       ("spatial_adjacent", "spatial"),
    "right of":      ("spatial_adjacent", "spatial"),
    "leaning on":    ("spatial_support",  "spatial"),
    "against":       ("spatial_support",  "spatial"),
    "in":            ("spatial_on",       "spatial"),
    "inside":        ("spatial_on",       "spatial"),
    # attribute
    "wearing":       ("has_attribute", "attribute"),
    "has":           ("has_attribute", "attribute"),
    "with":          ("has_attribute", "attribute"),
    # structural
    "part of":       ("part_of",   "structural"),
    "attached to":   ("part_of",   "structural"),
    "hanging from":  ("part_of",   "structural"),
    "mounted on":    ("part_of",   "structural"),
    # ontology
    "is":            ("is_a",        "ontology"),
    "are":           ("is_a",        "ontology"),
}

_FALLBACK = ("related_to", "unknown")


def normalize(raw: str) -> tuple[str, str]:
    """Return (cms_canonical, predicate_type) for a raw predicate string."""
    return PREDICATE_MAP.get(raw.lower().strip(), _FALLBACK)
