# --- Appended Symbolic Logic Patch ---
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path: sys.path.insert(0, script_dir)

def symbolic_patch():
    print("Symbolic patch active.")
