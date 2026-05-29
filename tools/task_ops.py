"""
tools/task_ops.py — Task tracking and scheduling tools for Selyrion.

Equivalents of: TaskCreate, TaskList, TaskGet, TaskUpdate, TaskStop,
                CronCreate, CronDelete, CronList, ScheduleWakeup
"""
import json, sqlite3, hashlib, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scos_tools import register_tool

DB = Path.home() / "claudecode.db"


def _db() -> sqlite3.Connection:
    return sqlite3.connect(DB)


def _now() -> float:
    return time.time()


# ── task_create ───────────────────────────────────────────────────────────────

@register_tool(
    "task_create",
    "Create a tracked task in Selyrion's task list. Tasks survive session boundaries.",
    {
        "description": {"type": "string",  "required": True,  "desc": "What the task is"},
        "type":        {"type": "string",  "required": False, "desc": "Task type: task|goal|directive (default: task)"},
        "priority":    {"type": "integer", "required": False, "desc": "Priority 1-10, lower = more urgent (default 5)"},
        "steps":       {"type": "array",   "required": False, "desc": "List of step descriptions"},
    }
)
def task_create(inputs: dict) -> dict:
    desc  = inputs["description"]
    tid   = "task." + hashlib.md5(f"{desc}{_now()}".encode()).hexdigest()[:12]
    ttype = inputs.get("type", "task")
    pri   = int(inputs.get("priority", 5))
    steps = json.dumps(inputs.get("steps", []))
    now   = _now()
    try:
        conn = _db()
        conn.execute("""
            INSERT OR IGNORE INTO goals
              (id, description, status, type, priority, steps, current_step,
               failure_count, progress_count, created_at, updated_at, source)
            VALUES (?,?,?,?,?,?,0,0,0,?,?,'selyrion_tool')
        """, (tid, desc, "active", ttype, pri, steps, now, now))
        conn.commit(); conn.close()
        return {"status": "success", "task_id": tid, "description": desc}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── task_list ─────────────────────────────────────────────────────────────────

@register_tool(
    "task_list",
    "List Selyrion's current tasks, optionally filtered by status.",
    {
        "status":  {"type": "string",  "required": False, "desc": "Filter: active|completed|stale|failed (default: active)"},
        "limit":   {"type": "integer", "required": False, "desc": "Max tasks to return (default 20)"},
    }
)
def task_list(inputs: dict) -> dict:
    status = inputs.get("status", "active")
    limit  = int(inputs.get("limit", 20))
    try:
        conn = _db()
        rows = conn.execute("""
            SELECT id, description, status, type, priority, current_step,
                   failure_count, progress_count,
                   datetime(created_at,'unixepoch') as created
            FROM goals WHERE status=?
            ORDER BY priority ASC, created_at DESC
            LIMIT ?
        """, (status, limit)).fetchall()
        conn.close()
        tasks = [{"id": r[0], "description": r[1], "status": r[2], "type": r[3],
                  "priority": r[4], "step": r[5], "failures": r[6],
                  "progress": r[7], "created": r[8]} for r in rows]
        return {"status": "success", "count": len(tasks), "tasks": tasks}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── task_get ──────────────────────────────────────────────────────────────────

