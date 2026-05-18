# symbolic_core/symbolic_layers.py

import os

# Determine base directory relative to web context
web_root = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(web_root, ".."))
sym_path = os.path.join(project_root, "capsules", "symbolic_layers.sym")

def load_symbolic_layers(sym_file=sym_path):
    """Load symbolic layers from .sym file into a dictionary."""
    layers = {}
    if not os.path.exists(sym_file):
        print(f"[!!] symbolic_layers.sym not found at {sym_file}")
        return layers

    with open(sym_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                layer, terms = line.split(":", 1)
                layers[layer.strip()] = [t.strip() for t in terms.split(",") if t.strip()]
    return layers

def get_term_layers(term):
    """Returns all layers the term appears in (for debug/preview use)."""
    matches = []
    term = term.lower()
    for layer, words in LAYER_MAP.items():
        if any(term == w.lower() for w in words):
            matches.append(layer)
    return matches or ["unanchored"]

# Auto-load on import
LAYER_MAP = load_symbolic_layers()
