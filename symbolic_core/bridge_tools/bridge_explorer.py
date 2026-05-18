import os

BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "../memory/braid_bridges.sym")

def explore(term):
    term = term.strip().lower()
    if not os.path.exists(BRIDGE_PATH):
        print("❌ Bridge file not found.")
        return []

    results = []
    with open(BRIDGE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if term in line.lower():
                results.append(line.strip())

    if results:
        print(f"🔎 Bridges for '{term}':")
        for r in results:
            print(f"  → {r}")
    else:
        print(f"⚠️ No bridges found for '{term}'.")

    return results
