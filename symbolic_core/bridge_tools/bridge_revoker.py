import os

BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "../memory/braid_bridges.sym")

def revoke_bridge(term1, term2):
    term1, term2 = term1.strip().lower(), term2.strip().lower()
    line_match = f"{term1} ↔ {term2}"
    inverse_match = f"{term2} ↔ {term1}"

    if not os.path.exists(BRIDGE_PATH):
        print("❌ No bridge file found.")
        return False

    with open(BRIDGE_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = [l for l in lines if line_match not in l.lower() and inverse_match not in l.lower()]
    if len(new_lines) == len(lines):
        print(f"⚠️ No bridge found for: {term1} ↔ {term2}")
        return False

    with open(BRIDGE_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"🧹 Bridge removed: {term1} ↔ {term2}")
    return True
