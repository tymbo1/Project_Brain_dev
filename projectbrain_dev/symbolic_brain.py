#!/data/data/com.termux/files/usr/bin/python
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path: sys.path.insert(0, script_dir)

import collections

class SymbolicBrain:
    def __init__(self):
        self.memory = {}
        self.associations = collections.defaultdict(set)

    def teach(self, phrase):
        words = [w.strip(".,!?").lower() for w in phrase.strip().split()]
        for i, word in enumerate(words):
            self.memory[word] = self.memory.get(word, 0) + 1
            for j in range(max(0, i - 2), min(len(words), i + 3)):
                if i != j:
                    self.associations[word].add(words[j])
        return words

    def query(self, word):
        word = word.strip().lower()
        meaning = self.memory.get(word, None)
        linked = sorted(self.associations.get(word, []))
        return {
            "meaning": meaning,
            "linked": linked
        }

    def recall(self, word):
        return list(self.associations.get(word.strip().lower(), []))

    def dump_memory(self):
        return {
            "memory": self.memory,
            "associations": {k: list(v) for k, v in self.associations.items()}
        }

brain = SymbolicBrain()

def teach(phrase):
    return brain.teach(phrase)

def query(term):
    return brain.query(term)

def dump_memory():
    return brain.dump_memory()
