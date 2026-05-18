def describe_entity(entity: str):
    from ..core_api import core
    entity = entity.strip().lower()
    results = []
    for triple in core.recall():
        if len(triple) >= 3 and str(triple[0]).lower() == entity:
            results.append(f"{triple[0]} {triple[1]} {triple[2]}")
    if not results:
        return f"I do not yet know who or what {entity} is."
    results.sort(key=len, reverse=True)
    return "\n".join(results)
