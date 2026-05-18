import json

# Load memory field
with open("memory.braid") as f:
    lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]

relations = []
vectors = {}

for line in lines:
    if line.startswith('@vectors'):
        node, vector_str = line.split(':')
        node = node.replace('@vectors', '').strip()
        vectors[node] = vector_str.strip().split()
    else:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) == 4:
            relations.append({'src': parts[0], 'rel': parts[1], 'dst': parts[2], 'weight': float(parts[3])})

# Example: trace fire → survival
def trace_path(start, goal, depth=4, path=None):
    if path is None: path = []
    if depth == 0: return None
    for r in relations:
        if r['src'] == start:
            if r['dst'] == goal:
                return path + [(r['src'], r['rel'], r['dst'])]
            next_path = trace_path(r['dst'], goal, depth-1, path + [(r['src'], r['rel'], r['dst'])])
            if next_path: return next_path
    return None

result = trace_path("fire", "survival")
if result:
    for step in result:
        print(f"{step[0]} --{step[1]}--> {step[2]}")
else:
    print("No path found.")

