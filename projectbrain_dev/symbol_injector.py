#!/data/data/com.termux/files/usr/bin/python
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path: sys.path.insert(0, script_dir)

# projectbrain/symbol_injector.py

from recursive_logic_engine import symbolic_graph

def inject(symbol, links):
    if symbol not in symbolic_graph:
        symbolic_graph[symbol] = []
    symbolic_graph[symbol].extend(links)
    print(f"Injected: {symbol} → {links}")
