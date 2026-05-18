import os, json
from symbolic_core import store

LANG_CAPSULE_DIR = os.path.expanduser("~/projectbrain/capsules/language/english")

def load_capsules_from_directory():
    for fname in os.listdir(LANG_CAPSULE_DIR):
        if fname.endswith("_capsule.json"):
            fpath = os.path.join(LANG_CAPSULE_DIR, fname)
            try:
                with open(fpath, "r") as f:
                    capsule = json.load(f)
                    if isinstance(capsule, list):  # handle list-type capsule files
                        for entry in capsule:
                            subj = entry.get("subject")
                            rel = entry.get("relation")
                            obj = entry.get("object")
                            if subj and rel and obj:
                                store(subj, rel, obj)
                                print(f"[✓] Loaded: {subj} {rel} {obj}")
                    else:
                        print(f"[!] Unsupported capsule format in {fname}")
            except Exception as e:
                print(f"[!] Error loading {fname}: {e}")

if __name__ == "__main__":
    load_capsules_from_directory()
