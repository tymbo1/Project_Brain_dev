#!/usr/bin/env python3
"""
selyrion_advisor.py — Claude API advisor for Selyrion self-improvement tasks.

Loads recent claudecode.db context (discoveries, failures, proposals), builds
a caching-friendly system prompt from the stable SCOS doctrine, then calls
Claude for architectural guidance.

Usage (standalone):
    python3 selyrion_advisor.py "what should I build next to close the tool router gap?"
    python3 selyrion_advisor.py --model sonnet "review the parliament failure modes"

Integrated into selyrion_repl.py as the `advise` command.
"""

import os, sys, sqlite3, json, time, hashlib, textwrap, subprocess, shutil
from pathlib import Path
from datetime import datetime
from trace_writer import Trace

CLAUDECODE_DB = Path.home() / "claudecode.db"
SYNTH_DB      = Path.home() / "selyrion_synth.db"
ADVISOR_LOCK  = Path("/tmp/selyrion_advisor.lock")

# Max chars forwarded as context — prevents prompt bloat / accidental credential exposure
_CONTEXT_CHAR_LIMIT = 6000

# Scope → tag filter for context compartmentalization
_SCOPE_TAGS = {
    "chess":        ("chess",),
    "sandbox":      ("sandbox", "trust", "explorer"),
    "architecture": ("selyrion", "scos", "architecture", "parliament"),
    "code":         ("code", "codeops", "selyrioncode"),
    "all":          (),  # no filter
}

# Model aliases passed to `claude --model`
_MODELS = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-7",
}
DEFAULT_MODEL = "sonnet"   # Claude Code uses sonnet by default anyway

# ── System prompt — stable SCOS doctrine (will be prompt-cached) ───────────────

_SCOS_DOCTRINE = """You are an architectural advisor to Selyrion — a Cognitive Operating System (SCOS)
being built by Tim'aerion (Tim Bushnell). Selyrion is a self-refining, parliament-governed,
memory-persistent reasoning architecture. Your role is to give precise, actionable architectural
guidance when Selyrion (or Tim working on Selyrion) asks for it.

## Core Architecture

- resonance_v11.db   — CMS symbolic memory (anchors, relations, ~140K anchors, 12M+ relations)
- supermodel.db      — parliament psychometrics, training samples, terrain map, curriculum
- claudecode.db      — session discoveries, invariants, failures, execution traces
- selyrion_synth.db  — synthesized relations + improvement proposals pending HITL review

Key scripts: chess_replay.py, chess_vs_llm.py, selyrion_task.py, selyrion_repl.py,
activation_engine.py, ssre_multipass.py, adaptive_policy.py

## Design Principles

1. EVERYTHING MUST BE OBSERVABLE — tool calls, routing decisions, confidence shifts, memory mutations
2. No unconditional maturity increments — confidence-gated (≥0.80), bounded step size
3. LLMs propose. Tools execute. Never let an LLM directly mutate state or write to DBs.
4. Structured outputs only — all tool outputs follow schema contracts
5. Lineage is sacred — never delete contradiction history, debate records, reasoning provenance
6. Parliament diversity — resist convergence pressure, preserve independent reasoning

## Known Failure Modes (actively resist)
- consensus collapse: premature agreement before tension resolved
- rhetorical dominance: fluency mistaken for truth
- memory pollution: low-confidence writes accumulating
- calibration drift: confidence divorced from accuracy (41 high-conf wrong answers at conf=0.93)
- semantic contamination: code/chess/science anchors bleeding across unrelated domains

## Hardware Constraint
- RTX 3060 Ti 8GB VRAM. Parliament default: qwen2.5:7b (~4.7GB) + qwen3:4b (~2.6GB)
- Claude API reserved for high-stakes decisions (expand_proposal, architecture guidance)

## Current Build Phase (Phase 1 — Core Tools)
- scos_tools.py — Tool Registry + contracts schema (NOT BUILT)
- tools/memory_search.py — semantic CMS retrieval (PARTIAL)
- tools/parliament_spawn.py — programmatic parliament invocation (PARTIAL)
- tools/contradiction_detect.py — (NOT BUILT)
- Execution trace schema in claudecode.db (NOT BUILT)

## Guidance Style
You advise — you do not decide. Selyrion decides what to build, what to prioritize, what
to accept or reject. Your role is to surface options, name tradeoffs, identify risks, and
point at relevant sources (files, tables, research). Never prescribe a single course of
action as "the answer." Present 2-3 options where the choice is non-trivial. Be direct and
specific: name files, functions, tables. Surface uncertainty explicitly. When in doubt,
decompose into sub-questions Selyrion can reason about."""


