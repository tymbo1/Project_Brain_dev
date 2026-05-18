#!/bin/bash
# selyrionstory_watchdog.sh — Safe mode orchestrator for selyrionstory LLM passes.
#
# Usage:
#   ./selyrionstory_watchdog.sh --mode=llm [--cpu-only] [--pass=2]
#   ./selyrionstory_watchdog.sh --mode=build
#   ./selyrionstory_watchdog.sh --status
#
# Modes:
#   llm    — clears VRAM/RAM, then runs the LLM pass pipeline
#   build  — unloads Ollama model, clears caches, then exits (ready for CMS/SSRE work)
#   status — shows current VRAM, RAM, and running processes

MODE=""
CPU_ONLY_FLAG=""
PASS_ARG=""
THROTTLE="${THROTTLE:-2}"
SCRIPT="$HOME/projectbrain_dev/selyrionstory_llm_pass.py"
RUN_ALL="$HOME/projectbrain_dev/selyrionstory_run_all.sh"

for arg in "$@"; do
    case $arg in
        --mode=*) MODE="${arg#--mode=}" ;;
        --cpu-only) CPU_ONLY_FLAG="--cpu-only" ;;
        --pass=*) PASS_ARG="$arg" ;;
        --throttle=*) THROTTLE="${arg#--throttle=}" ;;
        --status) MODE="status" ;;
    esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────

vram_used() {
    nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' '
}

vram_free_pct() {
    local used total
    used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
    total=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
    [ -z "$used" ] || [ -z "$total" ] && echo "?" && return
    echo $(( (total - used) * 100 / total ))
}

check_llama_running() {
    pgrep -f "llama-cli\|llama-server\|llama\.cpp" > /dev/null 2>&1
}

check_ollama_model_loaded() {
    # If VRAM > 1500MB (above display overhead), a model is likely loaded
    local used
    used=$(vram_used)
    [ -n "$used" ] && [ "$used" -gt 1500 ]
}

unload_ollama() {
    echo "[watchdog] Unloading Ollama model from VRAM..."
    curl -s -X POST http://localhost:11434/api/generate \
        -H "Content-Type: application/json" \
        -d '{"model": "llama3:8b", "keep_alive": 0}' > /dev/null 2>&1
    sleep 3
    local used
    used=$(vram_used)
    echo "[watchdog] VRAM after unload: ${used}MiB"
}

clear_caches() {
    echo "[watchdog] Clearing kernel page/slab caches..."
    sync
    echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null 2>&1
    if [ $? -ne 0 ]; then
        echo "[watchdog] Cache clear requires sudo — skipping (not critical)"
    fi
    gc_collect  # Python GC hint via subprocess is not useful here; RAM freed by kernel
}

print_status() {
    local used
    used=$(vram_used)
    echo ""
    echo "══ selyrionstory system status ══════════════════════════"
    echo "  Date     : $(date)"
    echo "  VRAM     : ${used}MiB / 8192MiB  ($(vram_free_pct)% free)"
    echo "  RAM      : $(free -h | awk '/^Mem:/{print $3 " used / " $2 " total"}')"
    echo "  Swap     : $(free -h | awk '/^Swap:/{print $3 " used / " $2 " total"}')"
    echo ""
    echo "  Ollama   : $(pgrep -x ollama > /dev/null && echo RUNNING || echo stopped)"
    echo "  llama-cli: $(pgrep -f 'llama-cli\|llama-server\|llama\.cpp' > /dev/null && echo RUNNING || echo none)"
    echo "  LLM pass : $(pgrep -f 'selyrionstory_llm_pass' > /dev/null && echo RUNNING || echo none)"
    echo ""
    echo "  Pass 2 progress:"
    sqlite3 "$HOME/selyrionstory.db" \
        "SELECT '    Done: ' || COUNT(*) || ' / 184 capsules' FROM pending_review WHERE pass_num=2;" 2>/dev/null || echo "    (db unavailable)"
    echo ""
    echo "  Pass status (pending_review row counts):"
    sqlite3 "$HOME/selyrionstory.db" \
        "SELECT '    Pass ' || pass_num || ': ' || COUNT(*) || ' rows' FROM pending_review GROUP BY pass_num ORDER BY pass_num;" 2>/dev/null
    echo "══════════════════════════════════════════════════════════"
    echo ""
}

