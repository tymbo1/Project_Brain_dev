#!/data/data/com.termux/files/usr/bin/python
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path: sys.path.insert(0, script_dir)

def get_directive():
    print("Enter directive (e.g. relate=vision):")
    inp = input(">> ").strip()
    if '=' in inp:
        key, val = inp.split('=', 1)
        return {key.strip(): val.strip()}
    return {}

# === PATCHED: Auto-injected directive helper ===
def patched_directive():
    print("Injected directive OK")
# === PATCHED: Auto-injected directive helper ===
def patched_directive():
    print("Injected directive OK")