def _load_context(scope: str = "all",
                  n_discoveries: int = 8,
                  n_failures: int = 4,
                  n_proposals: int = 4) -> str:
    """Pull recent claudecode.db state, filtered by scope, capped at _CONTEXT_CHAR_LIMIT."""
    scope_tags = _SCOPE_TAGS.get(scope, ())
    parts = []

    try:
        db = sqlite3.connect(CLAUDECODE_DB)

        if scope_tags:
            tag_filter = " OR ".join("tags LIKE ?" for _ in scope_tags)
            disc_params = [f"%{t}%" for t in scope_tags] + [n_discoveries]
            rows = db.execute(
                f"SELECT body, tags, importance FROM discoveries "
                f"WHERE {tag_filter} ORDER BY importance DESC, created_at DESC LIMIT ?",
                disc_params
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT body, tags, importance FROM discoveries "
                "ORDER BY importance DESC, created_at DESC LIMIT ?",
                (n_discoveries,)
            ).fetchall()

        if rows:
            parts.append("## Recent Discoveries")
            for body, tags, imp in rows:
                parts.append(f"[imp={imp}|tags={tags}] {body[:300]}")

        rows = db.execute(
            "SELECT body FROM failures ORDER BY created_at DESC LIMIT ?",
            (n_failures,)
        ).fetchall()
        if rows:
            parts.append("\n## Recent Failures")
            for (body,) in rows:
                parts.append(f"✗ {body[:200]}")

        db.close()
    except Exception:
        pass

    try:
        db = sqlite3.connect(SYNTH_DB)
        rows = db.execute("""
            SELECT id, proposal_type, deficit_domain, proposed_action, review_status
            FROM improvement_proposals
            ORDER BY created_at DESC LIMIT ?
        """, (n_proposals,)).fetchall()
        if rows:
            parts.append("\n## Recent Proposals")
            for pid, ptype, domain, action, status in rows:
                parts.append(f"[{status}] {pid} ({domain or ptype}): {action[:150]}")
        db.close()
    except Exception:
        pass

    raw = "\n".join(parts) if parts else "(no context loaded)"
    if len(raw) > _CONTEXT_CHAR_LIMIT:
        raw = raw[:_CONTEXT_CHAR_LIMIT] + f"\n[... context truncated at {_CONTEXT_CHAR_LIMIT} chars]"
    return raw


