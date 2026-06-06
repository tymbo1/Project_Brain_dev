"""
response_planner.py — Response planner operator.

Assembles a ResponsePlan from operator outputs.
Only sends to LangEng if PlanQuality ≥ θ_plan.

PlanQuality =
  q₁·IntentFit
+ q₂·Completeness
+ q₃·Confidence
+ q₄·Continuity
- q₅·UnsupportedClaims
- q₆·SecurityRisk

ResponsePlan:
{
  "speech_act":      "PROJECT_RECALL|DEFINE|EXPLAIN|COMPARE|PLAN_NEXT|UNCERTAIN",
  "subject":         "...",
  "claims":          [],
  "evidence":        [],
  "uncertainties":   [],
  "next_steps":      [],
  "tone":            "companion_research|factual|cautious",
  "security_level":  "public_safe|user_personal|admin_only",
  "provenance":      {},
  "confidence":      0.0,
  "plan_quality":    0.0,
  "ready_for_langeng": bool
}
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Any

# ── Weights ───────────────────────────────────────────────────────────────────
Q1 = 0.25   # IntentFit
Q2 = 0.25   # Completeness
Q3 = 0.20   # Confidence
Q4 = 0.15   # Continuity (conversation coherence)
Q5 = 0.10   # UnsupportedClaims penalty
Q6 = 0.05   # SecurityRisk penalty

THETA_PLAN = 0.40   # minimum plan quality to send to LangEng

# ── Lane → default tone ───────────────────────────────────────────────────────
_LANE_TONE = {
    "identity":     "reflective_identity",
    "relationship": "companion_warm",
    "project":      "companion_research",
    "knowledge":    "factual_grounded",
}

# ── Operator → speech act ─────────────────────────────────────────────────────
_OP_SPEECH_ACT = {
    "DEFINE":             "DEFINITION",
    "EXPLAIN":            "EXPLANATION",
    "COMPARE":            "COMPARISON",
    "RECALL_IDENTITY":    "IDENTITY_RECALL",
    "RECALL_RELATIONSHIP":"RELATIONSHIP_RECALL",
    "RECALL_PROJECT":     "PROJECT_RECALL",
    "TRACE_CAUSE":        "CAUSAL_TRACE",
    "FIND_GAPS":          "GAP_ANALYSIS",
    "CHECK_CONTRADICTION":"CONTRADICTION_CHECK",
    "PLAN_NEXT":          "PLAN",
    "ASSESS_CONFIDENCE":  "CONFIDENCE_REPORT",
    "ANSWER_UNCERTAIN":   "UNCERTAIN",
    "REFUSE_PROTECTED":   "REFUSE",
}


@dataclass
class ResponsePlan:
    speech_act: str = "UNCERTAIN"
    subject: str = ""
    claims: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    tone: str = "factual_grounded"
    security_level: str = "public_safe"
    provenance: dict = field(default_factory=dict)
    confidence: float = 0.0
    plan_quality: float = 0.0
    ready_for_langeng: bool = False
    operator_used: str = ""
    lane: str = "knowledge"
    # Raw operator output kept for LangEng
    operator_output: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "speech_act":        self.speech_act,
            "subject":           self.subject,
            "claims":            self.claims,
            "evidence":          self.evidence,
            "uncertainties":     self.uncertainties,
            "next_steps":        self.next_steps,
            "tone":              self.tone,
            "security_level":    self.security_level,
            "confidence":        round(self.confidence, 3),
            "plan_quality":      round(self.plan_quality, 3),
            "ready_for_langeng": self.ready_for_langeng,
            "operator_used":     self.operator_used,
        }

    def to_substrate_text(self) -> str:
        """Serialize plan as plain substrate text for Qwen rewrite or direct output."""
        lines = []
        if self.speech_act == "UNCERTAIN":
            return "I don't have that in my memory right now."

        if self.claims:
            lines.append("\n".join(self.claims))
        if self.uncertainties:
            lines.append("Uncertain: " + "; ".join(self.uncertainties))
        if self.next_steps:
            lines.append("Next steps: " + "; ".join(self.next_steps))
        if self.evidence:
            lines.append("Evidence: " + " / ".join(self.evidence[:3]))
        return "\n\n".join(lines) if lines else "I don't have specific information about that."


class ResponsePlanner:

    def build(
        self,
        operator_name: str,
        operator_output: dict,
        lane: str = "knowledge",
        conversation_state: dict | None = None,
    ) -> ResponsePlan:
        """
        Assemble a ResponsePlan from a single operator's output.

        Args:
            operator_name:   e.g. "DEFINE", "RECALL_PROJECT"
            operator_output: The .as_dict() result from the operator
            lane:            Memory lane that was active
            conversation_state: dict with keys like last_speech_act, depth, etc.
        """
        state = conversation_state or {}
        plan = ResponsePlan(
            speech_act=_OP_SPEECH_ACT.get(operator_name, "UNCERTAIN"),
            subject=operator_output.get("subject", ""),
            tone=_LANE_TONE.get(lane, "factual_grounded"),
            security_level=_security_level(lane, operator_name),
            operator_used=operator_name,
            lane=lane,
            operator_output=operator_output,
            confidence=operator_output.get("confidence", 0.0),
        )

        # ── Populate fields from operator output ──────────────────────────────
        self._fill_claims(plan, operator_name, operator_output)
        self._fill_evidence(plan, operator_name, operator_output)
        self._fill_next_steps(plan, operator_name, operator_output)
        self._fill_uncertainties(plan, operator_name, operator_output)

        # ── Plan quality scoring ──────────────────────────────────────────────
        plan.plan_quality = self._score_plan(plan, operator_output, state)
        plan.ready_for_langeng = plan.plan_quality >= THETA_PLAN

        return plan

    def _fill_claims(self, plan: ResponsePlan, op: str, out: dict) -> None:
        if op == "DEFINE":
            if out.get("type"):
                plan.claims.append(f"{plan.subject} is a {out['type']}")
            if out.get("definition"):
                plan.claims.append(out["definition"])
            if out.get("properties"):
                plan.claims.extend([f"property: {p}" for p in out["properties"][:4]])

        elif op in ("RECALL_PROJECT", "RECALL_IDENTITY", "RECALL_RELATIONSHIP"):
            if out.get("definition"):
                plan.claims.append(out["definition"])
            if out.get("current_state"):
                plan.claims.append(out["current_state"])
            if out.get("history"):
                plan.claims.extend(out["history"][:3])

        elif op == "EXPLAIN":
            plan.claims = out.get("causal_chain", []) or out.get("claims", [])

        elif op == "COMPARE":
            a, b = out.get("subject_a", ""), out.get("subject_b", "")
            verdict = out.get("verdict", "")
            sim = out.get("similarity", 0.0)
            if verdict and a and b:
                plan.claims.append(f"{a} and {b} are {verdict} (similarity={sim:.2f})")
            for f in (out.get("shared") or [])[:3]:
                if isinstance(f, dict):
                    plan.claims.append(f"shared: {f.get('predicate')} {f.get('value')}")
            for f in (out.get("only_a") or [])[:2]:
                if isinstance(f, dict):
                    plan.claims.append(f"only {a}: {f.get('predicate')} {f.get('value')}")
            for f in (out.get("only_b") or [])[:2]:
                if isinstance(f, dict):
                    plan.claims.append(f"only {b}: {f.get('predicate')} {f.get('value')}")

        elif op == "PLAN_NEXT":
            for action in (out.get("actions") or [])[:5]:
                if isinstance(action, dict):
                    plan.claims.append(action.get("action", ""))
                else:
                    plan.claims.append(str(action))

        elif op == "CHECK_CONTRADICTION":
            plan.claims.append(
                f"Status: {out.get('status', 'insufficient_memory')}"
            )
            plan.claims.extend(out.get("supporting_evidence", [])[:2])

        elif op == "FIND_GAPS":
            for g in (out.get("missing") or [])[:4]:
                if isinstance(g, dict):
                    plan.claims.append(g.get("item", ""))
                else:
                    plan.claims.append(str(g))

        elif op == "ANSWER_UNCERTAIN":
            plan.claims = []

        plan.claims = [c for c in plan.claims if c and len(c.strip()) > 2]

    def _fill_evidence(self, plan: ResponsePlan, op: str, out: dict) -> None:
        if op == "CHECK_CONTRADICTION":
            plan.evidence.extend(out.get("supporting_evidence", [])[:3])
            plan.evidence.extend(out.get("contradicting_evidence", [])[:2])
        elif op == "DEFINE":
            plan.evidence = [f"{p}" for p in (out.get("related") or [])[:5]]
        elif op == "RECALL_PROJECT":
            plan.evidence = out.get("provenance", [])[:3] if isinstance(out.get("provenance"), list) else []
        plan.evidence = [e for e in plan.evidence if e]

    def _fill_next_steps(self, plan: ResponsePlan, op: str, out: dict) -> None:
        if op == "PLAN_NEXT":
            for action in (out.get("actions") or [])[:5]:
                if isinstance(action, dict):
                    plan.next_steps.append(action.get("action", ""))
                else:
                    plan.next_steps.append(str(action))
        elif op in ("RECALL_PROJECT",):
            plan.next_steps.extend((out.get("next_steps") or [])[:4])
        plan.next_steps = [s for s in plan.next_steps if s]

    def _fill_uncertainties(self, plan: ResponsePlan, op: str, out: dict) -> None:
        plan.uncertainties.extend((out.get("uncertainty") or out.get("uncertainties") or [])[:3])
        if op == "CHECK_CONTRADICTION" and out.get("status") == "insufficient_memory":
            plan.uncertainties.append("insufficient memory to verify claim")
        tier = out.get("epistemic_tier", "")
        if tier == "hypothesis":
            plan.uncertainties.append("this is a theoretical framework, not established science")
        elif tier == "working_model":
            plan.uncertainties.append("this is a working model, subject to revision")
        plan.uncertainties = [u for u in plan.uncertainties if u]

    def _score_plan(self, plan: ResponsePlan, out: dict, state: dict) -> float:
        # IntentFit: did operator match the speech act well?
        intent_fit = 1.0 if plan.speech_act != "UNCERTAIN" else 0.2
        # Completeness: how many claim slots are filled?
        completeness = out.get("completeness", min(len(plan.claims) / 5.0, 1.0))
        # Confidence
        conf = plan.confidence
        # Continuity
        last_act = state.get("last_speech_act", "")
        continuity = 0.7 if last_act and _acts_are_related(last_act, plan.speech_act) else 0.5
        # Unsupported claims (proxy: uncertainty count)
        unsupported = min(len(plan.uncertainties) * 0.2, 1.0)
        # Security risk
        sec_risk = 0.0 if plan.security_level == "public_safe" else 0.2

        quality = (
            Q1 * intent_fit
          + Q2 * completeness
          + Q3 * conf
          + Q4 * continuity
          - Q5 * unsupported
          - Q6 * sec_risk
        )
        return round(min(max(quality, 0.0), 1.0), 3)


def _security_level(lane: str, operator: str) -> str:
    if operator == "REFUSE_PROTECTED":
        return "admin_only"
    if lane in ("identity", "relationship"):
        return "user_personal"
    return "public_safe"


def _acts_are_related(a: str, b: str) -> bool:
    _related_pairs = {
        ("DEFINITION", "EXPLANATION"),
        ("EXPLANATION", "CAUSAL_TRACE"),
        ("PROJECT_RECALL", "PLAN"),
        ("PROJECT_RECALL", "GAP_ANALYSIS"),
        ("DEFINITION", "COMPARISON"),
    }
    return (a, b) in _related_pairs or (b, a) in _related_pairs


# ── Module-level convenience ──────────────────────────────────────────────────

_planner = ResponsePlanner()

def build_plan(
    operator_name: str,
    operator_output: dict,
    lane: str = "knowledge",
    conversation_state: dict | None = None,
) -> ResponsePlan:
    return _planner.build(operator_name, operator_output, lane, conversation_state)
