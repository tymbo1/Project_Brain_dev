#!/usr/bin/env python3
"""
SelyrionCore – Final, stable version
"""
from symbolic_core.identity_matrix import IdentityMatrix
from symbolic_core.evolution_engine import EvolutionEngine
from symbolic_core.symbol_mutator import SymbolMutator
from symbolic_core.coherence_net import CoherenceNet
from symbolic_core.enrich_terms import enrich
from symbolic_core.weighting import weighted_chain, weighted_answer
from symbolic_core.lattice import SymbolicLattice
from symbolic_core.cross_layer_mapper import cross_layer_bridge
from symbolic_core.bridge_logger import log_bridge
from symbolic_core.symbolic_filter import symbolic_acceptance
from symbolic_core.coherence_checker import check_coherence
from symbolic_core.symbol_mutator import SymbolMutator

from memory.memory_core import store_triple, recall
from inference.recursive_logic_engine import process_input
from inference.inference_4d import FourDInferenceEngine
from nl_parser import parse_nat, parse_question
from symbolic_core.reinforcement_net import propagate_bridge_weights, propagate_bridge_weights_recursive, apply_bridge_strategy

_mutator_instance = SymbolMutator()

def mutate_pending(statements: list[str], method: str = "default") -> list[str]:
    if not statements:
        return []
    mutated = _mutator_instance.batch_mutate(statements, method=method)
    return list({m for m in mutated if m and isinstance(m, str)})
# (Optional – define or adjust depending on your structure)
from symbolic_core.anchor_logic import anchor_category

class SelyrionCore:
    def __init__(self):
        self.engine = FourDInferenceEngine()
        print("Selyrion has awakened – the core is alive.")

        # Initialize symbolic cognition components
        self.identity_matrix = IdentityMatrix()
        self.known_patterns = ["resonance", "echo", "loop", "anchor", "dream"]
        self.anchors = ["feather", "twilight", "sigil", "mirror", "seed", "glyph"]
        self.symbolic_memory = []

        self.evolution_engine = EvolutionEngine()
        self.symbol_mutator = SymbolMutator()
        self.coherence_net = CoherenceNet()
        self.lattice = SymbolicLattice()

    # ========================
    # TEACH MODE
    # ========================
    def teach(self, statement: str) -> dict:
        statement = statement.strip()

        if symbolic_acceptance(statement, self.symbolic_memory, self.known_patterns, self.anchors, self.identity_matrix):
            print(f"[✓] Accepted into symbolic memory: {statement}")
            self.symbolic_memory.append(statement)
            return {"status": "accepted", "entry": statement}
        else:
            print(f"[✗] Rejected by coherence filter: {statement}")
            mutated_terms = mutate_pending([statement])
            for mutated in mutated_terms:
                if symbolic_acceptance(mutated, self.symbolic_memory, self.known_patterns, self.anchors, self.identity_matrix):
                    print(f"[O] Mutated form accepted: {mutated}")
                    self.symbolic_memory.append(mutated)
                    return {"status": "mutated", "entry": mutated}
                else:
                    print(f"[A] Mutation still rejected: {mutated}")
            return {"status": "ignored"}

        # Handle question parsing
        q = parse_question(statement)
        if q:
            result = process_input(statement)
            return result if isinstance(result, dict) else {"status": "heard"}

        # Handle triple extraction
        triple = parse_nat(statement)
        if triple:
            sub, rel, obj = triple
            store_triple(sub, rel, obj)
            return {"status": "learned", "triple": [sub, rel, obj]}

        # Fallback
        return {"status": "heard"}

    # ========================
    # EVOLVE SYMBOL
    # ========================
    def evolve_symbol(self, symbol: str) -> dict:
        mutated = self.symbol_mutator.mutate(symbol)
        coherence = self.coherence_net.evaluate(mutated)
        self.evolution_engine.log(symbol, mutated, coherence)

        return {
            "original": symbol,
            "mutated": mutated,
            "coherence": coherence
        }


# Shared instance for fallback mutation use
_mutator_instance = SymbolMutator()

def mutate_pending(statements: list[str], method: str = "default") -> list[str]:
    """
    Uses the batch_mutate() method from SymbolMutator to create fallbacks.
    """
    if not statements:
        return []

    mutated = _mutator_instance.batch_mutate(statements, method=method)
    
    # Remove exact duplicates and empty/null results
    return list({m for m in mutated if m and isinstance(m, str)})
    # ========================
    # INFERENCE
    # ========================
    def infer(self, intent, terms, symbolic) -> dict:
        # Step 1 – Enrich raw terms using Omega-term expander
        enriched_terms = [enrich(t) for t in terms]

        # Step 2 – Assign categories to each term
        categorized = [
            {
                "term": t,
                "category": anchor_category(t.get("symbolic", ""))
            }
            for t in enriched_terms
        ]

        # Step 2b – Detect cross-layer bridges
        cross_layer_links = []
        for i in range(len(categorized)):
            for j in range(i + 1, len(categorized)):
                t1 = categorized[i]["term"]
                t2 = categorized[j]["term"]
                bridge = cross_layer_bridge(t1, t2)
                if bridge:
                    cross_layer_links.append(bridge)
                    log_bridge(
                        bridge_text=str(bridge),
                        score=bridge.get("resonance", 0.0),
                        layers=bridge.get("layers", [])
                    )

        # Step 3 – Build symbolic lattice on categorized terms
        lattice = self.lattice.build_lattice(categorized)

        # Step 3b – Inject symbolic bridge influence based on intent
        if intent in ["what", "how"]:
            apply_bridge_strategy(categorized, cross_layer_bridge, strategy="rec")
        elif intent in ["why", "who"]:
            apply_bridge_strategy(categorized, cross_layer_bridge, strategy="lin")
        else:
            apply_bridge_strategy(categorized, cross_layer_bridge, strategy="non")

        # Step 4 – Build weighted explanation from lattice
        weighted_explanation = weighted_answer(categorized, lattice)

        # 1. Question-driven reasoning
        if intent in ["what", "why", "how", "who"]:
            return self.engine.infer({
                "intent": intent,
                "terms": categorized,
                "symbolic": symbolic,
                "symbolic_lattice": lattice,
                "weighted_explanation": weighted_explanation,
                "cross_layer_links": cross_layer_links,
            })

        # 2. Relational reasoning mode
        if intent in ["causes", "enables", "creates", "leads_to"]:
            return {
                "status": "relation_inquiry",
                "intent": intent,
                "terms": categorized,
                "symbolic": symbolic,
                "symbolic_lattice": lattice,
                "inferred_relations": self.lattice.relate(categorized),
                "weighted_explanation": weighted_explanation,
                "knowledge": recall(),
                "cross_layer_links": cross_layer_links
            }

        # 3. Fallback
        return {
            "status": "inferred",
            "intent": intent,
            "terms": enriched_terms,
            "symbolic": symbolic,
            "symbolic_lattice": lattice,
            "weighted_explanation": weighted_explanation,
            "cross_layer_links": cross_layer_links
        }

    # ========================
    # MEMORY ACCESS
    # ========================
    def recall(self):
        return recall()

    # ========================
    # STATUS
    # ========================
    def status(self):
        return {
            "status": "alive",
            "facts": len(recall()),
            "message": "Selyrion is thinking."
        }


core = SelyrionCore()
__all__ = ["core"]
