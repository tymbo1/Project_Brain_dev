"""codeops/promotion_policy.py — Phase D1 truth-state ladder.

Maps §9 execution-trace verdicts to selyrioncode.codeunits.truth_state per
Selyrion adapter §4.2 enum:
    proposed, plausible, verified_static, verified_runtime,
    regression_tested, benchmarked, deprecated, quarantined, failed

Doctrine:
    - failed_* verdicts demote: truth_state -> failed regardless of prior.
    - passed_* verdicts promote: monotonic on the ladder; never demote a higher
      state because of a weaker passing verdict.
    - deprecated / quarantined are HITL terminal states; policy never touches.
"""
from __future__ import annotations

LADDER = (
    "proposed", "plausible", "verified_static",
    "verified_runtime", "regression_tested", "benchmarked",
)
TERMINAL_HITL = ("deprecated", "quarantined")

_RANK = {state: i for i, state in enumerate(LADDER)}

VERDICT_TO_STATE = {
    "passed_minimal":      "verified_runtime",
    "passed_verified":     "regression_tested",
    "passed_benchmarked":  "benchmarked",
}
FAIL_VERDICTS = {"failed_parse", "failed_static", "failed_runtime"}


def decide(current_state: str | None, latest_verdict: str | None) -> str | None:
    """Return new truth_state for a codeunit given its current state and the
    latest verdict observed from execution_traces. Returns None if no change.
    """
    if current_state in TERMINAL_HITL:
        return None
    if latest_verdict is None:
        return None

    if latest_verdict in FAIL_VERDICTS:
        return "failed" if current_state != "failed" else None

    target = VERDICT_TO_STATE.get(latest_verdict)
    if target is None:
        return None

    cur_rank = _RANK.get(current_state, -1)
    tgt_rank = _RANK[target]
    if cur_rank >= tgt_rank:
        return None
    return target
