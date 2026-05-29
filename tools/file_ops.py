"""
tools/file_ops.py — File system tools for Selyrion.

Equivalents of: Read, Write, Edit, Glob, Grep
"""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scos_tools import register_tool

_HOME = Path.home()
_ROOT = _HOME / "projectbrain_dev"

# ── Safety: confine writes to known safe dirs ─────────────────────────────────
_WRITE_ALLOWED = [
    _HOME / "projectbrain_dev",
    _HOME / "claudecode.db",
    Path("/tmp"),
]

def _safe_write_path(path: str) -> bool:
    p = Path(path).resolve()
    return any(str(p).startswith(str(a)) for a in _WRITE_ALLOWED)


# ── file_read ─────────────────────────────────────────────────────────────────

@register_tool(
    "file_read",
    "Read a file's contents. Supports offset (line number to start from) and limit (max lines).",
    {
        "path":   {"type": "string",  "required": True,  "desc": "Absolute or ~/relative file path"},
        "offset": {"type": "integer", "required": False, "desc": "Start from this line number (1-indexed)"},
        "limit":  {"type": "integer", "required": False, "desc": "Maximum number of lines to return"},
    }
)
def file_read(inputs: dict) -> dict:
    path = Path(str(inputs["path"]).replace("~", str(_HOME))).resolve()
    if not path.exists():
        return {"status": "error", "error": f"File not found: {path}"}
    try:
        lines = path.read_text(errors="replace").splitlines()
        offset = max(0, int(inputs.get("offset", 1)) - 1)
        limit  = int(inputs.get("limit", 2000))
        chunk  = lines[offset:offset + limit]
        return {
            "status":      "success",
            "path":        str(path),
            "total_lines": len(lines),
            "returned":    len(chunk),
            "content":     "\n".join(f"{offset+i+1}\t{l}" for i, l in enumerate(chunk)),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── file_write ────────────────────────────────────────────────────────────────

@register_tool(
    "file_write",
    "Write content to a file, creating or overwriting it. Confined to projectbrain_dev and /tmp.",
    {
        "path":    {"type": "string", "required": True, "desc": "Absolute or ~/relative file path"},
        "content": {"type": "string", "required": True, "desc": "Full content to write"},
    }
)
def file_write(inputs: dict) -> dict:
    path = Path(str(inputs["path"]).replace("~", str(_HOME))).resolve()
    if not _safe_write_path(str(path)):
        return {"status": "error", "error": f"Write not allowed outside safe dirs: {path}"}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(inputs["content"])
        return {"status": "success", "path": str(path), "bytes": len(inputs["content"])}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── file_edit ─────────────────────────────────────────────────────────────────

@register_tool(
    "file_edit",
    "Replace an exact string in a file. Fails if old_string not found or not unique. Safer than rewriting the whole file.",
    {
        "path":        {"type": "string",  "required": True,  "desc": "File path"},
        "old_string":  {"type": "string",  "required": True,  "desc": "Exact text to find and replace"},
        "new_string":  {"type": "string",  "required": True,  "desc": "Replacement text"},
        "replace_all": {"type": "boolean", "required": False, "desc": "Replace all occurrences (default false)"},
    }
)
def file_edit(inputs: dict) -> dict:
    path = Path(str(inputs["path"]).replace("~", str(_HOME))).resolve()
    if not _safe_write_path(str(path)):
        return {"status": "error", "error": f"Write not allowed: {path}"}
    if not path.exists():
        return {"status": "error", "error": f"File not found: {path}"}
    try:
        content = path.read_text(errors="replace")
        old = inputs["old_string"]
        new = inputs["new_string"]
        count = content.count(old)
        if count == 0:
            return {"status": "error", "error": "old_string not found in file"}
        if count > 1 and not inputs.get("replace_all"):
            return {"status": "error",
                    "error": f"old_string found {count} times — use replace_all=true or provide more context"}
        updated = content.replace(old, new) if inputs.get("replace_all") else content.replace(old, new, 1)
        path.write_text(updated)
        return {"status": "success", "path": str(path), "replacements": count if inputs.get("replace_all") else 1}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── file_search (glob) ────────────────────────────────────────────────────────

@register_tool(
    "file_search",
    "Find files matching a glob pattern. Returns paths sorted by modification time.",
    {
        "pattern": {"type": "string", "required": True,  "desc": "Glob pattern e.g. '**/*.py', 'tools/*.py'"},
        "root":    {"type": "string", "required": False, "desc": "Root directory to search (default: projectbrain_dev)"},
    }
)
def file_search(inputs: dict) -> dict:
    root = Path(str(inputs.get("root", str(_ROOT))).replace("~", str(_HOME))).resolve()
    if not root.exists():
        return {"status": "error", "error": f"Root not found: {root}"}
    try:
        matches = sorted(root.glob(inputs["pattern"]), key=lambda p: p.stat().st_mtime, reverse=True)
        return {
            "status": "success",
            "count":  len(matches),
            "paths":  [str(p) for p in matches[:200]],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── content_search (grep) ─────────────────────────────────────────────────────

@register_tool(
    "content_search",
    "Search file contents using regex (ripgrep-style). Returns matching lines with file and line number.",
    {
        "pattern":    {"type": "string",  "required": True,  "desc": "Regex pattern to search for"},
        "root":       {"type": "string",  "required": False, "desc": "Directory to search (default: projectbrain_dev)"},
        "glob":       {"type": "string",  "required": False, "desc": "File filter e.g. '*.py'"},
        "max_results":{"type": "integer", "required": False, "desc": "Max matching lines to return (default 100)"},
        "ignore_case":{"type": "boolean", "required": False, "desc": "Case-insensitive search"},
    }
)
def content_search(inputs: dict) -> dict:
    import subprocess, shutil
    root  = str(Path(str(inputs.get("root", str(_ROOT))).replace("~", str(_HOME))).resolve())
    limit = int(inputs.get("max_results", 100))
    pat   = inputs["pattern"]
    glob  = inputs.get("glob", "")
    icase = inputs.get("ignore_case", False)

    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "-n", "--max-count", "50"]
        if icase: cmd.append("-i")
        if glob:  cmd.extend(["--glob", glob])
        cmd.extend([pat, root])
    else:
        grep = shutil.which("grep")
        if not grep:
            return {"status": "error", "error": "ripgrep/grep not available"}
        cmd = [grep, "-rn"]
        if icase: cmd.append("-i")
        if glob:  cmd.extend(["--include", glob])
        cmd.extend([pat, root])

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        lines = r.stdout.splitlines()
        return {
            "status":  "success",
            "count":   len(lines),
            "results": lines[:limit],
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "Search timed out"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
