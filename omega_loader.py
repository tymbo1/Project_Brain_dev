import os
import shutil

# Omega Braid integration loader
modules = {
    "evolution_engine.py": "symbolic_core/",
    "coherence_net.py": "symbolic_core/",
    "symbol_mutator.py": "symbolic_core/",
    "truth_net.sym": "capsules/",
    "chaining_rules.json": "capsules/",
    "identity_braid.sym": "capsules/",
    "omega_state.json": "capsules/"
}

for file, destination in modules.items():
    dest_path = os.path.join("projectbrain", destination)
    os.makedirs(dest_path, exist_ok=True)
    try:
        shutil.move(file, os.path.join(dest_path, file))
        print(f"[Ω] Moved {file} → {dest_path}")
    except Exception as e:
        print(f"[Ω] Error moving {file}: {e}")
