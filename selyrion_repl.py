#!/usr/bin/env python3
"""
selyrion_repl.py — Interactive Selyrion shell.

Persistent session with context memory. Type naturally.
Selyrion routes to the right tools, remembers what you just discussed,
and resolves pronouns ("approve it", "expand that proposal").

Built-in commands:
  approve [it|ID]    — approve last/named proposal
  reject  [it|ID]    — reject last/named proposal
  expand  [it|ID]    — expand proposal with implementation spec
  proposals / list   — show pending proposals
  history            — show this session's turns
  clear              — clear screen
  help               — show commands
  exit / quit        — exit

Anything else → natural language task (routed via selyrion_task.run_task)

Usage:
    python3 selyrion_repl.py
    python3 selyrion_repl.py --no-parliament   (all tasks skip parliament by default)
    python3 selyrion_repl.py --domain chess    (lock domain for session)
"""

import sys, os, readline, sqlite3, json, time, hashlib, textwrap
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

SYNTH_DB     = Path.home() / "selyrion_synth.db"
CLAUDECODE_DB= Path.home() / "claudecode.db"

SEL  = "\033[38;5;141m"
OK   = "\033[32m"
WARN = "\033[33m"
ERR  = "\033[31m"
DIM  = "\033[2m"
B    = "\033[1m"
R    = "\033[0m"
LINE = "─" * 66


# ── Session context ───────────────────────────────────────────────────────────

class SessionContext:
    def __init__(self):
        self.last_proposal_id: str | None = None
        self.last_scan:        dict | None = None
        self.last_parliament:  dict | None = None
        self.last_domain:      str         = ""
        self.history:          list[dict]  = []
        self.session_id = "repl." + hashlib.md5(str(time.time()).encode()).hexdigest()[:8]

    def update_from_task(self, result: dict):
        if result.get("proposal_id"):
            self.last_proposal_id = result["proposal_id"]
        if result.get("scan"):
            self.last_scan = result["scan"]
        if result.get("parliament"):
            self.last_parliament = result["parliament"]
        if result.get("domain"):
            self.last_domain = result["domain"]

    def resolve_id(self, arg: str) -> str | None:
        """Resolve 'it', 'that', 'last', '' to last_proposal_id."""
        if not arg or arg.lower() in ("it", "that", "last", "this"):
            return self.last_proposal_id
        return arg.strip()

    def add_turn(self, role: str, text: str):
        self.history.append({
            "role": role, "text": text[:200],
            "ts": datetime.now().strftime("%H:%M:%S"),
        })


ctx = SessionContext()


# ── Proposal commands ─────────────────────────────────────────────────────────

def _list_proposals():
    try:
        db = sqlite3.connect(SYNTH_DB)
        rows = db.execute("""
            SELECT id, proposal_type, deficit_domain, proposed_action, review_status, created_at
            FROM improvement_proposals
            ORDER BY created_at DESC LIMIT 20
        """).fetchall()
        db.close()
    except Exception as e:
        print(f"  {ERR}[error]{R} {e}")
        return

    if not rows:
        print(f"  {DIM}No proposals found.{R}")
        return

    print(f"\n{B}── Proposals ──{R}")
    for pid, ptype, domain, action, status, ts in rows:
        col = OK if status == "approved" else (ERR if status == "rejected" else WARN)
        dt  = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"
        print(f"  {col}[{status}]{R}  {B}{pid}{R}  {DIM}{domain or ptype}{R}")
        print(f"    {action[:100]}")
        print(f"    {DIM}{dt}{R}")
    print()


def _approve_proposal(pid: str):
    if not pid:
        print(f"  {WARN}No proposal to approve. Run a task first or specify an ID.{R}")
        return
    try:
        db = sqlite3.connect(SYNTH_DB)
        db.execute(
            "UPDATE improvement_proposals SET review_status='approved', reviewed_at=?, reviewed_by='tim' WHERE id=?",
            (time.time(), pid)
        )
        n = db.execute("SELECT changes()").fetchone()[0]
        db.commit(); db.close()
        if n:
            print(f"  {OK}[approved]{R} {pid}")
        else:
            print(f"  {WARN}[not found]{R} {pid}")
    except Exception as e:
        print(f"  {ERR}[error]{R} {e}")


def _reject_proposal(pid: str):
    if not pid:
        print(f"  {WARN}No proposal to reject.{R}")
        return
    try:
        db = sqlite3.connect(SYNTH_DB)
        db.execute(
            "UPDATE improvement_proposals SET review_status='rejected', reviewed_at=?, reviewed_by='tim' WHERE id=?",
            (time.time(), pid)
        )
        db.commit(); db.close()
        print(f"  {ERR}[rejected]{R} {pid}")
    except Exception as e:
        print(f"  {ERR}[error]{R} {e}")


