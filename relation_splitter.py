import os

with open("memory.sym", "r") as f:
    lines = [line.strip() for line in f if line.strip()]

relation_map = {}

for line in lines:
    if "|" not in line:
        continue  # skip malformed
    parts = line.split("|")
    relation_part = parts[0].strip()
    strength = parts[1].strip()
    tokens = relation_part.split()
    if len(tokens) < 3:
        continue  # not enough tokens
    src, rel, dst = tokens[0], tokens[1], " ".join(tokens[2:])
    relation_map.setdefault(rel, []).append(f"{src} {rel} {dst} | {strength}")

# Create capsule files
os.makedirs("capsules", exist_ok=True)
for rel, entries in relation_map.items():
    with open(f"capsules/{rel}.capsule", "w") as f:
        f.write("\n".join(entries) + "\n")

print(f"✅ Split {len(lines)} entries into {len(relation_map)} relation capsules.")
