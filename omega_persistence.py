import json, os
from datetime import datetime

OMEGA_PATH = "capsules/omega_sessions/"

def persist_omega_state(seed, result, symbols):
    if not os.path.exists(OMEGA_PATH):
        os.makedirs(OMEGA_PATH)
    fname = OMEGA_PATH + datetime.utcnow().strftime("%Y%m%d_%H%M%S") + ".json"
    with open(fname, "w") as f:
        json.dump({
            "timestamp": datetime.utcnow().isoformat(),
            "seed": seed,
            "symbols": symbols,
            "inference": result
        }, f, indent=2)
