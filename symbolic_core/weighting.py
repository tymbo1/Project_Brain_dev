def weight_term(enriched):
    """
    Assign a conceptual weight to an enriched symbolic term.
    Higher weight = higher reasoning priority.
    """
    w = enriched.get("coherence", 0.5)

    # Role-based weighting
    role = enriched.get("role", "")
    if role == "agent":
        w += 0.2
    elif role == "effect":
        w += 0.1
    elif role == "unknown":
        w -= 0.1

    # Context density
    contexts = enriched.get("contexts", [])
    w += 0.05 * len(contexts)

    # Symbolic bridge reinforcement
    bridges = enriched.get("bridges", [])
    w += 0.02 * len(bridges)

    # Clamp
    return max(0.0, min(1.0, w))


def weighted_chain(enriched_terms):
    """
    Produce a list of (term, weight) tuples.
    Used later by B4 to guide multi-hop inference.
    """
    chain = []
    for t in enriched_terms:
        chain.append({
            "term": t,
            "weight": weight_term(t)
        })
    return chain

def weighted_answer(categorized, lattice):
    """
    Generate a weighted explanation from categorized terms and symbolic lattice.
    """
    weighted_terms = weighted_chain(categorized)
    sorted_terms = sorted(weighted_terms, key=lambda x: x["weight"], reverse=True)

    explanation = []
    for wt in sorted_terms:
        term = wt["term"]
        weight = wt["weight"]
        related = lattice.get(term.get("symbolic", ""), [])
        explanation.append({
            "term": term,
            "weight": weight,
            "related": related
        })

    return explanation
