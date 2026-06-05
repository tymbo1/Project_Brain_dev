"""
cognitive_operators — explicit symbolic reasoning layer between activation and expression.

Pipeline:
  query
  → LaneScore(l|q)         [memory_router]
  → A₀(v|q) + Aₜ(v)       [activation_engine]
  → K(q)                   [working_memory.WorkingMemoryPacket]
  → OpScore(o|q,K,l*)      [operator_selector.OperatorSelector]
  → OperatorOutput(o,K)    [define / recall_project / plan_next / ...]
  → Conf(answer)           [assess_confidence]
  → ResponsePlan           [response_planner.ResponsePlanner]
  → LangEng(ResponsePlan)
"""

from .working_memory   import WorkingMemoryPacket, build_packet
from .operator_selector import OperatorSelector, select_operator
from .response_planner  import ResponsePlanner, ResponsePlan

__all__ = [
    "WorkingMemoryPacket", "build_packet",
    "OperatorSelector", "select_operator",
    "ResponsePlanner", "ResponsePlan",
]
