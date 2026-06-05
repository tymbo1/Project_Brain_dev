"""
plan_next.py — PLAN_NEXT operator.

Action utility:
  U(a | S,G) =
    g₁·GoalAlignment(a,G)
  + g₂·Feasibility(a,S)
  + g₃·ExpectedInformationGain(a)
  + g₄·RiskReduction(a)
  - g₅·Cost(a)
  - g₆·SecurityRisk(a)
  - g₇·UncertaintyPenalty(a)

Multi-step plan score:
  PlanScore(P) = Σ_t δ^t · U(a_t | S_t,G)

Uses beam search over the activated graph's project/requires/enables edges.

Output:
{
  "operator":   "PLAN_NEXT",
  "subject":    "...",
  "actions":    [{"action": "...", "utility": 0.0, "rationale": "..."}],
  "plan_score": 0.0,
  "confidence": 0.0,
  "gaps":       []
}
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from .working_memory import WorkingMemoryPacket, MemoryEdge

# ── Weights ───────────────────────────────────────────────────────────────────
G1 = 0.30   # GoalAlignment
G2 = 0.20   # Feasibility
G3 = 0.20   # ExpectedInformationGain
G4 = 0.10   # RiskReduction
G5 = 0.10   # Cost
G6 = 0.05   # SecurityRisk
G7 = 0.05   # UncertaintyPenalty
DELTA = 0.85  # future discount

BEAM_WIDTH = 4
MAX_PLAN_DEPTH = 5

# ── Predicate utility proxies ─────────────────────────────────────────────────
# Forward-looking predicates that represent actionable steps
_PLAN_PREDS = {
    "requires":    (0.8, 0.7),   # (goal_alignment, feasibility_hint)
    "enables":     (0.9, 0.8),
    "depends_on":  (0.7, 0.6),
    "produces":    (0.85, 0.75),
    "blocks":      (0.3, 0.2),
    "leads_to":    (0.8, 0.7),
    "prevents":    (0.4, 0.3),
    "next_step":   (1.0, 0.9),
    "build_order": (1.0, 0.85),
    "precedes":    (0.85, 0.75),
    "follows":     (0.80, 0.70),
}

# Predicates that signal high uncertainty / risk
_RISK_PREDS = frozenset({"contradicts", "blocks", "prevents", "conflicts_with"})


@dataclass
class ActionStep:
    action: str
    utility: float
    rationale: str = ""
    depth: int = 0
    supporting_edges: list[MemoryEdge] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "action":    self.action,
            "utility":   round(self.utility, 3),
            "rationale": self.rationale,
        }


@dataclass
class PlanNextResult:
    operator: str = "PLAN_NEXT"
    subject: str = ""
    actions: list[ActionStep] = field(default_factory=list)
    plan_score: float = 0.0
    confidence: float = 0.0
    gaps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "operator":   self.operator,
            "subject":    self.subject,
            "actions":    [a.as_dict() for a in self.actions],
            "plan_score": round(self.plan_score, 3),
            "confidence": round(self.confidence, 3),
            "gaps":       self.gaps,
        }

    def is_sufficient(self) -> bool:
        return len(self.actions) >= 1 and self.plan_score > 0.2


def run(packet: WorkingMemoryPacket, goals: list[str] | None = None) -> PlanNextResult:
    """Execute PLAN_NEXT over the working memory packet."""
    subject = packet.query
    result = PlanNextResult(subject=subject)

    if packet.is_empty():
        return result

    # ── Extract plan-relevant edges ───────────────────────────────────────────
    plan_edges = [
        e for e in packet.top_edges
        if e.predicate in _PLAN_PREDS
    ]

    if not plan_edges:
        # Fall back: any outbound edge from the top-activated node
        top_node = packet.top_nodes[0].canonical if packet.top_nodes else subject
        plan_edges = [e for e in packet.top_edges if e.subject == top_node][:8]

    if not plan_edges:
        result.gaps.append(f"no actionable edges found for '{subject}'")
        return result

    # ── Score each action ─────────────────────────────────────────────────────
    actions: list[ActionStep] = []
    goal_terms = set((goals or []) + [subject])
    seen: set[str] = set()

    for edge in plan_edges:
        if edge.obj in seen:
            continue
        seen.add(edge.obj)

        goal_w, feas_w = _PLAN_PREDS.get(edge.predicate, (0.5, 0.5))
        a_u = packet.node_activation(edge.subject)
        a_v = packet.node_activation(edge.obj)

        ga   = goal_w * (1.0 + 0.2 * _goal_overlap(edge.obj, goal_terms))
        feas = feas_w * edge.strength
        eig  = a_v * (1.0 - a_u) * 0.5  # gain = high-activation target we haven't visited much
        risk = 1.0 if edge.predicate in _RISK_PREDS else 0.0
        cost = 1.0 - edge.strength       # low-strength edges = higher cost
        unc  = max(0.0, 0.5 - packet.packet_confidence)

        u = (
            G1 * min(ga, 1.0)
          + G2 * feas
          + G3 * eig
          + G4 * (1.0 - risk * 0.5)
          - G5 * cost
          - G6 * risk
          - G7 * unc
        )

        rationale = _build_rationale(edge, packet)

        actions.append(ActionStep(
            action=edge.obj,
            utility=round(max(u, 0.0), 4),
            rationale=rationale,
            depth=1,
            supporting_edges=[edge],
        ))

    # ── Multi-step beam ───────────────────────────────────────────────────────
    actions.sort(key=lambda a: -a.utility)
    beam = actions[:BEAM_WIDTH]

    # Extend top actions one more step
    extended = []
    for step in beam:
        follow_edges = [
            e for e in packet.top_edges
            if e.subject == step.action and e.obj not in seen and e.predicate in _PLAN_PREDS
        ]
        for fe in follow_edges[:2]:
            _, feas_w = _PLAN_PREDS.get(fe.predicate, (0.5, 0.5))
            u2 = step.utility * DELTA * feas_w * fe.strength
            extended.append(ActionStep(
                action=f"{step.action} → {fe.obj}",
                utility=round(u2, 4),
                rationale=f"After {step.action}: {fe.predicate} {fe.obj}",
                depth=2,
            ))

    all_actions = (actions + extended)
    all_actions.sort(key=lambda a: -a.utility)

    result.actions = all_actions[:8]

    # ── Plan score: discounted sum ────────────────────────────────────────────
    plan_score = sum(
        (DELTA ** a.depth) * a.utility
        for a in result.actions
    )
    result.plan_score = round(min(plan_score, 1.0), 3)

    # ── Confidence ────────────────────────────────────────────────────────────
    result.confidence = round(
        packet.packet_confidence * 0.6 + result.plan_score * 0.4,
        3,
    )

    # ── Gaps: required nodes not in packet ───────────────────────────────────
    required_nodes = {
        e.obj for e in packet.top_edges
        if e.predicate in ("requires", "depends_on") and e.subject == subject
    }
    available_nodes = {n.canonical for n in packet.top_nodes}
    result.gaps = sorted(required_nodes - available_nodes)

    return result


def _goal_overlap(action: str, goal_terms: set[str]) -> float:
    action_words = set(action.lower().split())
    goal_words   = set(w.lower() for g in goal_terms for w in g.split())
    if not goal_words:
        return 0.0
    return len(action_words & goal_words) / max(len(goal_words), 1)


def _build_rationale(edge: MemoryEdge, packet: WorkingMemoryPacket) -> str:
    a_subj = packet.node_activation(edge.subject)
    parts = []
    if edge.predicate in ("enables", "leads_to", "produces"):
        parts.append(f"{edge.subject} enables {edge.obj}")
    elif edge.predicate in ("requires", "depends_on"):
        parts.append(f"{edge.obj} is required for {edge.subject}")
    elif edge.predicate == "next_step":
        parts.append(f"next step after {edge.subject}")
    else:
        parts.append(f"{edge.subject} {edge.predicate} {edge.obj}")
    if a_subj > 0.7:
        parts.append("(high activation)")
    return "; ".join(parts)
