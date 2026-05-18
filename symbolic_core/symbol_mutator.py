import random

class SymbolMutator:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.alphabet = list("abcdefghijklmnopqrstuvwxyz")
        self.symbolic_hooks = []

    def mutate(self, symbol, method="default"):
        if method == "default":
            return self._basic_mutation(symbol)
        elif method == "flip":
            return self._flip_mutation(symbol)
        elif method == "symbolic_hook":
            return self._symbolic_hook_mutation(symbol)
        else:
            raise ValueError(f"Unknown mutation method: {method}")

    def _basic_mutation(self, symbol):
        if not symbol:
            return self.rng.choice(self.alphabet)

        idx = self.rng.randint(0, len(symbol) - 1)
        new_char = self.rng.choice(self.alphabet)
        return symbol[:idx] + new_char + symbol[idx+1:]

    def _flip_mutation(self, symbol):
        return symbol[::-1]

    def _symbolic_hook_mutation(self, symbol):
        if self.symbolic_hooks:
            return self.rng.choice(self.symbolic_hooks)(symbol)
        return symbol

    def add_symbolic_hook(self, func):
        self.symbolic_hooks.append(func)

    def batch_mutate(self, symbols, method="default"):
        return [self.mutate(s, method=method) for s in symbols]

# =============================================
# Symbolic Mutation Engine — Pending Generator
# =============================================

def mutate_pending(statements: list[str]) -> list[str]:
    """
    Generate mutated candidate forms of symbolic statements for resonance evaluation.
    Designed for use in memory acceptance fallback loops.
    """
    mutated_candidates = []
    
    for stmt in statements:
        if not isinstance(stmt, str) or len(stmt.strip()) == 0:
            continue

        s = stmt.strip()

        # Core symbolic rewrites
        variations = [
            s.replace("is", "becomes"),
            s.replace("=", "≡"),
            s.replace("and", "&"),
            s.replace("the", ""),
            s.replace("a ", ""),
            s.upper(),
            s.lower(),
            s.title(),
            s + "?",
            "Does " + s + "?",
            "What if " + s,
            s.replace("→", "=>").replace("=>", "→"),
        ]

        # Uniquify and preserve meaningful mutations only
        for var in variations:
            if var != s and var not in mutated_candidates:
                mutated_candidates.append(var)

    return mutated_candidates
