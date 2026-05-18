# ===============================
# ProjectBrain Recursive Logic Engine
# Deterministic Directed Inference + Strength-Safe Loading
# ===============================

def process_input(text):
    import re
    from collections import deque

    # -------------------------------
    # Normalization
    # -------------------------------
    def normalize(t):
        t = t.strip().lower()
        t = re.sub(r"[^a-z0-9_ ]", "", t)
        t = t.replace(" ", "_")
        if t.startswith(("a_", "an_", "the_")):
            t = t.split("_", 1)[1]
        return t

    query = normalize(text)

    # -------------------------------
    # Load triples from memory.sym (strength labels removed)
    # -------------------------------
    triples = []
    try:
        with open("memory.sym") as f:
            for line in f:
                clean = line.split("|")[0].strip()  # strip strength annotation
                parts = clean.split(" ")
                if len(parts) >= 3:
                    subj = normalize(parts[0])
                    rel  = parts[1].lower()
                    obj  = normalize(" ".join(parts[2:]))
                    triples.append((subj, rel, obj))
    except FileNotFoundError:
        return {"status": "unknown", "query": text}

    # -------------------------------
    # BFS multi-hop chain search
    # -------------------------------
    queue = deque([(query, [])])
    visited = {query}
    results = []

    while queue:
        current, path = queue.popleft()
        for subj, rel, obj in triples:

            # forward hop (A -> B)
            if current == subj and obj not in visited:
                new_path = path + [(subj, rel, obj)]
                visited.add(obj)
                queue.append((obj, new_path))
                results.append(new_path)

            # backward hop (B -> A)
            if current == obj and subj not in visited:
                new_path = path + [(subj, rel, obj)]
                visited.add(subj)
                queue.append((subj, new_path))
                results.append(new_path)

    # -------------------------------
    # Format inference results
    # -------------------------------
    if results:
        longest = max(results, key=len)
        formatted = [f"{s} {r} {o}" for s, r, o in longest]
        return {"status": "inferred", "chain": formatted}

    return {"status": "unknown", "query": text}