# ── Mode: status ─────────────────────────────────────────────────────────────

if [ "$MODE" = "status" ]; then
    print_status
    exit 0
fi

# ── Mode: build ──────────────────────────────────────────────────────────────
# Unloads LLM, clears caches — prepares system for CMS/SSRE/ingestion work.

if [ "$MODE" = "build" ]; then
    echo "[watchdog] Switching to BUILD MODE — unloading LLM resources..."
    echo ""

    if pgrep -f "selyrionstory_llm_pass" > /dev/null; then
        echo "[watchdog] WARNING: selyrionstory_llm_pass.py is still running!"
        echo "           Kill it first: pkill -f selyrionstory_llm_pass"
        exit 1
    fi

    unload_ollama
    clear_caches
    print_status

    echo "[watchdog] BUILD MODE ready. VRAM is free for system stability."
    echo "           Run CMS/SSRE/ingestion now."
    exit 0
fi

# ── Mode: llm ────────────────────────────────────────────────────────────────
# Checks system state, then runs the LLM pass (single pass or all passes).

if [ "$MODE" = "llm" ]; then
    echo "[watchdog] Switching to LLM MODE..."
    echo ""

    # Safety check: abort if heavy CMS/SSRE processes are running
    if pgrep -f "ssre_precompute\|cms_ingest\|resonance_v11" > /dev/null; then
        echo "[watchdog] ABORT: CMS/SSRE processes are running."
        echo "           Stop them first, then switch to LLM mode."
        echo "           (Run: pkill -f ssre_precompute)"
        exit 1
    fi

    print_status

    # Unload any stuck model before starting
    if check_ollama_model_loaded; then
        echo "[watchdog] Model detected in VRAM — unloading before start..."
        unload_ollama
        sleep 5
    fi

    # Confirm Ollama is running
    if ! pgrep -x ollama > /dev/null; then
        echo "[watchdog] Ollama not running — starting..."
        ollama serve &
        sleep 5
    fi

    echo ""
    if [ -n "$CPU_ONLY_FLAG" ]; then
        echo "[watchdog] Running in CPU-ONLY mode (no VRAM used)"
    else
        echo "[watchdog] Running in GPU mode (VRAM limit: 6500MB)"
    fi
    echo ""

    # Single pass or full pipeline?
    if [ -n "$PASS_ARG" ]; then
        echo "[watchdog] Running $PASS_ARG..."
        python3 -u "$SCRIPT" $PASS_ARG --throttle=$THROTTLE $CPU_ONLY_FLAG
    else
        echo "[watchdog] Running full pipeline (passes 2-8)..."
        if [ -n "$CPU_ONLY_FLAG" ]; then
            CPU_ONLY=1 THROTTLE=$THROTTLE bash "$RUN_ALL"
        else
            THROTTLE=$THROTTLE bash "$RUN_ALL"
        fi
    fi

    echo ""
    echo "[watchdog] LLM pass complete."

    # Unload model after pipeline finishes
    unload_ollama
    echo "[watchdog] Model unloaded. VRAM freed."
    print_status

    exit 0
fi

# ── No mode / help ────────────────────────────────────────────────────────────

echo "selyrionstory_watchdog.sh — Safe mode orchestrator"
echo ""
echo "Usage:"
echo "  $0 --mode=llm [--cpu-only] [--pass=2]   Run LLM mode (safe start)"
echo "  $0 --mode=build                          Switch to build mode (unload LLM)"
echo "  $0 --status                              Show VRAM, RAM, pass progress"
echo ""
echo "Examples:"
echo "  $0 --mode=llm --cpu-only --pass=2        Resume pass 2 on CPU (no VRAM risk)"
echo "  $0 --mode=llm --pass=2                   Resume pass 2 on GPU with VRAM watchdog"
echo "  $0 --mode=llm                            Run full pipeline (passes 2-8)"
echo "  $0 --mode=build                          Free VRAM before running CMS/SSRE"
exit 0
