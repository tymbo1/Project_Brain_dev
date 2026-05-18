from symbolic_core.symbol_mutator import mutate_symbol
from symbolic_core.four_d_inference import infer

def test_strength_range(symbol, min_val=1, max_val=100, step=10):
    results = []
    for strength in range(min_val, max_val + 1, step):
        mutated = mutate_symbol(symbol, strength=strength)
        inference = infer(mutated)
        results.append((strength, inference))
    return results
