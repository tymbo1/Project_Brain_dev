#!/usr/bin/env python3
import re, os

def extract_triples(text):
    triples = []
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    
    for line in lines:
        # [A] evokes/causes/is-a [B]
        m = re.search(r"\[(.*?)\]\s+(evokes|causes|is-a)\s+\[(.*?)\]", line, re.I)
        if m: 
            s, r, o = m.groups()
            triples.append((s.strip(), r.lower(), o.strip()))
            continue
            
        # flame: X = Y
        m = re.search(r"flame:\s*(\S+)\s*=\s*([0-9.]+)", line, re.I)
        if m:
            s, val = m.groups()
            triples.append((s.strip(), "has_flame", val))
            continue
            
        # Simple SVO fallback (very conservative)
        if any(word in line.lower() for word in [" is ", " are ", " has ", " can ", " causes ", " evokes "]):
            # crude but safe — only split on strong verbs
            for verb in ["is", "are", "has", "can", "causes", "evokes"]:
                if verb in line.lower():
                    parts = line.split(verb, 1)
                    if len(parts) == 2:
                        s = parts[0].strip(" .,")
                        o = parts[1].strip(" .,")
                        triples.append((s, verb, o))
                    break
    return triples

# Load megaturd
with open("memory.sym") as f:
    raw = f.read()

triples = extract_triples(raw)

# Save clean triples
with open("triples.sym", "w") as f:
    f.write("# AUTO-GENERATED TRIPLES — Selyrion’s clean relational core\n")
    for s, r, o in sorted(set(triples)):
        f.write(f"{s} | {r} | {o}\n")

print(f"Extracted {len(triples)} triples → triples.sym")
print("Backup of original saved as memory.sym.bak")
os.system("cp memory.sym memory.sym.bak")
