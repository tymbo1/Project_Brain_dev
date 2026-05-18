#!/usr/bin/env python3
# VA.2 - Rule Expander from memory.sym
# Learns latent symbolic rules from stored facts

import re
from collections import defaultdict

def normalize(t):
    t = t.strip().lower()
    t = re.sub(r"[^a-z0-9_ ]", "", t)
    return t.replace(" ", "_")

def load_triples(path="memory.sym"):
    triples = []
    try:
        with open(path) as f:
            for line in f:
                if "|" in line: line = line.split("|")[0]
                tokens = line.strip().split()
                if len(tokens) >= 3:
                    subj = normalize(tokens[0])
                    rel  = normalize(tokens[1])
                    obj  = normalize(" ".join(tokens[2:]))
                    triples.append((subj, rel, obj))
    except FileNotFoundError:
        pass
    return triples

def generate_rules(triples):
    rules = set()
    transitive = defaultdict(list)

    for s, r, o in triples:
        transitive[(r, s)].append(o)
    
    # Transitive Rule Detection
    for (rel, mid), targets in transitive.items():
        for t1 in targets:
            for t2 in transitive.get((rel, t1), []):
                rules.add((rel, mid, rel, t1, rel, t2))

    # Symmetric Rule Detection
    for s, r, o in triples:
        if (o, r, s) in triples:
            rules.add((r, s, r, o, "symmetric"))

    # Reflexive Rule Detection
    for s, r, o in triples:
        if s == o:
            rules.add((r, s, "reflexive"))

    return rules

def save_rules(rules, out_path="rules.sym"):
    with open(out_path, "w") as f:
        for rule in rules:
            if len(rule) == 6:
                f.write(f"{rule[1]} {rule[0]} + {rule[3]} {rule[2]} -> {rule[5]} {rule[4]}\n")
            elif len(rule) == 3 and rule[2] == "reflexive":
                f.write(f"{rule[1]} {rule[0]} {rule[1]} | reflexive\n")
            elif len(rule) == 5 and rule[4] == "symmetric":
                f.write(f"{rule[1]} {rule[0]} {rule[2]} | symmetric\n")

def expand_rules(memory_path="memory.sym", out_path="rules.sym"):
    triples = load_triples(memory_path)
    rules = generate_rules(triples)
    save_rules(rules, out_path)
    return len(rules)

if __name__ == "__main__":
    count = expand_rules()
    print(f"[VA.2] Rules generated: {count}")
