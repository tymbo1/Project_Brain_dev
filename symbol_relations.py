RELATION_TYPES = {
    "is_a": 1,
    "relates_to": 1,
    "part_of": 1,
    "has": 1,
    "contains": 1,
    "causes": 2,
    "leads_to": 2,
    "enables": 2,
    "requires": 2,
    "prevents": 2,
    "opposes": 2
}

def is_causal(rel):
    return RELATION_TYPES.get(rel, 1) == 2
