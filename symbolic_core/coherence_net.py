import os
import json

class CoherenceNet:
    def evaluate(self, symbol: str) -> float:
        if not symbol:
            return 0.0
        unique_chars = set(symbol)
        diversity_score = len(unique_chars) / len(symbol)
        length_factor = min(len(symbol) / 10, 1.0)
        return round(diversity_score * length_factor, 4)

def evaluate_and_log(symbol: str):
    net = CoherenceNet()
    score = net.evaluate(symbol)
    contradiction = score < 0.3  # Adjust threshold if needed

    entry = {
        "symbol": symbol,
        "coherence_score": score,
        "contradiction": contradiction
    }

    # 🧭 Path correction anchor
    base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    log_path = os.path.join(base_path, "capsules", "coherence_log.jsonl")

    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")

    return entry
