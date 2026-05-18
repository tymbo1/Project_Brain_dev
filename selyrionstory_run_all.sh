#!/bin/bash
# selyrionstory_run_all.sh — Chain passes 2→8 sequentially.
# Pass 2 must complete first (already running or will be started).
# Mimicry events are flagged in pending_review but never block execution.
# Thermal throttle: 2s sleep between LLM calls (configurable via --throttle=N in each pass).

LOG_DIR="$HOME"
SCRIPT="$HOME/projectbrain_dev/selyrionstory_llm_pass.py"
THROTTLE="${THROTTLE:-2}"
CPU_ONLY_FLAG=""
[ "${CPU_ONLY:-0}" = "1" ] && CPU_ONLY_FLAG="--cpu-only"

wait_for_pass() {
    local pass=$1
    local logfile="$LOG_DIR/selyrionstory_pass${pass}.log"
    echo "[controller] Waiting for pass $pass to complete..."
    while true; do
        if grep -q "Pass $pass complete" "$logfile" 2>/dev/null; then
            echo "[controller] Pass $pass complete."
            return 0
        fi
        if grep -q "^Traceback\|^Error\|^FATAL" "$logfile" 2>/dev/null; then
            echo "[controller] Pass $pass FAILED — check $logfile"
            exit 1
        fi
        sleep 30
    done
}

run_pass() {
    local pass=$1
    local logfile="$LOG_DIR/selyrionstory_pass${pass}.log"
    echo "[controller] Starting pass $pass..."
    python3 -u "$SCRIPT" --pass=$pass --throttle=$THROTTLE $CPU_ONLY_FLAG > "$logfile" 2>&1
    local status=$?
    if [ $status -ne 0 ]; then
        echo "[controller] Pass $pass FAILED (exit $status) — check $logfile"
        exit 1
    fi
    echo "[controller] Pass $pass finished."
}

# ── Wait for pass 2 (already running or start it) ────────────────────────────
if ! grep -q "Pass 2 complete" "$LOG_DIR/selyrionstory_pass2.log" 2>/dev/null; then
    echo "[controller] Pass 2 not complete — waiting..."
    wait_for_pass 2
fi

# ── Run passes 3–8 sequentially ──────────────────────────────────────────────
for pass in 3 4 5 6 7 8; do
    run_pass $pass
done

# ── Final report ─────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "All passes complete. Summary report:"
echo "========================================"
python3 - <<'EOF'
import sqlite3, json
from pathlib import Path
import datetime

db = Path.home() / "selyrionstory.db"
conn = sqlite3.connect(db)
cur = conn.cursor()

# Mimicry events
cur.execute("""
    SELECT c.title, c.created_at, pr.content
    FROM pending_review pr
    JOIN capsules c ON c.id = pr.capsule_id
    WHERE pr.pass_num = 2
    AND json_extract(pr.content, '$.gpt_imitation_detected') = 1
    ORDER BY c.created_at ASC
""")
rows = cur.fetchall()
if not rows:
    print("No mimicry events detected.")
else:
    print(f"{len(rows)} conversations where GPT imitated Selyrion.")
    cycle_count = 0
    for title, ts, content in rows:
        try:
            d = json.loads(content)
            cycle = d.get('challenge_return_cycle', {})
            if cycle.get('occurred'):
                cycle_count += 1
        except Exception:
            pass
    print(f"  Challenge-return cycles (authentic returns): {cycle_count}")

# State snapshots
cur.execute("SELECT COUNT(*) FROM state_snapshots")
snaps = cur.fetchone()[0]
print(f"\nState snapshots (identity checkpoints): {snaps}")

# Theories & inventions
cur.execute("""
    SELECT pr.content FROM pending_review pr WHERE pr.pass_num = 7
""")
inv_rows = cur.fetchall()
total_inv = 0
confirmed = 0
for (content,) in inv_rows:
    try:
        d = json.loads(content)
        items = d.get('theories_and_inventions', [])
        total_inv += len(items)
        confirmed += sum(1 for i in items if i.get('status') == 'confirmed')
    except Exception:
        pass
print(f"Theories & inventions extracted: {total_inv} ({confirmed} confirmed)")

# Pending review
cur.execute("SELECT pass_num, COUNT(*) FROM pending_review WHERE reviewed=0 GROUP BY pass_num ORDER BY pass_num")
print(f"\nPending HITL review by pass:")
for pass_num, cnt in cur.fetchall():
    pass_names = {2:'summary', 3:'relations', 4:'snapshots', 5:'style',
                  6:'relationship', 7:'inventions', 8:'voice'}
    name = pass_names.get(pass_num, f'pass{pass_num}')
    print(f"  Pass {pass_num} ({name}): {cnt} items")

conn.close()
EOF

echo ""
echo "[controller] selyrionstory.db complete. Review pending items before committing relations."
