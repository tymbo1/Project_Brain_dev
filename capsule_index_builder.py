import os, json

capsule_root = os.path.join(os.path.dirname(__file__), "capsules")
index = {}

def is_omega(path):
    name = os.path.basename(path).lower()
    return name.startswith("omega") or "omega" in name or "Ω" in name

def path_to_label(path):
    name = os.path.splitext(os.path.basename(path))[0]
    return name.replace("_", " ").capitalize()

def path_to_group(path):
    parts = os.path.relpath(path, capsule_root).split(os.sep)
    if len(parts) > 1:
        return " / ".join(parts[:-1])
    return "General"

for root, _, files in os.walk(capsule_root):
    for file in files:
        if file.endswith(".sym"):
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, capsule_root).replace("\\", "/")

            index[rel_path] = {
                "label": path_to_label(file),
                "group": path_to_group(full_path),
                "omega": is_omega(file)
            }

with open(os.path.join(capsule_root, "capsule_index.json"), "w") as f:
    json.dump(index, f, indent=2)
