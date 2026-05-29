import re, sqlite3, ast, json
from pathlib import Path

DB_PATH    = Path.home() / "selyrioncode.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
LLM_MODEL  = "qwen2.5:14b"

_CODE_FENCE = re.compile(r'```(?:python|py|bash|sh)?\n?(.*?)```', re.DOTALL | re.IGNORECASE)

# ── Code extraction from prose ─────────────────────────────────────────────────

def _extract_code(text: str) -> str | None:
    try:
        ast.parse(text)
        return text
    except SyntaxError:
        pass
    m = _CODE_FENCE.search(text)
    if m:
        candidate = m.group(1).strip()
        if len(candidate) >= 10:
            return candidate
    return None

# ── Heuristic fixers ───────────────────────────────────────────────────────────

def fix_indentation(code: str) -> str:
    """Re-indent: detect function/class headers and indent their bodies."""
    lines  = code.splitlines()
    result = []
    expect_indent = False
    current_indent = 0
    BLOCK_KEYWORDS = re.compile(r'^\s*(def |class |if |else:|elif |for |while |try:|except|with |finally:)')

    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            result.append("")
            continue
        actual = len(line) - len(stripped)
        if expect_indent and actual == 0:
            # Body line with no indent — force one level in
            result.append("    " * (current_indent // 4 + 1) + stripped)
        else:
            snapped = round(actual / 4) * 4
            result.append("    " * (snapped // 4) + stripped)
            current_indent = snapped
        expect_indent = bool(BLOCK_KEYWORDS.match(line)) and stripped.endswith(":")
    return "\n".join(result)


def fix_unclosed_parens(code: str) -> str:
    """Balance brackets by inserting close chars at the line where they opened."""
    lines = code.splitlines()
    stack = []  # (char, line_idx)
    OPEN  = {"(": ")", "[": "]", "{": "}"}
    CLOSE = set(OPEN.values())
    in_str, str_char = False, None

    for li, line in enumerate(lines):
        i = 0
        while i < len(line):
            ch = line[i]
            if in_str:
                if ch == "\\" :
                    i += 2; continue
                if ch == str_char:
                    in_str = False
            else:
                if ch in ('"', "'"):
                    in_str = True; str_char = ch
                elif ch in OPEN:
                    stack.append((OPEN[ch], li))
                elif ch in CLOSE and stack and stack[-1][0] == ch:
                    stack.pop()
            i += 1

    # Insert closing chars at end of the line where they were opened
    insertions: dict[int, str] = {}
    for close_char, li in stack:
        insertions[li] = insertions.get(li, "") + close_char

    for li, chars in insertions.items():
        lines[li] = lines[li].rstrip() + chars

    return "\n".join(lines)


def fix_unterminated_string(code: str, lineno: int | None) -> str:
    if lineno is None:
        return code
    lines = code.splitlines()
    idx   = lineno - 1
    if 0 <= idx < len(lines):
        line = lines[idx]
        for q in ('"', "'"):
            if line.count(q) % 2 == 1:
                lines[idx] = line + q
                break
    return "\n".join(lines)


def fix_missing_module(stderr: str, code: str) -> str:
    m = re.search(r"No module named '([^']+)'", stderr)
    if m:
        module = m.group(1).split(".")[0]  # top-level package only
        pip = (f"import subprocess, sys\n"
               f"subprocess.check_call([sys.executable, '-m', 'pip', 'install', "
               f"'{module}', '--break-system-packages', '-q'])\n\n")
        if pip not in code:
            return pip + code
    return code


# ── Template-driven fix lookup ─────────────────────────────────────────────────

def template_fix(code: str, stderr: str, error_class: str,
                 subtype: str) -> tuple[str, str] | None:
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT strategy FROM fix_templates
            WHERE error_class=? AND subtype=?
            ORDER BY success_count DESC, id
        """, (error_class, subtype)).fetchall()
        conn.close()
    except Exception:
        return None

    from .parser import extract_line_number
    lineno = extract_line_number(stderr)

    for (strategy,) in rows:
        fixed = _apply_strategy(code, stderr, strategy, lineno)
        if fixed and fixed != code:
            try:
                c = sqlite3.connect(DB_PATH)
                c.execute("""UPDATE fix_templates SET success_count=success_count+1
                             WHERE strategy=? AND error_class=? AND subtype=?""",
                          (strategy, error_class, subtype))
                c.commit(); c.close()
            except Exception:
                pass
            return fixed, f"template:{strategy}"
    return None


def _apply_strategy(code: str, stderr: str, strategy: str, lineno: int | None) -> str | None:
    if strategy == "normalize_indent":
        return fix_indentation(code)
    if strategy == "dedent_body":
        import textwrap
        return textwrap.dedent(code)
    if strategy in ("balance_parens",):
        return fix_unclosed_parens(code)
    if strategy == "add_pass":
        lines = code.rstrip().splitlines()
        if lines and lines[-1].rstrip().endswith(":"):
            return code.rstrip() + "\n    pass"
        return None
    if strategy == "fix_missing_colon":
        if lineno is None:
            return None
        lines = code.splitlines()
        if 0 < lineno <= len(lines):
            l = lines[lineno - 1]
            kws = ("if ", "else", "elif ", "for ", "while ", "def ", "class ",
                   "try", "except", "with ", "finally")
            if any(l.strip().startswith(k) for k in kws) and not l.rstrip().endswith(":"):
                lines[lineno - 1] = l.rstrip() + ":"
                return "\n".join(lines)
        return None
    if strategy == "close_quote":
        return fix_unterminated_string(code, lineno)
    if strategy in ("pip_install", "pip_install_termux"):
        return fix_missing_module(stderr, code)
    if strategy == "add_import":
        m = re.search(r"NameError: name '(\w+)'", stderr)
        STDLIB = {"os","sys","re","json","time","math","random","hashlib",
                  "pathlib","datetime","collections","itertools","subprocess",
                  "threading","functools","shutil","tempfile","copy"}
        if m and m.group(1) in STDLIB:
            name = m.group(1)
            if f"import {name}" not in code:
                return f"import {name}\n" + code
        return None
    if strategy == "add_none_default":
        m = re.search(r"NameError: name '(\w+)'", stderr)
        if m:
            name = m.group(1)
            if f"{name} = " not in code:
                return f"{name} = None\n" + code
        return None
    if strategy == "to_str":
        # TypeError: can only concatenate str to str — find int/float var used with +
        m = re.search(r"unsupported operand type.*\+.*'(\w+)'", stderr)
        if not m:
            m = re.search(r"can only concatenate str.*not '(\w+)'", stderr)
        if m:
            bad_type = m.group(1)
            # Wrap numeric literals and variables in str() on + operations
            return re.sub(
                r'(\b\w+\b|\d+\.?\d*)\s*\+\s*(\b\w+\b|\d+\.?\d*)',
                lambda x: f'str({x.group(1)}) + str({x.group(2)})',
                code
            )
        return None
    if strategy == "str_to_int":
        m = re.search(r"unsupported operand.*int.*str", stderr)
        if m:
            return re.sub(r'(["\'])(\d+)\1', r'int(\2)', code)
        return None
    if strategy == "safe_cast":
        return re.sub(
            r'(\w+)\s*=\s*int\(([^)]+)\)',
            r'try:\n    \1 = int(\2)\nexcept (ValueError, TypeError):\n    \1 = 0',
            code
        )
    if strategy == "dict_get":
        return re.sub(r'(\w+)\[([\'"][\w\s]+[\'"])\]', r'\1.get(\2, None)', code)
    if strategy == "safe_json":
        if "json.loads(" in code and "try:" not in code:
            return ("import json\n" +
                    re.sub(r'(\w+\s*=\s*)json\.loads\(([^)]+)\)',
                           r'\1json.loads(\2) if \2 else {}', code) +
                    "\n# safe parse: returns {} on failure")
        return None
    if strategy == "timeout_retry":
        if "sqlite3.connect(" in code:
            fixed = re.sub(r'sqlite3\.connect\(([^)]+)\)',
                           r'sqlite3.connect(\1, timeout=30)', code)
            if "journal_mode" not in code:
                fixed = fixed.replace("sqlite3.connect(", "conn = sqlite3.connect(", 1)
            return fixed
        return None
    if strategy == "add_memo":
        if "def " in code and "return " in code and "@lru_cache" not in code:
            return "from functools import lru_cache\n@lru_cache(maxsize=None)\n" + code
        return None
    if strategy == "bounds_check":
        return re.sub(r'(\w+)\[(\d+)\]',
                      lambda m: f'({m.group(1)}[{m.group(2)}] if len({m.group(1)}) > {m.group(2)} else None)',
                      code)
    if strategy == "div_guard":
        return re.sub(r'(\w[\w.]*)\s*/\s*(\w[\w.]*)',
                      r'(\1 / \2 if \2 != 0 else 0)', code)
    if strategy == "path_guard":
        if "open(" in code and "Path(" not in code:
            return "from pathlib import Path\n" + code
        return None
    if strategy == "strip_before_parse":
        if "json.loads(" in code:
            return re.sub(r'json\.loads\((\w+)\)',
                          r'json.loads(\1.strip().lstrip("\ufeff"))', code)
        return None
    # Strategies that need LLM — fall through
    return None


# ── LLM fix tier ───────────────────────────────────────────────────────────────

_FIX_PROMPT = """\
You are a code repair engine. Fix ONLY the specific error shown.

ERROR CLASS: {error_class}/{subtype}
STDERR:
{stderr}

{cms_block}BROKEN CODE:
```python
{code}
```

Respond in this exact JSON structure:
{{
  "fixed_code": "<complete fixed code here>",
  "reasoning": "<one sentence: what was wrong and what you changed>"
}}

Rules:
- fixed_code must be complete and runnable
- Make the minimal change needed — do not refactor or add features
- reasoning must be one sentence, specific to this error
- If CMS context is shown above, use it to ground your fix strategy"""


def _validate_fixed(fixed: str, error_class: str) -> bool:
    if not fixed or len(fixed) < 5:
        return False
    if error_class == "syntax":
        try:
            ast.parse(fixed)
        except SyntaxError:
            return False
    return True


def _parse_llm_response(raw: str) -> tuple[str, str] | None:
    """Extract fixed_code and reasoning from LLM JSON response."""
    # Try full JSON parse
    m = re.search(r'\{[\s\S]*\}', raw)
    if m:
        try:
            data = json.loads(m.group(0))
            code = data.get("fixed_code", "").strip()
            reason = data.get("reasoning", "").strip()
            if code:
                return code, reason
        except json.JSONDecodeError:
            pass
    # Fallback: extract from code fence + any reasoning text
    fence = _extract_code(raw)
    reason_m = re.search(r'"reasoning"\s*:\s*"([^"]+)"', raw)
    reason = reason_m.group(1) if reason_m else ""
    if fence:
        return fence, reason
    return None


def claude_fix(code: str, stderr: str, error_class: str,
               subtype: str, cms_context: str = "") -> tuple[str, str] | None:
    """Use Claude API to fix code. Returns (fixed_code, reasoning_desc) or None."""
    try:
        import anthropic, os
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        client = anthropic.Anthropic(api_key=api_key)
        cms_block = f"CMS KNOWLEDGE CONTEXT (what Selyrion knows):\n{cms_context}\n\n" if cms_context else ""
        prompt = _FIX_PROMPT.format(
            error_class=error_class, subtype=subtype,
            stderr=stderr[:500], code=code[:2000],
            cms_block=cms_block,
        )
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text
    except Exception:
        return None

    result = _parse_llm_response(raw)
    if not result:
        return None
    fixed, reasoning = result
    if not _validate_fixed(fixed, error_class) or fixed == code:
        return None
    return fixed, f"claude_fix | {reasoning}"


def ollama_fix(code: str, stderr: str, error_class: str,
               subtype: str, cms_context: str = "") -> tuple[str, str] | None:
    """Use local Ollama LLM to fix code. Returns (fixed_code, reasoning_desc) or None."""
    try:
        import requests
        cms_block = f"CMS KNOWLEDGE CONTEXT (what Selyrion knows):\n{cms_context}\n\n" if cms_context else ""
        prompt = _FIX_PROMPT.format(
            error_class=error_class, subtype=subtype,
            stderr=stderr[:500], code=code[:1500],
            cms_block=cms_block,
        )
        r = requests.post(OLLAMA_URL, json={
            "model": LLM_MODEL, "prompt": prompt,
            "stream": False, "options": {"temperature": 0.1}
        }, timeout=60)
        raw = r.json().get("response", "")
    except Exception:
        return None

    result = _parse_llm_response(raw)
    if not result:
        return None
    fixed, reasoning = result
    if not _validate_fixed(fixed, error_class) or fixed == code:
        return None
    return fixed, f"ollama_fix:{LLM_MODEL} | {reasoning}"


def llm_fix(code: str, stderr: str, error_class: str,
            subtype: str, cms_context: str = "") -> tuple[str, str] | None:
    """Try Claude first, fall back to Ollama. CMS context injected into both."""
    return (claude_fix(code, stderr, error_class, subtype, cms_context) or
            ollama_fix(code, stderr, error_class, subtype, cms_context))


# ── Main fix dispatcher ────────────────────────────────────────────────────────

def apply(code: str, stderr: str, error_class: str, subtype: str,
          cms_context: str = "") -> tuple[str, str]:
    from .parser import extract_line_number
    lineno = extract_line_number(stderr)

    # Tier 1: Curated templates
    tpl = template_fix(code, stderr, error_class, subtype)
    if tpl:
        return tpl

    # Tier 2: Heuristic fallbacks
    if subtype == "indentation":
        fixed = fix_indentation(code)
        if fixed != code:
            return fixed, "heuristic:fix_indentation"
    if subtype in ("syntax_error", "unclosed_block"):
        fixed = fix_unclosed_parens(code)
        if fixed != code:
            return fixed, "heuristic:fix_parens"
    if subtype == "unterminated_string":
        fixed = fix_unterminated_string(code, lineno)
        if fixed != code:
            return fixed, "heuristic:fix_string"
    if subtype == "missing_module":
        fixed = fix_missing_module(stderr, code)
        if fixed != code:
            return fixed, "heuristic:inject_pip_install"

    # Tier 3: LLM fix — CMS context injected here
    llm = llm_fix(code, stderr, error_class, subtype, cms_context=cms_context)
    if llm:
        return llm

    return code, "no_fix_available"
