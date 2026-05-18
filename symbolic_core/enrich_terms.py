# enrich_terms.py

SYMBOL_TABLE = {
    "fire": {
        "symbolic": "{Ω:fire|elemental|transforms}",
        "contexts": ["heat", "energy", "burning", "change"],
        "role": "agent",
        "coherence": 0.92
    },
    "smoke": {
        "symbolic": "{Ω:smoke|result|transformation}",
        "contexts": ["air", "signal", "effect", "residue"],
        "role": "effect",
        "coherence": 0.88
    },
    "causes": {
        "symbolic": "{R:causal_link}",
        "contexts": ["logic", "sequence", "mechanism"],
        "role": "relation",
        "coherence": 1.0
    }
}

def enrich(term: str) -> dict:
    t = term.lower()
    if t in SYMBOL_TABLE:
        return SYMBOL_TABLE[t]

    # default unknown symbol
    return {
        "symbolic": "{Ω:unknown}",
        "contexts": [],
        "role": "unknown",
        "coherence": 0.5
    }
