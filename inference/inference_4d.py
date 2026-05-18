#!/usr/bin/env python3
from collections import defaultdict, deque
from memory.memory_core import recall

class FourDInferenceEngine:
    def __init__(self):
        self.graph = defaultdict(lambda: defaultdict(set))

    def infer(self, query):
        # Live rebuild every time
        self.graph = defaultdict(lambda: defaultdict(set))
        for line in recall(term=query if isinstance(query, str) else None):
            parts = [p.strip() for p in line.split(" | ")]
            if len(parts) >= 3:
                s, r, o = parts[0], parts[1], " | ".join(parts[2:]).split(" | strength:")[0].strip()
                if o:
                    self.graph[s][r].add(o)
                    self.graph[o][f"rev_{r}"].add(s)

        if query not in self.graph and not any(query in str(v) for v in self.graph.values()):
            return {'status': '4d_inference', 'chains': [], 'query': query}

        visited = set()
        queue = deque([(query, [])])
        chains = []

        while queue and len(chains) < 15:
            current, path = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            for rel in self.graph[current]:
                for target in self.graph[current][rel]:
                    chain = f"{current} | {rel.replace('rev_', '←')} | {target}"
                    chains.append(chain)
                    if len(path) < 5:
                        queue.append((target, path + [chain]))

        return {'status': '4d_inference', 'chains': chains[:12], 'query': query}
