import os
from collections import defaultdict, Counter

SYM_PATH = os.path.join(os.path.dirname(__file__), "../capsules/symbolic_layers.sym")

def validate_layers(sym_file=SYM_PATH):
    if not os.path.exists(sym_file):
        print(f"❌ symbolic_layers.sym not found at: {sym_file}")
        return

    print(f"🔍 Validating: {sym_file}")
    with open(sym_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    layers = {}
    seen_terms = []
    header_found = False
    duplicates = []
    format_issues = []

    for line in lines:
        clean = line.strip()
        if not clean or clean.startswith("#"):
            if "# Structure:" in clean:
                header_found = True
            continue

        if ":" not in clean:
            format_issues.append(clean)
            continue

        try:
            layer, items = clean.split(":", 1)
            layer = layer.strip()
            terms = [t.strip() for t in items.split(",") if t.strip()]
            layers[layer] = terms
            seen_terms.extend(terms)
        except Exception as e:
            format_issues.append(f"{clean} → {e}")

    print(f"✅ Header present: {'YES' if header_found else '❌ MISSING'}")
    print(f"✅ Layers parsed: {len(layers)}")

    term_counter = Counter(seen_terms)
    for term, count in term_counter.items():
        if count > 1:
            duplicates.append(term)

    if duplicates:
        print("⚠️ Duplicates found across layers:")
        for d in duplicates:
            print(f"   • {d}")
    else:
        print("✅ No duplicates across layers")

    if format_issues:
        print("⚠️ Format issues detected:")
        for issue in format_issues:
            print("   •", issue)
    else:
        print("✅ All lines formatted correctly")

    print("🔗 Validation complete.")

if __name__ == "__main__":
    validate_layers()

