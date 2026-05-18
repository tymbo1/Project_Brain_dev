#!/usr/bin/env python3
"""
LangEng output test — feeds clean synthetic chains directly to nl_synthesis.
Shows what the language realisation layer produces when given quality data.
No CMS, no memory.sym — pure synthesis demonstration.
"""
from nl_synthesis import synthesize

TESTS = [
    ("dna", [
        "dna | contains | nucleotide | strength: 95",
        "dna | contains | nucleobase | strength: 90",
        "dna | is_a | polymer | strength: 88",
        "dna | derived_from | nucleic_acid | strength: 85",
        "dna | enables | protein_synthesis | strength: 82",
        "dna | contains | adenine | strength: 80",
        "dna | contains | guanine | strength: 80",
        "chromosome | contains | dna | strength: 78",
        "dna | requires | helicase | strength: 70",
    ]),
    ("fire", [
        "fire | causes | combustion | strength: 95",
        "fire | requires | oxygen | strength: 93",
        "fire | requires | fuel | strength: 91",
        "fire | produces | heat | strength: 90",
        "fire | produces | carbon_dioxide | strength: 85",
        "fire | can_cause | smoke | strength: 80",
        "fire | enables | cooking | strength: 72",
        "water | reduces | fire | strength: 88",
    ]),
    ("cancer", [
        "cancer | causes | cell_proliferation | strength: 92",
        "cancer | can_cause | metastasis | strength: 90",
        "cancer | affects | immune_system | strength: 85",
        "cancer | is_a | disease | strength: 98",
        "radiation | can_cause | cancer | strength: 80",
        "cancer | requires | treatment | strength: 75",
        "chemotherapy | reduces | cancer | strength: 78",
        "cancer | is_a | malignant_neoplasm | strength: 88",
    ]),
    ("beethoven", [
        "beethoven | composer | symphony_no_9 | strength: 99",
        "beethoven | composer | moonlight_sonata | strength: 98",
        "beethoven | is_a | composer | strength: 97",
        "beethoven | is_a | pianist | strength: 90",
        "beethoven | derived_from | classical_music | strength: 85",
        "beethoven | has_property | deaf | strength: 88",
        "beethoven | created_by | vienna | strength: 60",
        "symphony_no_9 | composer | beethoven | strength: 99",
    ]),
    ("gravity", [
        "gravity | causes | acceleration | strength: 95",
        "gravity | affects | mass | strength: 93",
        "gravity | enables | orbit | strength: 90",
        "gravity | is_a | fundamental_force | strength: 97",
        "gravity | requires | mass | strength: 88",
        "gravity | can_cause | tidal_force | strength: 75",
        "einstein | defines | gravity | strength: 85",
        "gravity | affects | spacetime | strength: 82",
    ]),
]

print("=" * 60)
print("LangEng Output — nl_synthesis demonstration")
print("=" * 60)

for query, chains in TESTS:
    print(f"\n── Query: '{query}' ──")
    print(f"   Input chains: {len(chains)}")
    output = synthesize(query, chains, max_sentences=5)
    print(f"   Output:\n   {output}")

print("\n" + "=" * 60)
print("Raw template coverage check:")
from nl_synthesis import TEMPLATES
print(f"  Templates defined: {len(TEMPLATES)}")
relations_in_test = set()
for _, chains in TESTS:
    for c in chains:
        parts = c.split(" | ")
        if len(parts) >= 3:
            relations_in_test.add(parts[1])
missing = relations_in_test - set(TEMPLATES.keys())
covered = relations_in_test - missing
print(f"  Relations in test: {len(relations_in_test)}")
print(f"  Covered by template: {len(covered)} {sorted(covered)}")
print(f"  Falling back to generic: {len(missing)} {sorted(missing) if missing else 'none'}")
