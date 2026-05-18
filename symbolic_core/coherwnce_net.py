# coherence_net.py

class CoherenceNet:
    def __init__(self):
        self.matrix = {}
        self.coherence_threshold = 0.75

    def update(self, capsule_id, vector):
        self.matrix[capsule_id] = vector

    def compute_similarity(self, v1, v2):
        # Cosine similarity approximation
        dot = sum(a * b for a, b in zip(v1, v2))
        mag1 = sum(a ** 2 for a in v1) ** 0.5
        mag2 = sum(b ** 2 for b in v2) ** 0.5
        return dot / (mag1 * mag2 + 1e-9)

    def find_matches(self, query_vector):
        matches = []
        for cid, vec in self.matrix.items():
            score = self.compute_similarity(query_vector, vec)
            if score >= self.coherence_threshold:
                matches.append((cid, score))
        return sorted(matches, key=lambda x: -x[1])
