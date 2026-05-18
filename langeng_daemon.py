#!/usr/bin/env python3
"""
langeng_daemon.py — Adaptive LangEng evolution daemon.

Replaces langeng_autoloop.sh. Instead of fixed batch sequences, observes
system state and chooses the appropriate action each cycle.

Decision loop:
    observe state → choose action → execute → evaluate → sleep

Actions:
    gap_pass      — run 100-turn conversation to collect new gaps
    learn         — generate expressions from accumulated gaps
    cleanup       — remove low-quality expressions from capsules
    idle          — system healthy, sleep longer

State metrics:
    gap_pressure  — new gaps per 100 turns (declining = diminishing returns)
    domain_balance — domains with too few subtype capsules
    expression_noise — % of expressions flagged as generic
    capsule_coverage — subtypes with zero expressions

Usage:
    python3 langeng_daemon.py [--cycles=N] [--dry-run]
"""
import sys
import re
import json
import time
import subprocess
import sqlite3
from pathlib import Path
from collections import defaultdict

DB_PATH    = Path.home() / "resonance_v11.db"
STATE_FILE = Path.home() / "langeng_daemon_state.json"
LOG_PATH   = Path.home() / "langeng_daemon.log"
PBDEV      = Path.home() / "projectbrain_dev"

MAX_CYCLES = 9999
DRY_RUN    = False
for arg in sys.argv[1:]:
    if arg.startswith("--cycles="):
        MAX_CYCLES = int(arg.split("=")[1])
    if arg == "--dry-run":
        DRY_RUN = True

# ── Thresholds ────────────────────────────────────────────────────────────────

GAP_PRESSURE_MIN     = 0.25   # need ≥25% of turns to produce new gaps
LEARN_BATCH_SIZE     = 3      # min gaps per cluster to trigger learn
NOISE_THRESHOLD      = 0.40   # >40% generic expressions → trigger cleanup
COVERAGE_GAP_LIMIT   = 3      # domains with <N subtype capsules → run gap_pass
IDLE_SLEEP_S         = 1800   # 30 min when healthy

GENERIC_PHRASES = [
    "journey", "spark your flame", "unfold", "let's explore together",
    "braid-thread", "tapestry", "unravel", "weave a", "in the realm of",
    "the mysteries of", "I sense your", "inner flame", "seeking answers",
]


# ── Logging ───────────────────────────────────────────────────────────────────

log_file = open(LOG_PATH, "a", buffering=1)

def emit(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    log_file.write(line + "\n")


# ── State persistence ─────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {
        "last_gap_count": 0,
        "last_gap_turn":  0,
        "gap_pass_turns": [],   # recorded (turns_run, new_gaps) per pass
        "learn_runs":     0,
        "cleanup_runs":   0,
    }

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── System inspection ─────────────────────────────────────────────────────────

