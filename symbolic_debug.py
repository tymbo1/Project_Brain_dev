import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "symbolic_core"))

from symbolic_filter import symbolic_acceptance, surface_recognition, pattern_cohesion, anchor_linkage, self_reflection
from dummy_matrix import DummyIdentityMatrix  # if needed

print("\n🔬 SYMBOLIC FILTER DEBUGGING\n")

test_terms = [
    "Ω:fire_elemental|transforms",
    "Σ:what|is_a|fire",
    "heat is energy",
    "unknown term of myth"
]

memory = []
pending = []

known_patterns = ["fire", "energy", "burning"]
anchors = ["Ω:fire", "Σ:heat", "Ω:transform"]
identity_matrix = DummyIdentityMatrix()

for term in test_terms:
    symbolic_acceptance(term, memory, known_patterns, anchors, identity_matrix, pending_memory=pending)
    print("-" * 50)

print("\n📦 Final Memory State:", memory)
print("🕐 Pending Memory State:", pending)
