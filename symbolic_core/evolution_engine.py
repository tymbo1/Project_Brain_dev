from datetime import datetime
import json

class EvolutionEngine:
    def __init__(self):
        self.history = []
        self.loop_rejections = []  # ⛔ For storing looped pairs
        self.stats = {
            "mutations": 0,
            "average_coherence": 0.0,
        }

    def log(self, original, mutated, coherence_score=None, method="unspecified"):
        if self.has_loop(original, mutated):
            print(f"🔁 Mutation loop detected: {original} → {mutated} = rejected")
            self.loop_rejections.append({
                "timestamp": datetime.utcnow().isoformat(),
                "original": original,
                "mutated": mutated,
                "method": method
            })
            return False

        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "original": original,
            "mutated": mutated,
            "method": method,
            "coherence": coherence_score,
            "backlink": self.find_origin(original)
        }

        self.history.append(entry)
        self.stats["mutations"] += 1

        if coherence_score is not None:
            n = self.stats["mutations"]
            prev_avg = self.stats["average_coherence"]
            self.stats["average_coherence"] = ((prev_avg * (n - 1)) + coherence_score) / n

        print(f"🧬 Logged: {method} | {original} ➜ {mutated} | Score: {coherence_score}")
        return True

    def recent(self, n=5):
        return self.history[-n:]

    def summary(self):
        return {
            "total_mutations": self.stats["mutations"],
            "average_coherence": round(self.stats["average_coherence"], 4),
            "last_mutation": self.history[-1] if self.history else None
        }

    def reset(self):
        self.history.clear()
        self.loop_rejections.clear()
        self.stats = {
            "mutations": 0,
            "average_coherence": 0.0,
        }

    def filter_by_origin(self, origin_term):
        return [entry for entry in self.history if entry["original"] == origin_term]

    def mutations_of(self, symbol):
        return [entry for entry in self.history if entry["mutated"] == symbol]

    def export(self, filepath="mutation_log.json"):
        with open(filepath, "w") as f:
            json.dump(self.history, f, indent=2)

    def origin_summary(self):
        pass  # Placeholder for additional symbolic logic

    def find_origin(self, mutated):
        for entry in reversed(self.history):
            if entry["mutated"] == mutated:
                return entry["original"]
        return None

    def has_loop(self, original, mutated):
        return any(e["original"] == mutated and e["mutated"] == original for e in self.history)
