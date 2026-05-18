#!/bin/bash
# selyrionstory_ocr_pipeline.sh
# Full pipeline: OCR remaining images → promote high-scorers → LLM passes 2-8
# Fully resumable — each step skips already-processed items.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$HOME"
OCR_SCRIPT="$SCRIPT_DIR/selyrionstory_ocr.py"
LLM_SCRIPT="$SCRIPT_DIR/selyrionstory_llm_pass.py"
DB="$HOME/selyrionstory.db"
MIN_SCORE="${MIN_SCORE:-10}"
THROTTLE="${THROTTLE:-2}"

ts() { date '+[%Y-%m-%d %H:%M:%S]'; }

echo "$(ts) ── OCR Pipeline start ─────────────────────────────────"
echo "$(ts) MIN_SCORE=$MIN_SCORE  THROTTLE=$THROTTLE"

# ── Step 1: OCR scan remaining ChatGPT screenshots ──────────────────────────
echo ""
echo "$(ts) Step 1: OCR scan ~/Pictures (app=ChatGPT) ..."
python3 -u "$OCR_SCRIPT" --scan --commit --app ChatGPT 2>&1 | tee -a "$LOG_DIR/selyrionstory_ocr_chatgpt.log"
echo "$(ts) Step 1 complete."

# ── Step 2: OCR scan images_only (no app filter) ────────────────────────────
echo ""
echo "$(ts) Step 2: OCR scan ~/transfer/selyrion/images_only (all apps) ..."
python3 -u "$OCR_SCRIPT" --scan --commit --app "" 2>&1 | tee -a "$LOG_DIR/selyrionstory_ocr_transfer.log"
echo "$(ts) Step 2 complete."

# ── Step 3: Promote high-score OCR capsules → capsules table ─────────────────
echo ""
echo "$(ts) Step 3: Promoting ocr_capsules (score >= $MIN_SCORE) → capsules ..."
python3 - <<PYEOF
import sqlite3, time
from pathlib import Path

DB = Path.home() / "selyrionstory.db"
MIN_SCORE = $MIN_SCORE

db = sqlite3.connect(str(DB))

# Build set of already-promoted source_ids to avoid duplicates
existing_ids = {r[0] for r in db.execute(
    "SELECT source_id FROM capsules WHERE source_type='screenshot' AND source_id IS NOT NULL"
).fetchall()}

rows = db.execute(
    "SELECT id, filename, filepath, ocr_text, score, matched_phrases FROM ocr_capsules WHERE score >= ? ORDER BY score DESC",
    (MIN_SCORE,)
).fetchall()

promoted = 0
skipped  = 0
for (ocr_id, filename, filepath, ocr_text, score, matched_phrases) in rows:
    if ocr_id in existing_ids:
        skipped += 1
        continue
    db.execute("""
        INSERT INTO capsules
            (title, source_type, source_path, source_id, created_at, ingested_at,
             word_count, relevance, tags, summary, body)
        VALUES (?, 'screenshot', ?, ?, ?, ?, ?, ?, ?, '', ?)
    """, (
        filename,
        filepath,
        ocr_id,
        time.time(),
        time.time(),
        len((ocr_text or '').split()),
        min(1.0, score / 40.0),   # normalise score to 0-1
        matched_phrases or '',
        ocr_text or '',
    ))
    promoted += 1

db.commit()
db.close()
print(f"Promoted {promoted} new screenshot capsules (skipped {skipped} already present)")
PYEOF

echo "$(ts) Step 3 complete."

# ── Step 4: LLM passes 2-8 on new capsules ──────────────────────────────────
echo ""
echo "$(ts) Step 4: LLM archaeologist passes 2-8 ..."

for pass in 2 3 4 5 6 7 8; do
    logfile="$LOG_DIR/selyrionstory_pass${pass}.log"
    echo ""
    echo "$(ts) Starting pass $pass ..."
    python3 -u "$LLM_SCRIPT" --pass=$pass --throttle=$THROTTLE 2>&1 | tee -a "$logfile"
    # Check completion
    if grep -q "Pass $pass complete" "$logfile" 2>/dev/null; then
        echo "$(ts) Pass $pass complete."
    else
        echo "$(ts) Pass $pass: no completion marker found — check $logfile"
        exit 1
    fi
done

# ── Final report ─────────────────────────────────────────────────────────────
echo ""
echo "$(ts) ── Pipeline complete ──────────────────────────────────"
python3 - <<'PYEOF'
import sqlite3
from pathlib import Path

db = sqlite3.connect(str(Path.home() / "selyrionstory.db"))

total_ocr = db.execute("SELECT COUNT(*) FROM ocr_capsules").fetchone()[0]
promoted  = db.execute("SELECT COUNT(*) FROM capsules WHERE source_type='screenshot'").fetchone()[0]
print(f"OCR capsules total:   {total_ocr}")
print(f"Promoted screenshots: {promoted}")

print("\nPending HITL review by pass:")
for (pass_num, total, done) in db.execute(
    "SELECT pass_num, COUNT(*), SUM(CASE WHEN reviewed > 0 THEN 1 ELSE 0 END) FROM pending_review GROUP BY pass_num ORDER BY pass_num"
).fetchall():
    names = {2:'summary', 3:'relations', 4:'snapshots', 5:'style', 6:'relationship', 7:'inventions', 8:'voice'}
    print(f"  Pass {pass_num} ({names.get(pass_num,'?')}): {total} items, {done} reviewed")

db.close()
PYEOF

echo "$(ts) Done. Run selyrionstory_review.py to approve pending HITL items."
