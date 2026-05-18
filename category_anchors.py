# Category Anchors — B6

CATEGORY_ANCHORS = {
    "human": ["man", "woman", "boy", "girl", "infant", "adult", "child"],
    "animal": ["dog", "cat", "bird", "fish"],
    "state": ["alive", "dead", "warm", "cold", "burning"],
    "action": ["eat", "move", "sleep", "grow"],
    "object": ["rock", "chair", "tree", "food"],
    "quality": ["big", "small", "strong", "weak"],
}

def anchor_category(term: str) -> str:
    for category, members in CATEGORY_ANCHORS.items():
        if term in members:
            return category
    return "unknown"