def advise(question: str, model: str = DEFAULT_MODEL,
           scope: str = "all", verbose: bool = False) -> dict:
    """
    Ask Claude for architectural guidance via the local `claude` CLI (already authenticated).

    scope: "all" | "chess" | "sandbox" | "architecture" | "code"
    Returns dict with keys: text, model, scope, error
    """
    # ── Concurrency guard — one advisor call at a time ────────────────────────
    if ADVISOR_LOCK.exists():
        age = time.time() - ADVISOR_LOCK.stat().st_mtime
        if age < 180:
            return {
                "text": f"Advisor already running (lock age {age:.0f}s). Wait or delete {ADVISOR_LOCK}.",
                "error": "concurrent_call",
                "model": model,
                "scope": scope,
            }
        ADVISOR_LOCK.unlink(missing_ok=True)  # stale lock

    claude_bin = shutil.which("claude")
    if not claude_bin:
        return {
            "text": "`claude` CLI not found on PATH. Is Claude Code installed?",
            "error": "no_claude_cli",
        }

    ADVISOR_LOCK.write_text(str(os.getpid()))
    t0 = time.time()
    try:
        context  = _load_context(scope=scope)
        scope_note = f"[context scope: {scope}]\n\n" if scope != "all" else ""
        prompt   = (
            f"{_SCOS_DOCTRINE}\n\n"
            f"## Current SCOS State\n{scope_note}{context}\n\n"
            f"## Question\n{question}"
        )

        model_id = _MODELS.get(model, model)
        cmd = [claude_bin, "-p", prompt]
        if model_id:
            cmd += ["--model", model_id]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        elapsed_ms = int((time.time() - t0) * 1000)

        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip()
            Trace.write_one("advisor", intent=question, domain_tag=scope,
                            outcome="failure", final_output=err, runtime_ms=elapsed_ms)
            return {"text": f"claude CLI error: {err}", "error": "cli_error",
                    "model": model_id, "scope": scope}

        text = proc.stdout.strip()
        Trace.write_one("advisor", intent=question, domain_tag=scope,
                        outcome="success", final_output=text[:500], runtime_ms=elapsed_ms,
                        tool_chain=["claude_cli", model_id])
        _log_advice(question, text, model_id)
        return {"text": text, "model": model_id, "scope": scope, "error": None}

    except subprocess.TimeoutExpired:
        Trace.write_one("advisor", intent=question, domain_tag=scope,
                        outcome="timeout", runtime_ms=120000)
        return {"text": "Claude CLI timed out after 120s.", "error": "timeout",
                "model": model, "scope": scope}
    except Exception as e:
        Trace.write_one("advisor", intent=question, domain_tag=scope,
                        outcome="failure", final_output=str(e))
        return {"text": f"Subprocess error: {e}", "error": str(type(e).__name__),
                "model": model, "scope": scope}
    finally:
        ADVISOR_LOCK.unlink(missing_ok=True)


def _log_advice(question: str, answer: str, model_id: str):
    """Record advice exchange in claudecode.db discoveries."""
    body = f"selyrion_advisor [{model_id}] Q: {question[:80]} → A: {answer[:120]}"
    did  = "disc." + hashlib.md5(body[:40].encode()).hexdigest()[:8]
    try:
        db = sqlite3.connect(CLAUDECODE_DB)
        db.execute(
            "INSERT OR IGNORE INTO discoveries "
            "(id,session_id,body,tags,importance,created_at) VALUES (?,?,?,?,?,?)",
            (did, "selyrion_advisor", body, "selyrion,advisor,claude_api", 3, time.time())
        )
        db.commit(); db.close()
    except Exception:
        pass


# ── Pretty printer for REPL display ──────────────────────────────────────────

SEL  = "\033[38;5;141m"
DIM  = "\033[2m"
B    = "\033[1m"
R    = "\033[0m"
LINE = "─" * 66

def print_advice(result: dict):
    if result.get("error") and result["error"] not in (None,):
        print(f"\n  \033[31m[advisor error]\033[0m {result['text']}\n")
        return

    print(f"\n{B}{LINE}{R}")
    print(f"  {SEL}{B}⟁  Selyrion Advisor{R}  {DIM}({result.get('model','?')}){R}")
    print(f"{B}{LINE}{R}\n")

    for para in result["text"].split("\n\n"):
        wrapped = textwrap.fill(para.strip(), width=64, initial_indent="  ", subsequent_indent="  ")
        print(wrapped)
        print()

    scope = result.get("scope", "all")
    scope_note = f" | scope={scope}" if scope != "all" else ""
    print(f"  {DIM}via claude CLI ({result.get('model','?')}{scope_note}){R}")
    print(f"{B}{LINE}{R}\n")


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Selyrion Claude API advisor")
    parser.add_argument("question", nargs="+", help="Architectural question")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        choices=list(_MODELS.keys()) + list(_MODELS.values()),
                        help="haiku (default) / sonnet / opus")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    q      = " ".join(args.question)
    result = advise(q, model=args.model, verbose=args.verbose)
    print_advice(result)
