from symbolic_core.topo_map import get_resonant_glyph

def display_glyph_feedback(symbol):
    glyph = get_resonant_glyph(symbol)
    print(f"\n[Glyph: {glyph}] ← Resonance feedback for '{symbol}'\n")
    return glyph
