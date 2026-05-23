#!/usr/bin/env python3
"""
adaptive_policy.py — Selyrion parliament self-regulation engine.

Reads cognitive terrain data at session start and returns a per-position
DebatePolicy that governs:

  - stockfish_depth       : deeper in uncertain / weak zones
  - consensus_threshold   : stricter in calibration-defect zones
  - model_weights         : from terrain_routing_rules
  - extra_depth_flag      : True when position is in known weak region
  - curriculum_flag       : True when position matches known failure geometry
  - zone_label            : human-readable terrain classification

Usage (in chess_replay.py):
    from adaptive_policy import AdaptivePolicy, DebatePolicy

    policy = AdaptivePolicy(supermodel_db_path)

    # At each position:
    dp = policy.for_position(ply, sf_eval, opening)
    sf = stockfish_report(engine, board, dp.stockfish_depth, args.think_time)
    result = parliament_position(..., policy=dp)
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DebatePolicy:
    """Per-position parliament governance parameters."""
    stockfish_depth:     int   = 12
    consensus_threshold: float = 0.67    # fraction of models needed for consensus
    model_weights:       dict  = field(default_factory=dict)
    extra_depth:         bool  = False   # True → show uncertainty in console
    curriculum_flag:     bool  = False   # True → auto-generate curriculum task
    zone_label:          str   = "normal"
    reason:              str   = ""


def _game_phase(ply: int) -> str:
    if ply < 20:  return "opening"
    if ply < 40:  return "middlegame"
    return "endgame"


def _eval_band(sf_eval) -> str:
    if sf_eval is None: return "unknown"
    e = float(sf_eval)
    if e >  2.0: return "winning"
    if e >  0.5: return "slight_advantage"
    if e > -0.5: return "equal"
    if e > -2.0: return "slight_disadvantage"
    return "losing"


class AdaptivePolicy:
    """
    Loads terrain data once at session start, answers per-position queries
    cheaply (all in-memory lookups after init).
    """

    # Depth scaling by zone
    DEPTH_NORMAL    = 10
    DEPTH_UNCERTAIN = 16   # known split-opinion zone
    DEPTH_WEAK      = 18   # known weakness domain
    DEPTH_DEFECT    = 20   # known calibration defect zone (worst)

    # Consensus thresholds
    THRESHOLD_NORMAL    = 0.67  # 2/3 models
    THRESHOLD_UNCERTAIN = 0.80  # stricter in split zones
    THRESHOLD_DEFECT    = 1.00  # require unanimity in confidence-pathology zones

    def __init__(self, supermodel_db_path: str = None):
        self._routing:    dict = {}   # (ctype, cval) → {model, trust_weight, acc}
        self._weak:       set  = set()  # (model, phase, band) high-failure combos
        self._defect_zones: set = set() # (phase, band) calibration-defect zones
        self._uncertain:  set  = set()  # (phase, band) high-uncertainty zones
        self._loaded      = False

        if supermodel_db_path and Path(supermodel_db_path).exists():
            self._load(supermodel_db_path)

    def _load(self, db_path: str):
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            # Routing rules
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

            if "terrain_routing_rules" in tables:
                for r in conn.execute(
                    "SELECT condition_type, condition_value, preferred_model, "
                    "trust_weight, accuracy_rate FROM terrain_routing_rules"
                ).fetchall():
                    key = (r["condition_type"], r["condition_value"])
                    self._routing[key] = {
                        "model":        r["preferred_model"],
                        "trust_weight": r["trust_weight"],
                        "accuracy":     r["accuracy_rate"],
                    }

            # Weakness domains — high failure rate zones
            if "terrain_weakness_domains" in tables:
                for r in conn.execute(
                    "SELECT model, domain_type, domain_value, failure_rate "
                    "FROM terrain_weakness_domains WHERE failure_rate >= 0.55"
                ).fetchall():
                    self._weak.add((r["model"], r["domain_type"], r["domain_value"]))

            # Calibration defect zones
            if "terrain_calibration_defects" in tables:
                for r in conn.execute(
                    "SELECT DISTINCT game_phase, eval_band "
                    "FROM terrain_calibration_defects WHERE overconfidence >= 0.30"
                ).fetchall():
                    self._defect_zones.add((r["game_phase"], r["eval_band"]))

            # Uncertainty topology clusters
            if "terrain_uncertainty_topology" in tables:
                for r in conn.execute(
                    "SELECT game_phase, eval_band, COUNT(*) c "
                    "FROM terrain_uncertainty_topology "
                    "GROUP BY game_phase, eval_band HAVING c >= 5"
                ).fetchall():
                    self._uncertain.add((r["game_phase"], r["eval_band"]))

            conn.close()
            self._loaded = True

        except Exception as e:
            # Graceful degradation — fall back to defaults silently
            self._loaded = False

    def for_position(self, ply: int, sf_eval=None, opening: str = "") -> DebatePolicy:
        """Return a DebatePolicy for this position."""
        phase = _game_phase(ply)
        band  = _eval_band(sf_eval)
        dp    = DebatePolicy()

        if not self._loaded:
            return dp

        reasons = []

        # ── Model weights from routing rules ──────────────────────────────────
        weights = {}
        for ctype, cval in [("game_phase", phase), ("eval_band", band)]:
            rule = self._routing.get((ctype, cval))
            if rule:
                m = rule["model"]
                w = rule["trust_weight"]
                # Take the max weight if model appears in multiple rules
                weights[m] = max(weights.get(m, 0.5), w)
                reasons.append(f"route {ctype}={cval}→{m}(w={w:.2f})")
        dp.model_weights = weights

        # ── Calibration defect zone — strictest ───────────────────────────────
        if (phase, band) in self._defect_zones:
            dp.stockfish_depth     = self.DEPTH_DEFECT
            dp.consensus_threshold = self.THRESHOLD_DEFECT
            dp.extra_depth         = True
            dp.curriculum_flag     = True
            dp.zone_label          = "calibration_defect"
            reasons.append(f"defect zone {phase}/{band}")

        # ── High-uncertainty zone ─────────────────────────────────────────────
        elif (phase, band) in self._uncertain:
            dp.stockfish_depth     = self.DEPTH_UNCERTAIN
            dp.consensus_threshold = self.THRESHOLD_UNCERTAIN
            dp.extra_depth         = True
            dp.zone_label          = "uncertain"
            reasons.append(f"uncertain zone {phase}/{band}")

        # ── Weakness domain (any model) ───────────────────────────────────────
        else:
            for m in self._weak:
                model, dtype, dval = m
                if (dtype == "game_phase" and dval == phase) or \
                   (dtype == "eval_band"  and dval == band):
                    dp.stockfish_depth = max(dp.stockfish_depth, self.DEPTH_WEAK)
                    dp.extra_depth     = True
                    dp.zone_label      = "weak_domain"
                    reasons.append(f"weak: {model} in {dtype}={dval}")
                    break

        dp.reason = " | ".join(reasons) if reasons else "normal"
        return dp

    def summary(self) -> str:
        lines = [f"AdaptivePolicy loaded={self._loaded}"]
        lines.append(f"  routing rules:    {len(self._routing)}")
        lines.append(f"  weakness domains: {len(self._weak)}")
        lines.append(f"  defect zones:     {len(self._defect_zones)}")
        lines.append(f"  uncertain zones:  {len(self._uncertain)}")
        return "\n".join(lines)