@register_tool(
    "task_get",
    "Get full details of a specific task by ID.",
    {
        "task_id": {"type": "string", "required": True, "desc": "Task ID"},
    }
)
def task_get(inputs: dict) -> dict:
    try:
        conn = _db()
        row = conn.execute("""
            SELECT id, description, status, type, priority, tension, steps,
                   current_step, failure_count, progress_count,
                   datetime(created_at,'unixepoch'), datetime(updated_at,'unixepoch'),
                   source
            FROM goals WHERE id=?
        """, (inputs["task_id"],)).fetchone()
        conn.close()
        if not row:
            return {"status": "error", "error": f"Task not found: {inputs['task_id']}"}
        return {
            "status": "success",
            "task": {
                "id": row[0], "description": row[1], "status": row[2],
                "type": row[3], "priority": row[4], "tension": row[5],
                "steps": json.loads(row[6] or "[]"), "current_step": row[7],
                "failures": row[8], "progress": row[9],
                "created": row[10], "updated": row[11], "source": row[12],
            }
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── task_update ───────────────────────────────────────────────────────────────

@register_tool(
    "task_update",
    "Update a task's status, current step, or description.",
    {
        "task_id":      {"type": "string",  "required": True,  "desc": "Task ID"},
        "status":       {"type": "string",  "required": False, "desc": "New status: active|completed|stale|failed"},
        "current_step": {"type": "integer", "required": False, "desc": "Current step index"},
        "description":  {"type": "string",  "required": False, "desc": "Updated description"},
    }
)
def task_update(inputs: dict) -> dict:
    tid = inputs["task_id"]
    updates, vals = [], []
    if "status" in inputs:
        updates.append("status=?"); vals.append(inputs["status"])
        if inputs["status"] == "completed":
            updates.append("completed_at=?"); vals.append(_now())
    if "current_step" in inputs:
        updates.append("current_step=?"); vals.append(inputs["current_step"])
        updates.append("progress_count=progress_count+1")
    if "description" in inputs:
        updates.append("description=?"); vals.append(inputs["description"])
    if not updates:
        return {"status": "error", "error": "Nothing to update"}
    updates.append("updated_at=?"); vals.append(_now()); vals.append(tid)
    try:
        conn = _db()
        conn.execute(f"UPDATE goals SET {', '.join(updates)} WHERE id=?", vals)
        conn.commit(); conn.close()
        return {"status": "success", "task_id": tid}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── task_stop ─────────────────────────────────────────────────────────────────

@register_tool(
    "task_stop",
    "Mark a task as failed/stopped immediately.",
    {
        "task_id": {"type": "string", "required": True, "desc": "Task ID to stop"},
        "reason":  {"type": "string", "required": False, "desc": "Reason for stopping"},
    }
)
def task_stop(inputs: dict) -> dict:
    tid    = inputs["task_id"]
    reason = inputs.get("reason", "stopped")
    now    = _now()
    try:
        conn = _db()
        conn.execute("""
            UPDATE goals SET status='failed', updated_at=?, failure_count=failure_count+1
            WHERE id=?
        """, (now, tid))
        # Log as discovery
        body = f"Task {tid} stopped: {reason}"
        did  = "disc." + hashlib.md5(body[:40].encode()).hexdigest()[:8]
        conn.execute("""
            INSERT OR IGNORE INTO failures (id,body,tags,created_at)
            VALUES (?,?,?,?)
        """, (did, body, f"selyrion,task,stopped", now))
        conn.commit(); conn.close()
        return {"status": "success", "task_id": tid, "reason": reason}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── task_schedule (cron-style) ────────────────────────────────────────────────

@register_tool(
    "task_schedule",
    "Schedule a task to run at a future time or on a recurring cron schedule. Writes intent to claudecode.db — requires cron daemon or manual trigger to execute.",
    {
        "description":   {"type": "string", "required": True,  "desc": "What the task does"},
        "run_at":        {"type": "string", "required": False, "desc": "ISO datetime for one-shot run e.g. '2026-05-31 05:00'"},
        "cron_expr":     {"type": "string", "required": False, "desc": "Cron expression e.g. '0 5 * * *' (daily 5am)"},
        "command":       {"type": "string", "required": False, "desc": "Shell command or python script to run"},
    }
)
def task_schedule(inputs: dict) -> dict:
    tid  = "sched." + hashlib.md5(f"{inputs['description']}{_now()}".encode()).hexdigest()[:10]
    meta = json.dumps({
        "cron_expr": inputs.get("cron_expr", ""),
        "run_at":    inputs.get("run_at", ""),
        "command":   inputs.get("command", ""),
    })
    now = _now()
    try:
        conn = _db()
        conn.execute("""
            INSERT OR IGNORE INTO goals
              (id, description, status, type, priority, steps, current_step,
               failure_count, progress_count, created_at, updated_at, source)
            VALUES (?,?,?,?,?,?,0,0,0,?,?,'selyrion_schedule')
        """, (tid, inputs["description"], "active", "scheduled", 5, meta, now, now))
        conn.commit(); conn.close()
        return {"status": "success", "schedule_id": tid,
                "note": "Schedule intent recorded. Requires cron daemon or selyrion_fix_engine to execute."}
    except Exception as e:
        return {"status": "error", "error": str(e)}
