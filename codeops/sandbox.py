import re

BLOCKED_PATTERNS = [
    r'\brm\s+-rf\b',
    r'\bos\.remove\b',
    r'\bos\.unlink\b',
    r'\bshutil\.rmtree\b',
    r'\bsubprocess\.call\b',
    r'\bformat\s+[A-Za-z]:\b',
    r'\bmkfs\b',
    r'\bdd\s+if=',
    r'\b>\s*/dev/sd',
    r'\bchmod\s+777\b',
    r'\beval\s*\(',
    r'\b__import__\s*\(',
]

_compiled = [re.compile(p) for p in BLOCKED_PATTERNS]

def is_safe(code: str) -> tuple[bool, str]:
    for pat in _compiled:
        m = pat.search(code)
        if m:
            return False, f"blocked pattern: {m.group(0)!r}"
    return True, ""


def risks_detected(code: str) -> list[dict]:
    out = []
    for pat in _compiled:
        m = pat.search(code)
        if m:
            out.append({"pattern": pat.pattern, "match": m.group(0)})
    return out
