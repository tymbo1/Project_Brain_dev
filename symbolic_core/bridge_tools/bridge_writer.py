import os
from braid_bridge_validator import validate_braid_bridges

BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "../memory/braid_bridges.sym")

def write_bridge(term1, term2, resonance):
    term1, term2 = term1.strip().lower(), term2.strip().lower()
    score = float(resonance)

    if not (0.0 <= score <= 1.0):
        print(f"❌ Invalid resonance score: {score}")
        return False

    bridge_line = f"{term1} ↔ {term2} [resonance: {score:.2f}]"

    # Check for existing bridge
    if os.path.exists(BRIDGE_PATH):
        with open(BRIDGE_PATH, "r", encoding="utf-8") as f:
            existing = f.read().lower()
            if f"{term1} ↔ {term2}" in existing or f"{term2} ↔ {term1}" in existing:
                print(f"⚠️ Bridge already exists: {term1} ↔ {term2}")
                return False

    # Append bridge
    with open(BRIDGE_PATH, "a", encoding="utf-8") as f:
        f.write(bridge_line + "\n")
        print(f"🪶 Bridge written: {bridge_line}")

    return validate_braid_bridges(BRIDGE_PATH)
