#!/usr/bin/env python3

import os
from collections import deque, defaultdict

MEMORY_FILE = "memory.sym"
WHITELIST_FILE = "symbol_whitelist.txt"
MAX_DEPTH = 3

def load_memory():
    try:
        with open(MEMORY_FILE, "r") as f:
            return [line.strip().split(" | ")[0] for line in f if line.strip()]
    except FileNotFoundError:
        return []

def load_whitelist():
    try:
        with open(WHITELIST_FILE, "r") as f:
            return set(line.strip().lower() for line in f if line.strip())
    except FileNotFoundError:
        return set()  # No whitelist = allow all

def build_is_a_map(lines):
    is_a_links = defaultdict(set)
    for line in lines:
        if " is_a " in line:
            subj, obj = line.split(" is_a ")
            subj, obj = subj.strip(), obj.strip()
            is_a_links[subj].add(obj)
    return is_a_links

def promote_transitive_chains(is_a_links, whitelist, max_depth=MAX_DEPTH):
    promoted = set()
    for start in is_a_links:
        visited = set()
        queue = deque([(start, 0)])
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for target in is_a_links.get(current, []):
                if target not in visited:
                    visited.add(target)
                    if not whitelist or (start in whitelist and target in whitelist):
                        promoted.add(f"{start} is_a {target}")
                    queue.append((target, depth + 1))
    return promoted

def save_promoted(promoted_lines):
    with open(MEMORY_FILE, "a") as f:
        for line in promoted_lines:
            f.write(f"{line} | strength: inferred\n")

def main():
    memory_lines = load_memory()
    whitelist = load_whitelist()
    is_a_links = build_is_a_map(memory_lines)
    new_lines = promote_transitive_chains(is_a_links, whitelist)
    save_promoted(new_lines)
    print(f"✅ Promoted {len(new_lines)} new inferred chains.")

if __name__ == "__main__":
    main()
