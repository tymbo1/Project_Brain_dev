# === symbol_inference.py ===
import random
from symbolic_core.evolution_engine import mutate_symbol
from symbolic_core.coherence_net import evaluate_seed

def symbolic_infer(phrase: str) -> dict:
    """
    Perform a symbolic inference on a natural language phrase.
    Returns a dictionary with symbolic output.
    """
    # Mutate symbolically for creative variation
    mutated = mutate_symbol(phrase)

    # Evaluate coherence
    eval_result = evaluate_seed(mutated)

    # Return inference result
    return {
        "original": phrase,
        "inferred": mutated,
        "coherence_score": eval_result.get("coherence_score", 0.0),
        "contradiction": eval_result.get("contradiction", False),
        "glyph": generate_glyph(mutated)
    }

def generate_glyph(text):
    """
    Return a simple visual symbol or glyph from the mutated phrase.
    (Later to be expanded with proper mapping layer)
    """
    base_glyphs = ['♁', '☯', '⚶', '⟁', '𒆙', '🜂', '🜁', '🜃', '🜄', '🧿']
    return random.choice(base_glyphs)
