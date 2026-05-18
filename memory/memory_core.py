#!/usr/bin/env python3
import os
from collections import defaultdict
from memory.cms_bridge import query_cms

strength = defaultdict(int)

_MEMORY_PATH = os.path.join(os.path.dirname(__file__), "..", "memory.sym")

def recall(term=None):
    """
    Return triples for inference.
    Always includes local memory.sym (taught knowledge).
    If term is given, filters local to triples mentioning term,
    then merges with CMS results. Local takes priority.
    """
    local = []
    try:
        with open(_MEMORY_PATH) as f:
            local = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except FileNotFoundError:
        pass

    if term:
        # Filter local — only triples that mention the query term
        term_norm = term.lower().replace(" ", "_")
        local = [l for l in local if term_norm in l.lower()]

        cms = query_cms(term)
        # Merge: local first, then CMS (deduplicate by subject|predicate|object key)
        seen = set()
        merged = []
        for line in local + cms:
            key = " | ".join(line.split(" | ")[:3])
            if key not in seen:
                seen.add(key)
                merged.append(line)
        return merged

    return local

def store_triple(subj, rel, obj):
    """Write only to memory.sym — CMS is never written to."""
    key = f"{subj} | {rel} | {obj}"
    strength[key] += 1
    with open("memory.sym", "a") as f:
        f.write(f"{key} | strength: {strength[key]}\n")

store = store_triple
