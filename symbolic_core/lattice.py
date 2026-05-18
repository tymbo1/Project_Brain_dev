class SymbolicLattice:
    def __init__(self):
        self.clusters = {}

    def add_to_cluster(self, term, enriched):
        """
        Add term to symbolic neighborhood based on role + contexts.
        """
        key = enriched.get("role", "unknown")

        if key not in self.clusters:
            self.clusters[key] = []

        self.clusters[key].append(enriched)

    def build_lattice(self, enriched_terms):
        """
        Construct coherence-guided symbolic lattice.
        """
        for t in enriched_terms:
            self.add_to_cluster(t.get("raw", "unknown"), t)

        return self.clusters

    # ============================================================
# LATTICE RELATIONAL ENGINE (B6.2)
# ============================================================

class SymbolicLattice:
    def __init__(self):
        self.nodes = []
        self.edges = []

    def build_lattice(self, categorized_terms):
        self.nodes = categorized_terms
        # simple placeholder edges — can later evolve to weighted
        self.edges = [
            (categorized_terms[i], categorized_terms[j])
            for i in range(len(categorized_terms))
            for j in range(i+1, len(categorized_terms))
        ]
        return {
            "nodes": self.nodes,
            "edges": self.edges
        }

    # --------------------------------------------------------
    # NEW: LATTICE-BASED RELATIONAL INFERENCE (B6.2)
    # --------------------------------------------------------
    def relate(self, categorized_terms):
        relations = []

        for entry in categorized_terms:
            t = entry["term"]
            c = entry["category"]

            # extremely simple proto-logic
            relation = {
                "symbol": t,
                "category": c,
                "neighbors": [
                    n["term"] for n in categorized_terms
                    if n["term"] != t
                ]
            }
            relations.append(relation)

        return relations
# ============================================================
# B6.3 — LATTICE-WEIGHTED ANSWER CONSTRUCTION
# ============================================================
def weighted_answer(self, categorized_terms, lattice):
    nodes = lattice.get("nodes", [])
    edges = lattice.get("edges", [])

    # If no structure yet, return neutral answer
    if not nodes:
        return {
            "summary": "insufficient structure",
            "weights": {},
            "top_terms": []
        }

    # Weight calculation: simple proto-importance score
    weights = {}
    for entry in categorized_terms:
        term = entry["term"]
        # degree = number of neighbors
        degree = sum(
            1 for (a, b) in edges
            if a["term"] == term or b["term"] == term
        )
        weights[term] = degree

    # Rank terms by weight
    sorted_terms = sorted(weights.items(), key=lambda x: x[1], reverse=True)
    top_k = [t for t, w in sorted_terms[:3]]  # top 3 nodes

    return {
        "summary": "lattice-weighted explanation",
        "weights": weights,
        "top_terms": top_k
    }
