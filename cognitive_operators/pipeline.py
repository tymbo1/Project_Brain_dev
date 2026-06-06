"""
pipeline.py — Full cognitive operator pipeline.

Response =
  LangEng(
    Planner(
      Operators(
        WorkingMemory(
          Activation(
            Router(q)
          )
        )
      )
    )
  )

Usage:
    from cognitive_operators.pipeline import run_pipeline

    plan = run_pipeline(
        query="what is TLST",
        chains=[...],          # from activation_engine.infer()
        source_lane="project",
        conversation_state={},
    )

    # plan.ready_for_langeng → bool
    # plan.to_substrate_text() → str for Qwen rewrite
    # plan.as_dict() → dict for LangEng template selection
"""

from __future__ import annotations
from .working_memory    import build_packet, WorkingMemoryPacket
from .operator_selector import select_operator, OperatorSelector
from .response_planner  import ResponsePlanner, ResponsePlan, build_plan
from . import define, explain, compare, find_gaps, recall_project, recall_identity, plan_next, check_contradiction, assess_confidence

_selector = OperatorSelector()
_planner  = ResponsePlanner()


def run_pipeline(
    query: str,
    chains: list[str],
    source_lane: str = "knowledge",
    operator_hint: str = "",
    missing_requirements: list[str] | None = None,
    conversation_state: dict | None = None,
    goals: list[str] | None = None,
    claim: str = "",
) -> ResponsePlan:
    """
    Full pipeline: chains → WorkingMemoryPacket → operator → ResponsePlan.

    Returns a ResponsePlan. Check plan.ready_for_langeng before sending to LangEng.
    If False, plan.to_substrate_text() gives an uncertainty / no-memory fallback.
    """
    state = conversation_state or {}

    # ── 1. Build working memory packet ───────────────────────────────────────
    packet = build_packet(
        query=query,
        chains=chains,
        source_lane=source_lane,
        operator_hint=operator_hint,
        missing_requirements=missing_requirements,
    )

    # ── 2. Select operator ────────────────────────────────────────────────────
    op_name = select_operator(query, packet, state)

    # ── 3. Run operator ───────────────────────────────────────────────────────
    op_out = _run_operator(op_name, packet, query, goals, claim)

    # ── 4. Assess confidence ──────────────────────────────────────────────────
    contra_score = 0.0
    if op_name == "CHECK_CONTRADICTION":
        contra_score = op_out.get("contradiction_score", 0.0)

    conf_result = assess_confidence.run(
        packet,
        contradiction_score=contra_score,
    )

    # Blend operator confidence with packet confidence
    if op_out.get("confidence", 0.0) > 0:
        blended_conf = op_out["confidence"] * 0.7 + conf_result.confidence * 0.3
        op_out["confidence"] = round(blended_conf, 3)
    else:
        op_out["confidence"] = conf_result.confidence

    # ── 5. Build response plan ────────────────────────────────────────────────
    plan = _planner.build(
        operator_name=op_name,
        operator_output=op_out,
        lane=source_lane,
        conversation_state=state,
    )

    # Attach uncertainty label from confidence assessment
    if conf_result.uncertainty_label in ("uncertain", "no_memory"):
        plan.uncertainties.insert(0, f"confidence: {conf_result.uncertainty_label}")

    return plan


def _run_operator(
    op_name: str,
    packet: WorkingMemoryPacket,
    query: str,
    goals: list[str] | None,
    claim: str,
) -> dict:
    """Dispatch to the correct operator module. Returns .as_dict()."""

    if op_name == "DEFINE":
        return define.run(packet).as_dict()

    elif op_name == "RECALL_IDENTITY":
        return recall_identity.run(query=query).as_dict()

    elif op_name in ("RECALL_PROJECT", "RECALL_RELATIONSHIP"):
        return recall_project.run(packet, query=query).as_dict()

    elif op_name == "PLAN_NEXT":
        return plan_next.run(packet, goals=goals).as_dict()

    elif op_name == "CHECK_CONTRADICTION":
        return check_contradiction.run(packet, claim=claim or query).as_dict()

    elif op_name == "ASSESS_CONFIDENCE":
        return assess_confidence.run(packet).as_dict()

    elif op_name == "ANSWER_UNCERTAIN":
        return {
            "operator":   "ANSWER_UNCERTAIN",
            "subject":    query,
            "confidence": 0.0,
            "completeness": 0.0,
        }

    elif op_name == "REFUSE_PROTECTED":
        return {
            "operator":   "REFUSE_PROTECTED",
            "subject":    query,
            "confidence": 0.0,
        }

    elif op_name == "EXPLAIN":
        return explain.run(packet).as_dict()

    elif op_name == "FIND_GAPS":
        return find_gaps.run(packet).as_dict()

    elif op_name == "COMPARE":
        return compare.run(packet).as_dict()

    else:
        # TRACE_CAUSE — stub until built
        # Fall through to DEFINE as best available approximation
        return define.run(packet).as_dict()
