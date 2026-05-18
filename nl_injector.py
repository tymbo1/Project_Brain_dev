#!/data/data/com.termux/files/usr/bin/python
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path: sys.path.insert(0, script_dir)

from symbol_parser import extract_symbols_from_text, pair_symbols
from memory.memory_core import SymbolMemory

class NLInjector:
    def __init__(self):
        self.memory = SymbolMemory()

    def inject(self, text):
        symbols = extract_symbols_from_text(text)
        pairs = pair_symbols(symbols)
        for a, b in pairs:
            self.memory.remember_pair(a, b)
        return pairs
