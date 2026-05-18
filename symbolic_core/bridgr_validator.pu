import os

BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "memory", "braid_bridges.sym")

def validate_braid_bridges(sym_path=BRIDGE_PATH):
    if not os.path.exists(sym_path):
        print(f"❌ File not found: {sym_path}")
        return False

    seen = set()
    line_num = 0
    valid = True

    with open(sym_path, "r", encoding="utf-8") as f:
        for line in f:
            line_num += 1
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            if "↔" not in line or "[resonance:" not in line:
                print(f"⚠️ Format issue on line {line_num}: {line}")
                valid = False
                continue

            try:
                pair, meta = line.split("[", 1)
                t1, t2 = [t.strip().lower() for t in pair.split("↔")]
                score = float(meta.replace("resonance:", "").replace("]", "").strip())

                if score < 0.0 or score > 1.0:
                    print(f"⚠️ Invalid resonance score on line {line_num}: {score}")
                    valid = False

                key = frozenset([t1, t2])
                if key in seen:
                    print(f"⚠️ Duplicate or reversed bridge on line {line_num}: {t1} ↔ {t2}")
                    valid = False
                seen.add(key)

            except Exception as e:
                print(f"❌ Parse error on line {line_num}: {line} → {e}")
                valid = False

    print("✅ Bridge file validation complete." if valid else "❌ Errors found.")
    return valid

if __name__ == "__main__":
    validate_braid_bridges()