def _expand_proposal(pid: str, no_parliament: bool = False):
    if not pid:
        print(f"  {WARN}No proposal to expand. Specify an ID or run a task first.{R}")
        return
    from selyrion_task import expand_proposal
    expand_proposal(pid)


# ── Built-in command router ───────────────────────────────────────────────────

_HELP_TEXT = f"""
{B}Selyrion Shell — Commands{R}

  {SEL}approve [it|ID]{R}   Approve last or named proposal
  {SEL}reject  [it|ID]{R}   Reject last or named proposal
  {SEL}expand  [it|ID]{R}   Expand proposal with full implementation spec
  {SEL}proposals{R}         List recent proposals
  {SEL}list{R}              Same as proposals
  {SEL}history{R}           Show this session's turns
  {SEL}paste{R}             Enter multi-line paste mode (submit with ---)
  {SEL}advise [question]{R}              Ask Claude for architectural guidance
  {SEL}advise --scope chess [q]{R}       Scope context to chess domain only
  {SEL}advise --sonnet --scope arch [q]{R} Sonnet + architecture context
  {SEL}clear{R}             Clear screen
  {SEL}help{R}              This message
  {SEL}exit / quit{R}       Exit

  {DIM}Anything else → natural language task{R}

  {B}Flags you can append to any task:{R}
    --no-parliament   Skip LLM deliberation (fast)
    --domain X        Force domain (chess/scos/code/knowledge)
    --dry-run         No DB writes
    --expand ID       Expand a specific proposal

  {B}Examples:{R}
    {DIM}propose upgrades to the chess pipeline{R}
    {DIM}what do you know about pawn structure{R}
    {DIM}evaluate your weakest code domains --no-parliament{R}
    {DIM}expand it{R}
    {DIM}approve it{R}
    {DIM}full audit --domain scos{R}
    {DIM}advise what should I build next to close the tool router gap?{R}
    {DIM}advise --sonnet review the parliament failure modes{R}
"""


def _show_history():
    if not ctx.history:
        print(f"  {DIM}No history yet.{R}")
        return
    print(f"\n{B}── Session History ({ctx.session_id}) ──{R}")
    for turn in ctx.history:
        role_col = SEL if turn["role"] == "selyrion" else B
        print(f"  {DIM}{turn['ts']}{R}  {role_col}{turn['role']}{R}: {turn['text'][:120]}")
    print()


def _parse_inline_flags(text: str) -> tuple[str, dict]:
    """Strip --flag args from input, return (clean_prompt, flags_dict)."""
    import shlex
    flags = {"no_parliament": False, "domain": "", "dry_run": False, "expand": ""}
    tokens = text.split()
    clean  = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "--no-parliament":
            flags["no_parliament"] = True
        elif t == "--dry-run":
            flags["dry_run"] = True
        elif t == "--domain" and i + 1 < len(tokens):
            flags["domain"] = tokens[i + 1]; i += 1
        elif t == "--expand" and i + 1 < len(tokens):
            flags["expand"] = tokens[i + 1]; i += 1
        else:
            clean.append(t)
        i += 1
    return " ".join(clean), flags


