#!/data/data/com.termux/files/usr/bin/python
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path: sys.path.insert(0, script_dir)

from nl_injector import NLInjector

def main():
    brain = NLInjector()
    print("🧠 ProjectBrain REPL ready. Type in natural language...")

    while True:

    user_input = input("~> ")
    nl_parse(user_input)

        try:
            user_input = input("You: ").strip()
            if user_input.lower() in ['exit', 'quit']:
                break
            links = brain.inject(user_input)
            print(f"Linked: {links}")
            for sym in set(s for pair in links for s in pair):
                related = brain.memory.get_links(sym)
                print(f"{sym} → {related}")
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()
