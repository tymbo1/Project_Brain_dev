#!/bin/bash
# langeng_autoloop.sh — Wait for current gap pass, run learn pipeline,
# then repeat: 10 batches of 10-turn gap passes each followed by learning.

LOG="$HOME/langeng_autoloop.log"
GAP_LOG="$HOME/langeng_gap.log"
PBDEV="$HOME/projectbrain_dev"

emit() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# ── Find current next turn from gap log ──────────────────────────────────────
next_turn() {
    python3 -c "
import sqlite3, json
from pathlib import Path
conn = sqlite3.connect(Path.home() / 'resonance_v11.db')
row = conn.execute(\"SELECT MAX(json_extract(metadata,'$.turn')) FROM capsules WHERE capsule_type='language_gap'\").fetchone()
print((row[0] or 0) + 1)
conn.close()
"
}

# ── Wait for a python process matching pattern to finish ─────────────────────
wait_for() {
    local pattern="$1"
    local label="$2"
    emit "Waiting for: $label"
    while pgrep -f "$pattern" > /dev/null 2>&1; do
        sleep 30
    done
    emit "Done: $label"
}

# ── Run gap pass ──────────────────────────────────────────────────────────────
run_gap_pass() {
    local from_turn="$1"
    local to_turn=$((from_turn + 9))
    emit "Starting gap pass: turns $from_turn–$to_turn"
    python3 -u "$PBDEV/langeng_gap_pass.py" \
        --turns=$to_turn \
        --from-turn=$from_turn \
        --gpu \
        >> "$GAP_LOG" 2>&1
    emit "Gap pass complete: turns $from_turn–$to_turn"
}

# ── Run learning pipeline ─────────────────────────────────────────────────────
run_learn() {
    emit "Running learning pipeline..."
    python3 -u "$PBDEV/langeng_learn.py" >> "$LOG" 2>&1
    emit "Learning pipeline complete."
}

# ─────────────────────────────────────────────────────────────────────────────

emit "=== LangEng AutoLoop started ==="
emit "Will wait for current gap pass, then run 10 learn+gap cycles"

# Step 1: Wait for current running gap pass (turns 38-1000)
wait_for "langeng_gap_pass" "initial 1000-turn pass"

# Step 2: Learn from initial pass
run_learn

# Step 3: 10 × (10-turn gap pass + learn)
for batch in $(seq 1 10); do
    FROM=$(next_turn)
    emit "=== Batch $batch/10 — from turn $FROM ==="
    run_gap_pass "$FROM"
    run_learn
    emit "=== Batch $batch/10 complete ==="
    # Wait until GPU temp drops below 70°C before next batch
    while true; do
        TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1)
        if [ -z "$TEMP" ] || [ "$TEMP" -lt 70 ]; then
            break
        fi
        emit "GPU at ${TEMP}°C — waiting to cool..."
        sleep 30
    done
    sleep 10
done

emit "=== AutoLoop complete. All 10 batches done. ==="
