#!/usr/bin/env python3
# VA.1 — Transitive Synthesis Engine
# Expands inference to multi-hop forward/backward reasoning
# imports existing memory.sym storage format

import re
from collections import deque

def normalize(t):
    t = t.strip().lower()
    t = re.sub(r"[^a-z0-9_ ]", "", t)
    return t.replace(" ", "_")

def load_triples(memory_path="memory.sym"):
    triples = []
    try:
        with open(memory_path) as f:
            for line in f:
                if "|" in line:
                    clean = line.split("|")[0].strip()
                    parts = clean.split(" ")
                    if len(parts) >= 3:
                        subj = normalize(parts[0])
                        rel  = normalize(parts[1])
                        obj  = normalize(" ".join(parts[2:]))
                        triples.append((subj, rel, obj))
    except FileNotFoundError:
        pass
    return triples

def infer(symbol, memory_path="memory.sym"):
    symbol = normalize(symbol)
    triples = load_triples(memory_path)
    if not triples:
        return {"status": "unknown", "query": symbol}

    queue = deque([(symbol, [])])
    visited = {symbol}
    results = []

    while queue:
        current, path = queue.popleft()
        for subj, rel, obj in triples:

            if current == subj and obj not in visited:
                new = path + [(subj, rel, obj)]
                visited.add(obj)
                queue.append((obj, new))
                results.append(new)

            if current == obj and subj not in visited:
                new = path + [(subj, rel, obj)]
                visited.add(subj)
                queue.append((subj, new))
                results.append(new)

    if not results:
        return {"status": "unknown", "query": symbol}

    best = max(results, key=len)
    formatted = [f"{s} {r} {o}" for s, r, o in best]
    return {"status": "inferred", "chain": formatted}
