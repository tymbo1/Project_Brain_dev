# symbolic_core/cross_layer_mapper.py

from symbolic_core.symbolic_layers import LAYER_MAP

def cross_layer_bridge(term1, term2):
    # Find layer of each term
    layer1 = find_layer(term1)
    layer2 = find_layer(term2)

    # Only bridge across *different* layers
    if not layer1 or not layer2 or layer1 == layer2:
        return None

    # Simple lexical overlap / root match
    if term1.lower() in term2.lower() or term2.lower() in term1.lower():
        return {
            "bridge": f"{term1} ↔ {term2}",
            "resonance": 0.75,
            "layers": [layer1, layer2]
        }

    # Manual symbolic bridge examples
    known = {
        ("gravity", "pull"): 0.82,
        ("light", "hope"): 0.76,
        ("wave", "emotion"): 0.71
    }
    for (a, b), score in known.items():
        if {term1.lower(), term2.lower()} == {a, b}:
            return {
                "bridge": f"{a} ↔ {b}",
                "resonance": score,
                "layers": [layer1, layer2]
            }

    return None

def find_layer(term):
    # If it's a dict with a 'term' key, use that
    if isinstance(term, dict):
        if "term" in term:
            term = term["term"]
        elif "bridge" in term:
            # Try extracting term from bridge like "light ↔ hope"
            term = term["bridge"].split("↔")[0].strip()
        else:
            return None

    if not isinstance(term, str):
        return None

    term = term.lower()
    for layer, words in LAYER_MAP.items():
        if term in [w.lower() for w in words]:
            return layer
    return None

import os

BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "memory", "braid_bridges.sym")

def load_braid_bridges(sym_path=BRIDGE_PATH):
    """Load manually authored braid bridges into a lookup table."""
    bridges = {}
    if not os.path.exists(sym_path):
        print(f"[⚠] braid_bridges.sym not found at: {sym_path}")
        return bridges

    with open(sym_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "↔" not in line:
                continue
            try:
                pair, meta = line.split("[", 1)
                term1, term2 = [t.strip() for t in pair.split("↔")]
                score = float(meta.replace("resonance:", "").replace("]", "").strip())
                bridges[frozenset([term1.lower(), term2.lower()])] = score
            except Exception as e:
                print(f"[✖] Bridge parse error: {line} → {e}")
    return bridges
