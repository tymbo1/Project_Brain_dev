#!/data/data/com.termux/files/usr/bin/python
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path: sys.path.insert(0, script_dir)

import sys
from symbolic_core import core as engine

brain = SymbolicBrain()

print("\n[Live Trainer Activated] Type natural language input to teach. Type 'exit' to quit.\n")

while True:
    try:
        user_input = input("🧠 Teach> ").strip()
        if user_input.lower() == "exit":
            print("Goodbye, teacher.")
            break
        if not user_input:
            continue

        # Basic parsing and injection
        words = user_input.lower().split()
        if len(words) >= 2:
            for i in range(len(words) - 1):
                brain.relate(words[i], words[i+1])
            print(f"✅ Linked: {' → '.join(words)}")

        else:
            brain.add_symbol(words[0])
            print(f"✅ Symbol learned: {words[0]}")

    except KeyboardInterrupt:
        print("\nSession interrupted. Exiting.")
        break
    except Exception as e:
        print(f"[Error] {e}")
