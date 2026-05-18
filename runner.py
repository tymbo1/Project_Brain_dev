#!/data/data/com.termux/files/usr/bin/python
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path: sys.path.insert(0, script_dir)

from recursive_logic_engine import RecursiveLogicEngine
from symbol_inference import base_symbolic_relation_rule
from directive_input import get_directive

engine = RecursiveLogicEngine()
engine.relate("love", "compassion")
engine.relate("fear", "defense")
engine.relate("hope", "vision")
engine.add_rule(base_symbolic_relation_rule)

directive = get_directive()
results = engine.infer(directive)

print("\\nSymbolic Inference Output:")
for r in sorted(results):
    print(" •", r)
