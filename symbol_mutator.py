# symbolic_mutator.py

import random

synonyms = {
    "fire": ["flame", "burn", "ignite"],
    "energy": ["force", "power", "heat"],
    "myth": ["legend", "tale", "symbol"]
}

def mutate_term(term):
    words = term.lower().split()
    mutated = []

    for word in words:
        if word in synonyms:
            mutated.append(random.choice(synonyms[word]))
        else:
            mutated.append(word)

    return " ".join(mutated)

def mutate_pending(pending_memory):
    """Returns list of mutated forms."""
    return [mutate_term(term) for term in pending_memory]

if __name__ == "__main__":
    test_input = ["fire energy", "myth fire"]
    result = mutate_pending(test_input)
    print("🔁 Mutated Terms:", result)
