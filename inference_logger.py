import json
from datetime import datetime

def log_inference(seed, result, chain):
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "seed": seed,
        "result": result,
        "chain": chain
    }
    with open("logs/inference_log.jsonl", "a") as f:
        f.write(json.dumps(log_entry) + "\n")
