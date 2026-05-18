# ssai.py – Symbolic System Core Engine
class SymbolicSystem:
    def __init__(self, memory_file):
        self.memory_file = memory_file
        self.memory = []
        self.load_memory()

    def load_memory(self):
        with open(self.memory_file, "r") as f:
            self.memory = [line.strip() for line in f if line.strip()]

    def query(self, question):
        result, chain = self.infer(question, return_chain=True)
        with open("recent_inference.log", "a") as log:
            log.write(f"{question} => {chain}\n")
        return result

    def infer(self, question, return_chain=False):
        q_parts = question.lower().split()
        chain = []
        hits = []
        for line in self.memory:
            if any(p in line.lower() for p in q_parts):
                hits.append(line)
                chain.append(line)
        return (hits if return_chain else hits[0] if hits else "No match."), chain

    def infer_reverse(self, concept):
        reverse_hits = []
        for line in self.memory:
            if " is_a " in line:
                x, y = line.split(" is_a ")
                if y.strip() == concept:
                    reverse_hits.append(x.strip())
        return reverse_hits
