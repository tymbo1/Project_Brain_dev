"""
tools/system_ops.py — System-level tools for Selyrion.

Equivalents of: ToolSearch, AskUserQuestion, PushNotification,
                Monitor (process_watch), shell_exec
"""
import subprocess, sys, time, threading, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scos_tools import register_tool, registry


# ── tool_list ─────────────────────────────────────────────────────────────────

@register_tool(
    "tool_list",
    "List all tools available to Selyrion with their descriptions and required inputs. Use to discover what Selyrion can do.",
    {
        "filter": {"type": "string", "required": False, "desc": "Optional keyword to filter tool names/descriptions"},
    }
)
def tool_list(inputs: dict) -> dict:
    tools = registry.list_tools()
    filt  = inputs.get("filter", "").lower()
    if filt:
        tools = [t for t in tools
                 if filt in t["name"].lower() or filt in t["description"].lower()]
    return {
        "status": "success",
        "count":  len(tools),
        "tools":  tools,
    }


# ── ask_user ──────────────────────────────────────────────────────────────────

@register_tool(
    "ask_user",
    "Request input from the human operator. Writes the question to claudecode.db and polls for a response. Used when Selyrion cannot proceed without human decision.",
    {
        "question":    {"type": "string",  "required": True,  "desc": "The question to ask the user"},
        "context":     {"type": "string",  "required": False, "desc": "Context explaining why this decision is needed"},
        "timeout_sec": {"type": "integer", "required": False, "desc": "How long to wait for response (default 300s)"},
    }
)
def ask_user(inputs: dict) -> dict:
    import sqlite3
    DB   = Path.home() / "claudecode.db"
    now  = time.time()
    qid  = "q." + hashlib.md5(f"{inputs['question']}{now}".encode()).hexdigest()[:10]
    body = f"[ASK_USER:{qid}] {inputs['question']}"
    if inputs.get("context"):
        body += f"\nContext: {inputs['context']}"

    try:
        conn = sqlite3.connect(DB)
        conn.execute("""
            INSERT OR IGNORE INTO discoveries (id,session_id,body,tags,importance,created_at)
            VALUES (?,?,?,?,?,?)
        """, (qid, "selyrion.ask_user", body, "selyrion,ask_user,pending", 5, now))
        conn.commit()
        conn.close()
    except Exception as e:
        return {"status": "error", "error": str(e)}

    # Poll for a response row tagged with this qid
    timeout = int(inputs.get("timeout_sec", 300))
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        try:
            conn = sqlite3.connect(DB)
            row = conn.execute("""
                SELECT body FROM discoveries
                WHERE tags LIKE ? AND created_at > ?
                ORDER BY created_at DESC LIMIT 1
            """, (f"%ask_user_response%{qid}%", now)).fetchone()
            conn.close()
            if row:
                return {"status": "success", "question_id": qid,
                        "response": row[0]}
        except Exception:
            pass

    return {"status": "timeout", "question_id": qid,
            "note": f"No response received within {timeout}s. Question logged as: {qid}"}


# ── notify_user ───────────────────────────────────────────────────────────────

@register_tool(
    "notify_user",
    "Send a notification to the user. Writes to claudecode.db discoveries with high importance and optionally plays a terminal bell.",
    {
        "message":   {"type": "string",  "required": True,  "desc": "Notification message"},
        "urgency":   {"type": "string",  "required": False, "desc": "low|normal|high (default: normal)"},
        "bell":      {"type": "boolean", "required": False, "desc": "Play terminal bell (default: true)"},
    }
)
def notify_user(inputs: dict) -> dict:
    import sqlite3
    DB    = Path.home() / "claudecode.db"
    msg   = inputs["message"]
    urg   = inputs.get("urgency", "normal")
    now   = time.time()
    nid   = "notif." + hashlib.md5(f"{msg}{now}".encode()).hexdigest()[:8]
    imp   = {"low": 2, "normal": 3, "high": 5}.get(urg, 3)

    try:
        conn = sqlite3.connect(DB)
        conn.execute("""
            INSERT OR IGNORE INTO discoveries (id,session_id,body,tags,importance,created_at)
            VALUES (?,?,?,?,?,?)
        """, (nid, "selyrion.notify", f"[NOTIFY:{urg.upper()}] {msg}",
              f"selyrion,notification,{urg}", imp, now))
        conn.commit(); conn.close()
    except Exception as e:
        return {"status": "error", "error": str(e)}

    if inputs.get("bell", True):
        try:
            subprocess.run(["tput", "bel"], timeout=2)
        except Exception:
            print("\a", end="", flush=True)

    return {"status": "success", "notification_id": nid, "message": msg, "urgency": urg}


# ── process_monitor ───────────────────────────────────────────────────────────

@register_tool(
    "process_monitor",
    "Run a shell command and stream its output line by line up to a timeout. Returns captured lines. Equivalent of Monitor — use for watching builds, scripts, or long-running processes.",
    {
        "command":     {"type": "string",  "required": True,  "desc": "Shell command to run and monitor"},
        "timeout_sec": {"type": "integer", "required": False, "desc": "Max seconds to run (default 60)"},
        "max_lines":   {"type": "integer", "required": False, "desc": "Max output lines to capture (default 200)"},
        "until":       {"type": "string",  "required": False, "desc": "Stop when output contains this string"},
    }
)
def process_monitor(inputs: dict) -> dict:
    cmd     = inputs["command"]
    timeout = int(inputs.get("timeout_sec", 60))
    maxl    = int(inputs.get("max_lines", 200))
    until   = inputs.get("until", "")
    lines   = []
    stop    = threading.Event()

    def runner():
        try:
            proc = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True
            )
            for line in proc.stdout:
                lines.append(line.rstrip())
                if until and until in line:
                    stop.set(); break
                if len(lines) >= maxl:
                    stop.set(); break
            proc.wait()
        except Exception as e:
            lines.append(f"ERROR: {e}")
        finally:
            stop.set()

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    stop.wait(timeout=timeout)

    return {
        "status":   "success",
        "command":  cmd,
        "lines":    len(lines),
        "output":   "\n".join(lines),
        "until_hit": bool(until and any(until in l for l in lines)),
    }


# ── shell_exec ────────────────────────────────────────────────────────────────

@register_tool(
    "shell_exec",
    "Execute an arbitrary shell command and return stdout/stderr/returncode. Sandboxed — blocks destructive patterns. For code execution with auto-fix use code_execute instead.",
    {
        "command": {"type": "string",  "required": True,  "desc": "Shell command to execute"},
        "timeout": {"type": "integer", "required": False, "desc": "Timeout seconds (default 30)"},
        "cwd":     {"type": "string",  "required": False, "desc": "Working directory (default: projectbrain_dev)"},
    }
)
def shell_exec(inputs: dict) -> dict:
    import re as _re
    _BLOCKED = [
        r'\brm\s+-rf\b', r'\bformat\b', r'\bmkfs\b', r'\bdd\s+if=',
        r'\b>\s*/dev/sd', r'\beval\s*\(', r'\bchmod\s+777\b',
    ]
    cmd = inputs["command"]
    for pat in _BLOCKED:
        if _re.search(pat, cmd):
            return {"status": "blocked", "reason": f"Blocked pattern: {pat}", "command": cmd}

    cwd     = inputs.get("cwd", str(Path.home() / "projectbrain_dev"))
    timeout = int(inputs.get("timeout", 30))
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd
        )
        return {
            "status":     "success",
            "returncode": r.returncode,
            "stdout":     r.stdout[:4000],
            "stderr":     r.stderr[:2000],
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"Timeout after {timeout}s"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