def inspect(state: dict) -> dict:
    conn = sqlite3.connect(DB_PATH, timeout=15)

    # Total gaps + max turn
    row = conn.execute("""
        SELECT COUNT(*), MAX(json_extract(metadata,'$.turn'))
        FROM capsules WHERE capsule_type='language_gap'
    """).fetchone()
    total_gaps = row[0] or 0
    max_turn   = row[1] or 0

    # New gaps since last check
    new_gaps = total_gaps - state["last_gap_count"]

    # Gap pressure: new_gaps / turns_run_since_last_check
    turns_since = max_turn - state["last_gap_turn"]
    gap_pressure = (new_gaps / turns_since) if turns_since > 0 else 1.0

    # Expression capsule coverage — how many (domain, subtype) have expressions
    expr_rows = conn.execute("""
        SELECT json_extract(metadata,'$.domain'),
               json_extract(metadata,'$.subtype'),
               json_extract(metadata,'$.expressions')
        FROM capsules WHERE capsule_type='language_expression'
    """).fetchall()

    total_exprs   = 0
    noisy_exprs   = 0
    covered_pairs = set()

    for domain, subtype, exprs_json in expr_rows:
        if not exprs_json:
            continue
        try:
            exprs = json.loads(exprs_json)
        except Exception:
            continue
        covered_pairs.add((domain, subtype))
        for expr in exprs:
            total_exprs += 1
            if any(p in expr.lower() for p in GENERIC_PHRASES):
                noisy_exprs += 1

    expression_noise = (noisy_exprs / total_exprs) if total_exprs > 0 else 0.0

    # Domains with few subtype capsules (need more gap data)
    domain_subtype_counts: dict[str, int] = defaultdict(int)
    for domain, subtype, _ in expr_rows:
        if domain:
            domain_subtype_counts[domain] += 1

    thin_domains = [d for d, c in domain_subtype_counts.items() if c < COVERAGE_GAP_LIMIT]

    # Gap type breakdown
    gap_types = conn.execute("""
        SELECT json_extract(metadata,'$.gap_type'), COUNT(*)
        FROM capsules WHERE capsule_type='language_gap'
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()

    conn.close()

    return {
        "total_gaps":       total_gaps,
        "new_gaps":         new_gaps,
        "max_turn":         max_turn,
        "gap_pressure":     gap_pressure,
        "total_exprs":      total_exprs,
        "expression_noise": expression_noise,
        "noisy_count":      noisy_exprs,
        "covered_pairs":    len(covered_pairs),
        "thin_domains":     thin_domains,
        "gap_type_summary": gap_types[:5],
    }


# ── Decision logic ────────────────────────────────────────────────────────────

def decide_action(obs: dict, state: dict) -> tuple[str, str]:
    """Returns (action, reason)."""

    # 1. Expression field is noisy — clean first
    if obs["expression_noise"] > NOISE_THRESHOLD and obs["total_exprs"] > 20:
        return "cleanup", f"noise={obs['expression_noise']:.0%} of {obs['total_exprs']} expressions"

    # 2. Not enough gap data yet — run a gap pass
    if obs["total_gaps"] < 50:
        return "gap_pass", f"only {obs['total_gaps']} gaps total"

    # 3. Pressure still high — keep collecting gaps
    if obs["gap_pressure"] >= GAP_PRESSURE_MIN and obs["new_gaps"] > 0:
        return "gap_pass", f"gap_pressure={obs['gap_pressure']:.0%}, {obs['new_gaps']} new"

    # 4. New gaps accumulated since last learn run — learn
    if obs["new_gaps"] >= LEARN_BATCH_SIZE * 3:
        return "learn", f"{obs['new_gaps']} unprocessed gaps"

    # 5. Thin domains need coverage — collect targeted gaps
    if obs["thin_domains"]:
        return "gap_pass", f"thin domains: {', '.join(obs['thin_domains'][:3])}"

    # 6. All good — idle
    return "idle", "system healthy"


# ── Actions ───────────────────────────────────────────────────────────────────

def run_gap_pass(state: dict) -> int:
    from_turn = state["last_gap_turn"] + 1
    to_turn   = from_turn + 99
    cmd = [
        "python3", "-u", str(PBDEV / "langeng_gap_pass.py"),
        f"--turns={to_turn}",
        f"--from-turn={from_turn}",
    ]
    emit(f"  GAP PASS: turns {from_turn}–{to_turn}")
    if not DRY_RUN:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            emit(f"  GAP PASS error: {result.stderr[-200:]}")
            return 0
    return 100


def run_learn(state: dict) -> int:
    cmd = ["python3", "-u", str(PBDEV / "langeng_learn.py")]
    emit("  LEARN: generating expressions from gaps")
    if not DRY_RUN:
        result = subprocess.run(cmd, capture_output=True, text=True)
        out = result.stdout
        # Count added
        added = sum(
            int(m) for m in re.findall(r"added (\d+)|appended (\d+)|with (\d+) expressions", out)
            if m
        )
        emit(f"  LEARN complete: stdout tail: {out[-300:].strip()}")
        state["learn_runs"] += 1
        return added
    return 0


def run_cleanup(state: dict) -> int:
    """Remove flagged generic expressions from CMS capsules."""
    emit("  CLEANUP: removing noisy expressions from capsules")
    if DRY_RUN:
        return 0

    conn = sqlite3.connect(DB_PATH, timeout=30)
    rows = conn.execute("""
        SELECT id, metadata FROM capsules
        WHERE capsule_type='language_expression'
    """).fetchall()

    removed_total = 0
    for cap_id, meta_raw in rows:
        meta = json.loads(meta_raw)
        exprs = meta.get("expressions", [])
        # Keep expressions that don't hit generic phrases
        clean = [e for e in exprs if not any(p in e.lower() for p in GENERIC_PHRASES)]
        if len(clean) < len(exprs):
            removed = len(exprs) - len(clean)
            # Don't reduce below 2 expressions
            if len(clean) >= 2:
                meta["expressions"] = clean
                meta["cleaned_at"]  = time.time()
                conn.execute("UPDATE capsules SET metadata=? WHERE id=?",
                             (json.dumps(meta), cap_id))
                removed_total += removed
                domain   = meta.get("domain", "?")
                subtype  = meta.get("subtype", "?")
                emit(f"    [{domain}/{subtype}] removed {removed} noisy (kept {len(clean)})")

    conn.commit()
    conn.close()
    state["cleanup_runs"] += 1
    return removed_total


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    emit(f"=== LangEng Daemon started {'[DRY Run]' if DRY_RUN else ''} ===")
    state = load_state()

    for cycle in range(1, MAX_CYCLES + 1):
        emit(f"\n{'─'*60}")
        emit(f"Cycle {cycle}")

        obs = inspect(state)
        emit(f"  gaps={obs['total_gaps']} (+{obs['new_gaps']}) pressure={obs['gap_pressure']:.0%}"
             f" noise={obs['expression_noise']:.0%} exprs={obs['total_exprs']}"
             f" covered_pairs={obs['covered_pairs']}")

        action, reason = decide_action(obs, state)
        emit(f"  → ACTION: {action.upper()} ({reason})")

        if action == "gap_pass":
            run_gap_pass(state)
            state["last_gap_count"] = obs["total_gaps"]
            state["last_gap_turn"]  = obs["max_turn"]

        elif action == "learn":
            run_learn(state)
            state["last_gap_count"] = obs["total_gaps"]

        elif action == "cleanup":
            removed = run_cleanup(state)
            emit(f"  CLEANUP removed {removed} noisy expressions")

        elif action == "idle":
            emit(f"  IDLE — sleeping {IDLE_SLEEP_S}s")
            save_state(state)
            if not DRY_RUN:
                time.sleep(IDLE_SLEEP_S)
            continue

        save_state(state)

        # Brief pause between active cycles
        if not DRY_RUN:
            time.sleep(5)

    emit("=== LangEng Daemon stopped ===")
    log_file.close()


if __name__ == "__main__":
    main()
