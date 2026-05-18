#!/data/data/com.termux/files/usr/bin/python
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path: sys.path.insert(0, script_dir)

# projectbrain/inference_director.py

from recursive_logic_engine import walk_inference_tree

def run(symbol):
    print(f"\nSymbolic Inference for '{symbol}':")
    walk_inference_tree(symbol)
    
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 inference_director.py <symbol>")
    else:
        run(sys.argv[1])
