#!/usr/bin/env python3
import sys
import os

# Add script dir to path
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# Try normal import
try:
    from symbolic_cli import Memory, load_memory, save_memory
    IMPORT_OK = True
except Exception as e:
    print(f"Import failed: {e}")
    print("Trying local fallback...")
    IMPORT_OK = False

# Fallback: exec symbolic_cli.py into current globals
if not IMPORT_OK:
    cli_path = os.path.join(script_dir, 'symbolic_cli.py')
    if os.path.exists(cli_path):
        with open(cli_path) as f:
            code = f.read()
        # Define stubs if missing
        exec(code, globals())
        print("Local fallback: loaded via exec()")
    else:
        print("symbolic_cli.py not found!")
        sys.exit(1)

# Ensure load_memory exists (fallback stub)
if 'load_memory' not in globals():
    def load_memory(path):
        class DummyMemory:
            facts = []
            rules = []
        return DummyMemory() if os.path.exists(path) else None
    print("Using stub load_memory")

def visualize(memory_path='memory.sym'):
    full_path = os.path.join(script_dir, memory_path)
    memory = load_memory(full_path)
    if not memory:
        print("No memory loaded.")
        return

    print("\n=== SYMBOLIC BRAIN VISUALIZATION ===")
    print(f"Total facts: {len(memory.facts)}")
    print(f"Total rules: {len(memory.rules)}")
    print("\n--- FACTS ---")
    for f in memory.facts:
        print(f"✓ {f}")

    print("\n--- RULES ---")
    for r in memory.rules:
        print(f"→ {r}")

    # ASCII Graph
    print("\n--- ASSOCIATIVE GRAPH (subset) ---")
    nodes = set()
    edges = []
    for rule in memory.rules[:10]:
        if '=>' in rule:
            premise, conclusion = rule.split('=>')
            p_parts = [p.strip() for p in premise.split('and')]
            for p in p_parts:
                if p and conclusion.strip():
                    nodes.add(p)
                    nodes.add(conclusion.strip())
                    edges.append((p, conclusion.strip()))

    for src, dst in edges:
        print(f"{src} ──→ {dst}")

    print(f"\nGraph nodes: {len(nodes)} | edges: {len(edges)}")

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else 'memory.sym'
    visualize(path)
