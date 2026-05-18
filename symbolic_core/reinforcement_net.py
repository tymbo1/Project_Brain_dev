# reinforcement_net.py

"""
Bridge Weight Propagation Module for Symbolic SSAI
Applies cross-layer resonance influence recursively or linearly
to adjust symbolic term weights and reinforce reasoning depth.
"""

def propagate_bridge_weights(categorized_terms, bridge_function):
    """
    Adjusts weights of categorized symbolic terms using bridge influence.
    
    Args:
        categorized_terms (List[Dict]): Symbolically enriched terms with 'term' and optional 'weight'.
        bridge_function (Callable): Function to compute bridges between term pairs.
    """
    for i, t1 in enumerate(categorized_terms):
        for j, t2 in enumerate(categorized_terms):
            if i == j:
                continue
            bridge = bridge_function(t1["term"], t2["term"])
            if not bridge:
                continue

            # Influence: Use resonance to amplify or modulate weights
            influence = bridge.get("resonance", 0.1)
            t1["weight"] = t1.get("weight", 0.5) + influence

            # Optional: Log or store the bridge
            t1.setdefault("bridges", []).append(bridge)

def propagate_bridge_weights_recursive(categorized, cross_layer_bridge, depth_limit=3):
    """
    Recursively propagate symbolic bridge influence across categorized terms.
    Each bridge adds 'resonance' to the term's weight, cascading up to depth_limit.
    """

    def propagate(term, visited, depth):
        if depth > depth_limit:
            return 0

        influence_total = 0.0

        for other in categorized:
            if term is other or id(other) in visited:
                continue

            bridge = cross_layer_bridge(term["term"], other["term"])
            if not bridge:
                continue

            resonance = bridge.get("resonance", 0.1)
            influence_total += resonance

            other_weight = other.get("weight", 0.5)
            other["weight"] = other_weight + resonance
            other.setdefault("bridges", []).append(bridge)

            visited.add(id(term))
            influence_total += propagate(other, visited, depth + 1)

        return influence_total

    for term in categorized:
        visited = set()
        propagate(term, visited, 1)

def apply_bridge_strategy(categorized_terms, bridge_fn, strategy="recursive", depth_limit=3):
    if strategy == "recursive":
        propagate_bridge_weights_recursive(categorized_terms, bridge_fn, depth_limit)
    else:
        propagate_bridge_weights(categorized_terms, bridge_fn)