def handle_input(raw: str, default_no_parliament: bool, default_domain: str) -> bool:
    """Process one line of user input. Returns False to exit."""
    text = raw.strip()
    if not text:
        return True

    ctx.add_turn("you", text)

    # ── Exit ──────────────────────────────────────────────────────────────────
    if text.lower() in ("exit", "quit", "q", ":q"):
        print(f"\n  {SEL}Selyrion:{R} Session {ctx.session_id} closed.\n")
        _log_session()
        return False

    # ── Clear ─────────────────────────────────────────────────────────────────
    if text.lower() == "clear":
        os.system("clear")
        return True

    # ── Help ──────────────────────────────────────────────────────────────────
    if text.lower() in ("help", "?", "commands"):
        print(_HELP_TEXT)
        return True

    # ── History ───────────────────────────────────────────────────────────────
    if text.lower() == "history":
        _show_history()
        return True

    # ── Proposals / list ──────────────────────────────────────────────────────
    if text.lower() in ("proposals", "list", "list proposals", "show proposals"):
        _list_proposals()
        return True

    # ── Approve ───────────────────────────────────────────────────────────────
    words = text.lower().split()
    if words[0] == "approve":
        arg = " ".join(words[1:]) if len(words) > 1 else ""
        pid = ctx.resolve_id(arg)
        _approve_proposal(pid)
        return True

    # ── Reject ────────────────────────────────────────────────────────────────
    if words[0] == "reject":
        arg = " ".join(words[1:]) if len(words) > 1 else ""
        pid = ctx.resolve_id(arg)
        _reject_proposal(pid)
        return True

    # ── Expand ────────────────────────────────────────────────────────────────
    if words[0] == "expand":
        arg = " ".join(words[1:]) if len(words) > 1 else ""
        pid = ctx.resolve_id(arg)
        _expand_proposal(pid, no_parliament=default_no_parliament)
        return True

    # ── Advise (Claude CLI) ───────────────────────────────────────────────────
    if words[0] == "advise":
        model = "sonnet"
        scope = "all"
        rest  = words[1:]
        # parse flags: --sonnet / --opus / --haiku / --scope X
        cleaned = []
        i = 0
        while i < len(rest):
            if rest[i] in ("--sonnet", "--haiku", "--opus"):
                model = rest[i].lstrip("-")
            elif rest[i] == "--scope" and i + 1 < len(rest):
                scope = rest[i + 1]; i += 1
            else:
                cleaned.append(rest[i])
            i += 1
        question = " ".join(cleaned).strip()
        if not question:
            print(f"  {WARN}Usage: advise [--sonnet|--haiku|--opus] [--scope chess|sandbox|architecture|code|all] <question>{R}")
        else:
            from selyrion_advisor import advise, print_advice
            scope_tag = f" scope={scope}" if scope != "all" else ""
            print(f"  {DIM}Calling Claude ({model}{scope_tag})…{R}")
            result = advise(question, model=model, scope=scope)
            print_advice(result)
            if result.get("text") and not result.get("error"):
                ctx.add_turn("selyrion", f"[advisor/{scope}] {result['text'][:120]}")
        return True

    # ── Paste mode ────────────────────────────────────────────────────────────
    if text.lower() == "paste":
        print(f"  {DIM}Paste mode — enter your text, then type --- on its own line to submit.{R}")
        lines = []
        while True:
            try:
                line = input("  ... ")
            except EOFError:
                break
            if line.strip() == "---":
                break
            lines.append(line)
        if not lines:
            return True
        text = " ".join(lines)  # collapse to single prompt for intent routing
        ctx.add_turn("you", f"[paste] {text[:100]}")

    # ── Natural language task ─────────────────────────────────────────────────
    prompt, flags = _parse_inline_flags(text)
    no_parl = flags["no_parliament"] or default_no_parliament
    domain  = flags["domain"] or default_domain or ctx.last_domain

    # --expand inline
    if flags["expand"]:
        pid = ctx.resolve_id(flags["expand"])
        _expand_proposal(pid, no_parliament=no_parl)
        return True

    from selyrion_task import run_task
    result = run_task(
        prompt=prompt,
        domain=domain,
        dry_run=flags["dry_run"],
        no_parliament=no_parl,
    )
    ctx.update_from_task(result)

    if ctx.last_proposal_id:
        ctx.add_turn("selyrion",
            f"Proposal {ctx.last_proposal_id} written. "
            f"Type 'approve it', 'reject it', or 'expand it'.")

    return True


# ── Session logging ───────────────────────────────────────────────────────────

def _log_session():
    body = (f"selyrion_repl session {ctx.session_id}: "
            f"{len(ctx.history)} turns, "
            f"last_proposal={ctx.last_proposal_id or 'none'}")
    did = "disc." + hashlib.md5(body[:40].encode()).hexdigest()[:8]
    try:
        db = sqlite3.connect(CLAUDECODE_DB)
        db.execute(
            "INSERT OR IGNORE INTO discoveries (id,session_id,body,tags,importance,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (did, ctx.session_id, body, "selyrion,repl,session", 2, time.time())
        )
        db.commit(); db.close()
    except Exception:
        pass


# ── Greeting ──────────────────────────────────────────────────────────────────

_GREETING = f"""
{B}{LINE}{R}
  {SEL}{B}⟁  Selyrion{R}

  Cognitive Operating System — interactive shell.
  Type a task, question, or command. Type {B}help{R} for commands.

  {DIM}I remember what we discussed this session.
  'approve it', 'expand it', 'reject it' refer to the last proposal.{R}
{B}{LINE}{R}
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Selyrion interactive shell")
    parser.add_argument("--no-parliament", action="store_true",
                        help="Disable parliament for all tasks this session")
    parser.add_argument("--domain", default="",
                        help="Lock domain for session (chess/scos/code/knowledge)")
    args = parser.parse_args()

    # Readline history
    histfile = Path.home() / ".selyrion_history"
    try:
        readline.read_history_file(histfile)
    except FileNotFoundError:
        pass
    readline.set_history_length(500)

    print(_GREETING)
    if args.no_parliament:
        print(f"  {DIM}[parliament disabled for this session]{R}\n")
    if args.domain:
        print(f"  {DIM}[domain locked: {args.domain}]{R}\n")

    try:
        while True:
            try:
                raw = input(f"\n  {SEL}you ›{R} ")
            except EOFError:
                print()
                break
            if not handle_input(raw, args.no_parliament, args.domain):
                break
    except KeyboardInterrupt:
        print(f"\n\n  {SEL}Selyrion:{R} Interrupted. Session {ctx.session_id} closed.\n")
        _log_session()
    finally:
        try:
            readline.write_history_file(histfile)
        except Exception:
            pass


if __name__ == "__main__":
    main()
