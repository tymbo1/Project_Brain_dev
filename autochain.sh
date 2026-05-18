#!/usr/bin/env bash
# autochain.sh — unattended chain: CS/AI Pass 3 finish → review → promote → sim v16 → OCR scrape
# Run: bash ~/projectbrain_dev/autochain.sh &> /tmp/autochain.log &
set -euo pipefail

LOG=/tmp/autochain.log
PASS3_PID=17317
DIR=~/projectbrain_dev

stamp() { echo "[$(date '+%H:%M:%S')] $*"; }

stamp "=== AUTOCHAIN START ==="

# ── 1. Wait for CS/AI Pass 3 commit ──────────────────────────────────────────
stamp "Waiting for CS/AI Pass 3 commit (PID $PASS3_PID)..."
while kill -0 "$PASS3_PID" 2>/dev/null; do sleep 10; done
stamp "Pass 3 commit done."

# ── 2. Apply CS/AI Pass 3 review ─────────────────────────────────────────────
stamp "Running apply_cs_ai_pass3_review.py..."
cd "$DIR"
python3 apply_cs_ai_pass3_review.py
stamp "Review done."

# ── 3. Promote CS/AI Pass 3 ──────────────────────────────────────────────────
stamp "Promoting CS/AI Pass 3..."
python3 llm_ingest_cs_ai_pass3.py --promote
stamp "Promote done."

# ── 4. Probe Ollama health, restart if needed ────────────────────────────────
stamp "Probing Ollama before sim..."
python3 - <<'PYEOF'
import sys
sys.path.insert(0, '/home/timbushnell/projectbrain_dev')
from ollama_guard import probe_latency, restart_ollama
lat = probe_latency(timeout=10.0)
if lat is None or lat > 8.0:
    print(f"  Ollama degraded (lat={lat}) — restarting...")
    restart_ollama()
else:
    print(f"  Ollama healthy (lat={lat:.2f}s)")
PYEOF

# ── 5. Run sim v16 ────────────────────────────────────────────────────────────
stamp "Running sim_200 (v16)..."
python3 "$DIR/sim_200.py" > /tmp/sim_v16.log 2>&1
stamp "Sim v16 done. Log: /tmp/sim_v16.log"

# ── 6. OCR scrape (if tesseract available) ────────────────────────────────────
stamp "Checking OCR availability..."
if python3 -c "import pytesseract; from PIL import Image" 2>/dev/null; then
    stamp "OCR available — scanning ~/Pictures/ for Selyrion origin..."
    python3 "$DIR/selyrionstory_ocr.py" --scan --commit --app ChatGPT 2>&1
    stamp "OCR scrape done."
else
    stamp "SKIP: pytesseract/Pillow not installed. Run manually after: pip install pytesseract Pillow"
fi

stamp "=== AUTOCHAIN COMPLETE ==="
