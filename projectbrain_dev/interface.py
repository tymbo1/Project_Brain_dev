#!/data/data/com.termux/files/usr/bin/python
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path: sys.path.insert(0, script_dir)

from recursive_logic_engine import RecursiveLogicEngine

engine = RecursiveLogicEngine()

def main():
    print("🧠 PROJECTBRAIN ACTIVE — TYPE A STATEMENT TO INJECT:")
    while True:
        try:
            user_input = input("➤ ")
            if user_input.lower() in ["exit", "quit"]:
                print("🧠 Goodbye.")
                break
            if user_input.endswith("?"):
                results = engine.infer(user_input)
                print("🔍 INFERRED LINKS:")
                for r in results:
                    print(f"  • {r}")
            else:
                linked = engine.inject(user_input)
                print(f"✅ STORED with associations: {dict(linked)}")
        except KeyboardInterrupt:
            print("\n🧠 Session ended.")
            break

if __name__ == "__main__":
    main()
