
# Live reload for traversal — rebuild graph on every infer
from collections import defaultdict
original_infer = FourDInferenceEngine.infer
def live_infer(self, query):
    self.graph = defaultdict(lambda: defaultdict(set))
    from memory.memory_core import recall
    for line in recall():
        parts = [p.strip() for p in line.split(' | ')]
        if len(parts) >= 3:
            subj = parts[0]
            rel = parts[1]
            obj = ' | '.join(parts[2:]).split(' | strength:')[0].strip()
            self.graph[subj][rel].add(obj)
            self.graph[obj][f'rev_{rel}'].add(subj)
    return original_infer(self, query)
FourDInferenceEngine.infer = live_infer


# OVERRIDE: Full working infer4d with BFS traversal
from collections import deque, defaultdict
original_infer = FourDInferenceEngine.infer
def working_infer(self, query):
    # Reload graph from recall()
    self.graph = defaultdict(lambda: defaultdict(set))
    from memory.memory_core import recall
    for line in recall():
        parts = [p.strip() for p in line.split(' | ')]
        if len(parts) >= 3:
            subj = parts[0]
            rel = parts[1]
            obj = ' | '.join(parts[2:]).split(' | strength:')[0].strip()
            if obj:
                self.graph[subj][rel].add(obj)
                self.graph[obj][f'rev_{rel}'].add(subj)
    
    # Simple BFS to find chains
    if query not in self.graph:
        return {'status': '4d_inference', 'chains': [], 'query': query}
    
    visited = set()
    queue = deque([(query, [])])  # (current, path)
    chains = []
    
    while queue and len(chains) < 5:  # Limit to 5 chains
        current, path = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        
        for rel in self.graph[current]:
            for target in self.graph[current][rel]:
                new_path = path + [f'{current} | {rel} | {target}']
                chains.append(new_path[-1])  # Add the last hop as chain
                if len(new_path) < 4:  # Limit depth to 4 hops
                    queue.append((target, new_path))
    
    return {'status': '4d_inference', 'chains': chains[:5], 'query': query}
FourDInferenceEngine.infer = working_infer


# FULL WORKING OVERRIDE: Reload + BFS traversal for infer4d
from collections import deque, defaultdict
original_infer = FourDInferenceEngine.infer
def working_infer(self, query):
    # Reload graph from recall()
    self.graph = defaultdict(lambda: defaultdict(set))
    from memory.memory_core import recall
    for line in recall():
        parts = [p.strip() for p in line.split(' | ')]
        if len(parts) >= 3:
            subj = parts[0]
            rel = parts[1]
            obj = ' | '.join(parts[2:]).split(' | strength:')[0].strip()
            if obj:
                self.graph[subj][rel].add(obj)
                self.graph[obj][f'rev_{rel}'].add(subj)
    
    # BFS to find real chains
    if query not in self.graph:
        return {'status': '4d_inference', 'chains': [], 'query': query}
    
    visited = set()
    queue = deque([(query, [])])
    chains = []
    
    while queue and len(chains) < 5:
        current, path = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        
        for rel in self.graph[current]:
            for target in self.graph[current][rel]:
                new_path = path + [f'{current} | {rel} | {target}']
                chains.append(new_path[-1])
                if len(new_path) < 4:
                    queue.append((target, new_path))
    
    return {'status': '4d_inference', 'chains': chains[:5], 'query': query}
FourDInferenceEngine.infer = working_infer

