from ssai import SymbolicSystem

brain = SymbolicSystem("memory.sym")
brain.load_memory()

while True:
    q = input("🔍 Ask: ")
    if q.lower() in ["exit", "quit"]: break
    print("🧠", brain.query(q))
