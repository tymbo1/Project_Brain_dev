import subprocess, hashlib, time
from pathlib import Path
from .env import python_cmd, bash_cmd, tmp_dir

def _detect_lang(code: str, hint: str = "") -> str:
    if hint:
        return hint
    first = code.strip().splitlines()[0] if code.strip() else ""
    if "bash" in first or "sh" in first:
        return "bash"
    if first.startswith("SELECT") or first.startswith("CREATE"):
        return "sql"
    return "python"

def run(code: str, lang: str = "", timeout: int = 15) -> dict:
    lang = _detect_lang(code, lang)
    ext  = {"python": ".py", "bash": ".sh", "sql": ".sql"}.get(lang, ".py")
    fname = tmp_dir() / f"co_{hashlib.md5(code.encode()).hexdigest()[:8]}{ext}"
    fname.write_text(code)

    if lang == "python":
        cmd = [python_cmd(), str(fname)]
    elif lang == "bash":
        cmd = [bash_cmd(), str(fname)]
    else:
        return {"stdout": "", "stderr": "SQL execution not supported yet",
                "returncode": 1, "lang": lang, "elapsed": 0}

    import os
    env = os.environ.copy()
    env.setdefault("MPLBACKEND", "Agg")   # headless matplotlib — no display needed

    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return {
            "stdout":     r.stdout,
            "stderr":     r.stderr,
            "returncode": r.returncode,
            "lang":       lang,
            "elapsed":    round(time.time() - t0, 3),
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Timeout after {timeout}s",
                "returncode": -1, "lang": lang, "elapsed": timeout}
    except Exception as e:
        return {"stdout": "", "stderr": str(e),
                "returncode": -1, "lang": lang, "elapsed": 0}
    finally:
        try: fname.unlink()
        except Exception: pass
