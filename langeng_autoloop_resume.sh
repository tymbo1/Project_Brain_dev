#!/bin/bash
# Resume autoloop from batch 17/100

LOG="$HOME/langeng_autoloop.log"
GAP_LOG="$HOME/langeng_gap.log"
PBDEV="$HOME/projectbrain_dev"

emit() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

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

run_gap_pass() {
    local from_turn="$1"
    local to_turn=$((from_turn + 99))
    emit "Starting gap pass: turns $from_turn–$to_turn"
    python3 -u "$PBDEV/langeng_gap_pass.py" \
        --turns=$to_turn \
        --from-turn=$from_turn \
        >> "$GAP_LOG" 2>&1
    emit "Gap pass complete: turns $from_turn–$to_turn"
}

run_learn() {
    emit "Running learning pipeline..."
    python3 -u "$PBDEV/langeng_learn.py" >> "$LOG" 2>&1
    emit "Learning pipeline complete."
}

emit "=== AutoLoop RESUMED at batch 17/100 ==="

for batch in $(seq 17 100); do
    FROM=$(next_turn)
    emit "=== Batch $batch/100 — from turn $FROM ==="
    run_gap_pass "$FROM"
    run_learn
    emit "=== Batch $batch/100 complete ==="
    sleep 5
done

emit "=== AutoLoop complete. All 100 batches done. ==="
