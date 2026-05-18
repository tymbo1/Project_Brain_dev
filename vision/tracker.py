"""
tracker.py — Lightweight deterministic object tracker.

Matches detections across frames by label + bbox centroid proximity.
Assigns persistent instance_ids that survive across frames/video.

No deep learning — pure geometry. DeepSORT can replace this later.

Rule (GPT architectural invariant):
    instance_id = persistent identity across time
    anchor_id   = canonical CMS concept (what it IS)
"""
from __future__ import annotations
import math
import uuid
from dataclasses import dataclass, field

MATCH_DIST_THRESH = 80    # pixels — max centroid distance to consider a match
MAX_MISSING       = 5     # frames a track can be absent before it's dropped


@dataclass
class Track:
    instance_id: str
    label:       str
    anchor_id:   str
    center:      tuple[float, float]
    bbox:        tuple[float, float, float, float]
    confidence:  float
    frame_id:    int
    missing:     int = 0   # consecutive frames without a detection match


class ObjectTracker:
    """
    Maintains a set of active tracks across frames.
    Call update() each frame — returns TrackedDetection list.
    """

    def __init__(self):
        self._tracks: list[Track] = []
        self._frame_id: int = 0

    def update(self, detections: list, anchor_ids: dict[int, str]) -> list[Track]:
        """
        Match detections (list of Detection) to existing tracks.
        Creates new tracks for unmatched detections.
        Returns list of Track for this frame (matched + new).

        anchor_ids: {detection_index: anchor_id}
        """
        self._frame_id += 1
        unmatched_det  = list(range(len(detections)))
        matched_tracks = []

        # Match each existing track to nearest same-label detection
        for track in self._tracks:
            best_idx  = None
            best_dist = MATCH_DIST_THRESH + 1

            for i in unmatched_det:
                det = detections[i]
                if det.label != track.label:
                    continue
                dist = _dist(det.center, track.center)
                if dist < best_dist:
                    best_dist = dist
                    best_idx  = i

            if best_idx is not None:
                det = detections[best_idx]
                track.center     = det.center
                track.bbox       = det.bbox
                track.confidence = det.confidence
                track.frame_id   = self._frame_id
                track.missing    = 0
                matched_tracks.append(track)
                unmatched_det.remove(best_idx)
            else:
                track.missing += 1

        # Create new tracks for unmatched detections
        new_tracks = []
        for i in unmatched_det:
            det = detections[i]
            new_tracks.append(Track(
                instance_id = f"{det.label}_{uuid.uuid4().hex[:6]}",
                label       = det.label,
                anchor_id   = anchor_ids[i],
                center      = det.center,
                bbox        = det.bbox,
                confidence  = det.confidence,
                frame_id    = self._frame_id,
            ))

        # Age out missing tracks
        self._tracks = [
            t for t in self._tracks + new_tracks
            if t.missing <= MAX_MISSING
        ]

        return [t for t in self._tracks if t.missing == 0]

    @property
    def active_tracks(self) -> list[Track]:
        return [t for t in self._tracks if t.missing == 0]

    @property
    def frame_id(self) -> int:
        return self._frame_id


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0])**2 + (a[1] - b[1])**2)
