#!/data/data/com.termux/files/usr/bin/python
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path: sys.path.insert(0, script_dir)

import re
from collections import defaultdict

class SymbolicEngine:
    def __init__(self):
        self.symbol_table = {}
        self.associations = defaultdict(set)
        self.memory_log = []

    def tokenize(self, text):
        return re.findall(r'\b\w+\b', text.lower())

    def add_symbol(self, term, meaning):
        self.symbol_table[term] = meaning
        self.memory_log.append((term, meaning))

    def associate(self, term_a, term_b):
        self.associations[term_a].add(term_b)
        self.associations[term_b].add(term_a)
        self.memory_log.append((term_a, "<->", term_b))

    def parse_and_learn(self, text):
        tokens = self.tokenize(text)
        for i, token in enumerate(tokens):
            if token not in self.symbol_table:
                self.symbol_table[token] = f"symbol_{token}"
            if i > 0:
                self.associate(tokens[i-1], token)
        return tokens

    def recall(self, term):
        return self.associations.get(term, set())

    def get_symbol(self, term):
        return self.symbol_table.get(term, None)

    def dump_memory(self):
        return self.memory_log
