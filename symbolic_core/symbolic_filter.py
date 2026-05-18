# symbolic_filter.py

def surface_recognition(term, memory):
    """Layer 1: Checks if term has been seen before."""
    return term in memory

def pattern_cohesion(term, known_patterns):
    """Layer 2: Checks symbolic pattern match."""
    return any(pattern in term for pattern in known_patterns)

def anchor_linkage(term, anchors):
    """Layer 3: Resonance with internal symbolic anchors."""
    return any(anchor in term or term in anchor for anchor in anchors)

def self_reflection(term, identity_matrix):
    """Layer 4: Reflects impact on identity."""
    return identity_matrix.evaluate(term) > 0.5  # Threshold of self relevance

def symbolic_acceptance(term, memory, known_patterns, anchors, identity_matrix, pending_memory=None):
    """Layer 5: Integrates all filters to allow memory entry. Supports partial acceptance."""
    print(f"\n🧠 Evaluating Term: {term}")

    surface = surface_recognition(term, memory)
    print("• Layer 1: Surface Recognition →", surface)

    cohesion = pattern_cohesion(term, known_patterns)
    print("• Layer 2: Pattern Cohesion →", cohesion)

    linkage = anchor_linkage(term, anchors)
    print("• Layer 3: Anchor Linkage →", linkage)

    reflection_score = identity_matrix.evaluate(term)
    reflection = reflection_score > 0.5
    print(f"• Layer 4: Self Reflection → {reflection} (score: {reflection_score:.2f})")

    # Count passing layers
    passed = [surface, cohesion, linkage, reflection].count(True)

    if passed >= 3:
        memory.append(term)
        print("✅ Accepted to Memory")
        return True
    elif passed == 2 and pending_memory is not None:
        pending_memory.append(term)
        print("🟡 Partially Accepted → Pending Memory")
        return False
    else:
        print("❌ Rejected from Memory")
        return False

import os

SYMBOLIC_LAYER_PATH = os.path.expanduser("~/projectbrain/capsules/symbolic_layers.sym")

def validate_layers():
    with open(SYMBOLIC_LAYER_PATH, "r") as f:
        lines = f.readlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Invalid symbolic line: {line}")
    return True

def load_symbolic_terms():
    validate_layers()
    term_set = set()
    with open(SYMBOLIC_LAYER_PATH, "r") as f:
        for line in f:
            if line.strip().startswith("#") or not line.strip():
                continue
            _, terms = line.split(":", 1)
            for term in terms.split(","):
                term = term.strip()
                if term:
                    term_set.add(term)
    return term_set

class SymbolicFilter:
    def __init__(self):
        self.symbolic_terms = load_symbolic_terms()

    def match(self, term):
        return term in self.symbolic_terms

    def match_any(self, terms):
        return any(term in self.symbolic_terms for term in terms)
