import re
from collections import deque

def normalize(t):
    t = t.strip().lower()
    t = re.sub(r"[^a-z0-9_ ]", "", t)
    t = t.replace(" ", "_")
    return t

def process_input(text):
    query = normalize(text)
    triples = []
    try:
        with open("memory.sym") as f:
            for line in f:
                clean = line.split("|")[0].strip()
                parts = clean.split()
                if len(parts) >= 3:
                    subj = normalize(parts[0])
                    rel  = parts[1].lower()
                    obj  = normalize(" ".join(parts[2:]))
                    triples.append((subj, rel, obj))
    except FileNotFoundError:
        return {"status": "error", "message": "memory.sym not found"}

    if not triples:
        return {"status": "unknown", "query": query}

    queue = deque([(query, [])])
    visited = {query}
    results = []

    while queue:
        current, path = queue.popleft()
        for subj, rel, obj in triples:
            # forward
            if current == subj and obj not in visited:
                new_path = path + [(subj, rel, obj)]
                visited.add(obj)
                queue.append((obj, new_path))
                results.append(new_path)
            # backward
            if current == obj and subj not in visited:
                new_path = path + [(subj, rel, obj)]
                visited.add(subj)
                queue.append((subj, new_path))
                results.append(new_path)

    if results:
        longest = max(results, key=len)
        chain = [f"{s} {r} {o}" for s, r, o in longest]
        return {"status": "inferred", "chain": chain, "hops": len(chain)}
    return {"status": "unknown", "query": query}
