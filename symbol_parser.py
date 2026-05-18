#!/data/data/com.termux/files/usr/bin/python
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path: sys.path.insert(0, script_dir)

import re

def extract_symbols_from_text(text):
    # Simple token extraction based on word boundaries
    tokens = re.findall(r'\b\w+\b', text.lower())
    return list(set(tokens))

def pair_symbols(symbols):
    return [(a, b) for i, a in enumerate(symbols) for b in symbols[i+1:]]
