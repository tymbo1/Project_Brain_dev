# symbolic_core/bridge_logger.py

import json
from datetime import datetime
import os

LOG_PATH = "logs/bridge_log.jsonl"
SYM_PATH = "memory/bridges.sym"

def log_bridge(bridge_text, score, layers):
    timestamp = datetime.utcnow().isoformat()

    # Ensure directories exist
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(SYM_PATH), exist_ok=True)

    # Append to .jsonl log
    with open(LOG_PATH, "a") as f:
        json.dump({
            "timestamp": timestamp,
            "bridge": bridge_text,
            "resonance_score": score,
            "layers": layers
        }, f)
        f.write("\n")

    # Append to .sym (avoid duplicates)
    entry = f"{bridge_text} [resonance: {score}] [timestamp: {timestamp}]\n"
    if not os.path.exists(SYM_PATH) or entry not in open(SYM_PATH).read():
        with open(SYM_PATH, "a") as f:
            f.write(entry)
