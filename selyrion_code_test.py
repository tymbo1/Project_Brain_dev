#!/usr/bin/env python3
"""selyrion_code_test.py — Selyrion generates code from its own symbolic knowledge.

No LLM. Selyrion retrieves patterns from selyrioncode.db, adapts them to the task,
executes, learns from errors. If it doesn't know, it says so.

Usage:
  python3 selyrion_code_test.py --task "print hello world"
  python3 selyrion_code_test.py --task "count lines in a file"
  python3 selyrion_code_test.py --interactive
"""
import argparse, hashlib, io, json, re, sqlite3, subprocess, sys, time, traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

_AE_TIMEOUT = 2   # seconds per ae.infer call


def _ae_infer(ae, term: str, max_chains: int = 4) -> dict:
    """ae.infer with hard timeout. shutdown(wait=False) abandons the thread — no join block."""
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(ae.infer, term, max_chains)
    try:
        result = fut.result(timeout=_AE_TIMEOUT)
        ex.shutdown(wait=False)
        return result
    except Exception:
        ex.shutdown(wait=False)
        return {"chains": []}

CODE_DB   = Path.home() / "selyrioncode.db"
MAX_TRIES = 6

# Source UIDs of web/gap-search units used in the current synthesis.
# Populated by _synthesize_operation(), consumed + cleared by run_task() on failure.
_PENDING_SOURCE_UIDS: list[str] = []

# ── CMS activation engine (read-only) ─────────────────────────────────────────
_AE = None
def _get_ae():
    global _AE
    if _AE is None:
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from inference.activation_engine import ActivationEngine
            _AE = ActivationEngine()
        except Exception:
            _AE = False  # unavailable
    return _AE if _AE else None


# ── Dependency resolver ───────────────────────────────────────────────────────
# module name → pip package (known mismatches only; same-name packages need no entry)
_MODULE_TO_PIP = {
    "bs4":           "beautifulsoup4",
    "cv2":           "opencv-python",
    "PIL":           "Pillow",
    "sklearn":       "scikit-learn",
    "yaml":          "pyyaml",
    "dotenv":        "python-dotenv",
    "serial":        "pyserial",
    "gi":            "PyGObject",
    "wx":            "wxPython",
    "usb":           "pyusb",
    "magic":         "python-magic",
    "feedparser":    "feedparser",
    "networkx":      "networkx",
    "matplotlib":    "matplotlib",
    "numpy":         "numpy",
    "pandas":        "pandas",
    "scipy":         "scipy",
    "sympy":         "sympy",
    "requests":      "requests",
    "rich":          "rich",
}


def _extract_missing_module(err: str) -> str | None:
    """Extract module name from ModuleNotFoundError."""
    m = re.search(r"No module named ['\"]([^'\"]+)['\"]", err)
    return m.group(1).split(".")[0] if m else None


def _pip_package_for(module: str, db: sqlite3.Connection) -> str | None:
    """
    Resolve pip package name for a missing module.
    1. Known mapping dict
    2. Search selyrioncode.db pip install patterns
    3. CMS concept search for installation hints
    4. Fallback: try module name directly
    """
    # 1. Known mapping
    if module in _MODULE_TO_PIP:
        return _MODULE_TO_PIP[module]

    # 2. Search existing code for pip install <module>
    rows = db.execute("""
        SELECT parsed_code FROM codeunits
        WHERE parsed_code LIKE ? AND status='working'
        LIMIT 10
    """, (f"%pip install%{module}%",)).fetchall()
    for (code,) in rows:
        for line in code.splitlines():
            if "pip install" in line and module in line:
                parts = line.strip().split()
                if "install" in parts:
                    idx = parts.index("install")
                    pkgs = [p for p in parts[idx+1:] if not p.startswith("-")]
                    if pkgs:
                        return pkgs[0]

    # 3. CMS concept search (parallel SSRE — non-blocking)
    try:
        from ssre_multipass import multipass_infer
        result = multipass_infer(module, max_chains=6)
        for chain in result.get("chains", []):
            chain_str = str(chain).lower()
            if "pip" in chain_str or "install" in chain_str or "package" in chain_str:
                m2 = re.search(r"pip install\s+(\S+)", chain_str)
                if m2:
                    return m2.group(1)
    except Exception:
        pass

    # 4. Assume pip name = module name
    return module


def _resolve_and_install(module: str, db: sqlite3.Connection) -> bool:
    """Install a missing module. Returns True if successful."""
    pkg = _pip_package_for(module, db)
    print(f"  Selyrion: missing '{module}' → searching for pip package '{pkg}'")
    print(f"  Installing '{pkg}'...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg, "-q", "--break-system-packages"],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0:
            print(f"  ✓ '{pkg}' installed successfully.")
            return True
        else:
            print(f"  ✗ Install failed: {result.stderr.strip()[:100]}")
            return False
    except Exception as e:
        print(f"  ✗ Install error: {e}")
        return False


def _cms_expand(task_kw: set[str]) -> set[str]:
    """Keyword passthrough — CMS expansion skipped to avoid DB contention."""
    return task_kw


def _id(text: str) -> str:
    return "cu.gen." + hashlib.md5(text[:80].encode()).hexdigest()[:12]


def _keywords(text: str) -> set[str]:
    stop = {"a","the","to","is","in","of","and","or","for","from","with","that","it","an","be"}
    return {w.lower() for w in re.findall(r'\w+', text) if len(w) > 2 and w.lower() not in stop}


def _score(code: str, task_kw: set[str]) -> int:
    code_lower = code.lower()
    return sum(1 for w in task_kw if w in code_lower)


# Source trust weights — unexecuted web/gap units score lower than verified local code
_SOURCE_TRUST = {
    "log_build":          1.0,
    "gap_search_local":   0.85,
    "web_search":         0.55,
    "selyrion_synthesized": 0.75,
    "selyrion_symbolic":  0.9,
}

def _score_candidate(code: str, source: str, task_kw: set[str]) -> float:
    base = _score(code, task_kw)
    trust = _SOURCE_TRUST.get(source, 0.7)
    return base * trust


_HEADLESS_PREFIX = "import os; os.environ.setdefault('MPLBACKEND','Agg')\n"

def _execute(code: str) -> tuple[bool, str, str]:
    out = io.StringIO()
    try:
        full = _HEADLESS_PREFIX + code
        with redirect_stdout(out):
            exec(compile(full, "<selyrion>", "exec"), {})
        return True, out.getvalue(), ""
    except Exception:
        return False, out.getvalue(), traceback.format_exc()


def _classify_error(err: str) -> str:
    e = err.lower()
    for cls in ("NameError","SyntaxError","IndentationError","TypeError",
                "ImportError","AttributeError","ValueError"):
        if cls.lower() in e:
            return cls
    return "RuntimeError"


def _mark_source_untested(db: sqlite3.Connection, source_uid: str):
    """
    Downgrade a web/gap-search source unit to 'untested' after a synthesis
    derived from it fails execution. Prevents the same bad snippet from being
    selected again at full confidence.
    """
    if not source_uid:
        return
    row = db.execute(
        "SELECT source, status FROM codeunits WHERE id=?", (source_uid,)
    ).fetchone()
    if not row:
        return
    src, status = row
    # Only demote unverified external sources — never demote hand-ingested or symbolic units
    if src in ("web_search", "gap_search_local") and status == "working":
        db.execute(
            "UPDATE codeunits SET status='untested', confidence=0.4 WHERE id=?",
            (source_uid,)
        )
        db.commit()
        print(f"    [trust] source {source_uid[:16]}… demoted to 'untested' after synthesis failure")


def _log_broken(db, code, task, error, error_class, source_uid: str = ""):
    uid = _id(code + error[:20])
    db.execute("""
        INSERT OR IGNORE INTO codeunits
        (id,raw_input,parsed_code,error_class,subtype,environment,confidence,source,fix_text,context,created_at,status)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
    """, (uid, task, code, error_class, "generated", "python",
          0.0, "selyrion_symbolic", error[:500], "", time.time(), "broken"))
    db.commit()
    _mark_source_untested(db, source_uid)
    return uid


def _log_working(db, code, task):
    uid = _id(code)
    db.execute("""
        INSERT OR IGNORE INTO codeunits
        (id,raw_input,parsed_code,error_class,subtype,environment,confidence,source,fix_text,context,created_at,status)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
    """, (uid, task, code, "none", "generated", "python",
          0.95, "selyrion_symbolic", "", "", time.time(), "working"))
    db.commit()


def _log_fix_pair(db, broken_id, problem, fixed_code):
    fid = "fp." + hashlib.md5((broken_id + fixed_code[:20]).encode()).hexdigest()[:12]
    db.execute("""
        INSERT OR IGNORE INTO fix_pairs
        (id,unit_id,problem,fix,verified,source,created_at,fix_status)
        VALUES(?,?,?,?,?,?,?,?)
    """, (fid, broken_id, problem, fixed_code, 1, "selyrion_symbolic", time.time(), "verified"))
    db.commit()


def decompose_task(task: str) -> list[str]:
    """Break task into required concept components by keyword matching."""
    task_l = task.lower()
    concepts = []
    checks = [
        (["input","type","enter","ask","user"],          "input"),
        (["print","output","display","show","result"],   "output"),
        (["add","sum","plus","total","accumulate"],       "arithmetic"),
        (["subtract","minus","difference"],               "arithmetic"),
        (["multiply","times","product"],                  "arithmetic"),
        (["divide","division","quotient"],                "arithmetic"),
        (["calculator","calc","compute","evaluate"],      "calculator"),
        (["loop","repeat","for each","iterate","every"], "loop"),
        (["while","until","condition loop"],              "while"),
        (["list","array","collection","items"],           "list"),
        (["dict","dictionary","map","key"],               "dict"),
        (["function","def","define","method"],            "function"),
        (["class","object","oop","instance"],             "class"),
        (["file","read file","write file","save"],        "file"),
        (["sqlite","database","db","query"],              "sqlite"),
        (["json","parse json","load json"],               "json"),
        (["sort","sorted","order","rank"],                "sort"),
        (["random","random number","pick","choice"],      "random"),
        (["math","sqrt","pi","trig","log"],               "math"),
        (["try","except","error","exception"],            "exception"),
        (["string","text","word","sentence"],             "string"),
        (["date","time","datetime","now","today"],        "datetime"),
        # Scientific computing
        (["fourier","fft","frequency","spectrum","transform"], "fourier"),
        (["sine","cosine","wave","signal","waveform"],         "signal"),
        (["eigenvalue","eigenvector"],                          "eigenvalue"),
        (["svd","singular value","decomposition"],              "svd"),
        (["gradient","derivative","slope","differentiate"],    "gradient"),
        (["histogram","distribution","bin","frequency dist"],  "histogram"),
        (["correlation","covariance","pearson","corr"],        "correlation"),
        (["entropy","information","shannon"],                   "entropy"),
        (["regression","fit","curve","polynomial","linear fit"],"regression"),
        (["cumsum","cumulative","running total","rolling"],    "cumsum"),
        (["simulate","simulation","monte carlo","random walk"], "simulation"),
        (["plot","graph","chart","visualize","visualise"],     "plot"),
        (["numpy","ndarray","linspace","arange","zeros","ones"],"numpy"),
        (["pandas","dataframe","csv","series","read csv"],     "pandas"),
        (["scipy","optimize","minimize","integrate","solve"],  "scipy"),
        (["fibonacci spiral","braid spiral","golden angle","tlst spiral"], "fibonacci_spiral"),
        (["fibonacci","fib","fib(","sequence","recurrence"],   "fibonacci"),
        (["factorial","n!","fact("],                           "factorial"),
        (["prime","primes","sieve","is_prime"],                "prime"),
        (["gcd","lcm","greatest common","least common"],       "gcd"),
    ]
    for triggers, concept in checks:
        if any(t in task_l for t in triggers):
            if concept not in concepts:
                concepts.append(concept)
    return concepts or ["output"]


def _fetch_operations(db: sqlite3.Connection) -> list[dict]:
    """Load all operation templates from selyrioncode.db."""
    rows = db.execute("""
        SELECT parsed_code, raw_input, context FROM codeunits
        WHERE subtype='operation' AND status='working'
    """).fetchall()
    ops = []
    for code, desc, ctx_json in rows:
        try:
            ctx = json.loads(ctx_json or "{}")
        except Exception:
            ctx = {}
        ops.append({
            "concept":    ctx.get("concept", ""),
            "produces":   ctx.get("produces"),
            "consumes":   ctx.get("consumes"),
            "desc":       desc or "",
            "code":       code or "",
            "synthesized": ctx.get("source") == "synthesized",
        })
    return ops


def _synthesize_operation(gap_concept: str, task_kw: set, db: sqlite3.Connection) -> dict | None:
    """
    CMS-driven synthesis — no hardcoded templates.

    Pipeline:
    1. Run full SSRE multipass_infer on the gap concept — whole CMS, parallel B||C.
    2. Extract all concept terms from chains → expand search keywords.
    3. Scan chains for known library names (numpy/scipy/pandas/math) → lib hints.
    4. Scan chains for dot-notation function references (np.fft, math.sin) → call hints.
    5. If call hint found: construct template directly from library + function.
    6. Else: search selyrioncode.db with CMS-expanded keywords for actual code.
    7. Extract function-call lines from best DB match, normalise to `data`.
    8. Persist synthesised op for future reuse.

    If CMS and DB both have nothing useful → return None (Selyrion says "I don't know").
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from ssre_multipass import multipass_infer

    g = gap_concept.lower().strip()
    concept_id = f"synth_{re.sub(r'[^a-z0-9]', '_', g)}"

    # ── 1. CMS hint extraction — skipped (multipass creates unbounded threads) ─
    # _CONCEPT_FUNC_SYNONYMS covers all known scientific domains; unknown
    # concepts fall through to gap search rather than waiting on CMS inference.
    print(f"    [synth] CMS search: '{gap_concept}'...")
    chains = []

    # ── 2. Extract concepts + library/function hints from chains ─────────────
    _KNOWN_LIBS = {
        "numpy": "np",  "scipy": "scipy", "pandas": "pd",
        "math":  "math","statistics": "statistics",
        "matplotlib": "plt", "sklearn": "sklearn", "sympy": "sympy",
    }
    # Maps library alias → import statement
    _LIB_IMPORTS = {
        "np":    "import numpy as np",
        "scipy": "import scipy",
        "pd":    "import pandas as pd",
        "math":  "import math",
        "statistics": "import statistics",
        "plt":   "import matplotlib.pyplot as plt",
        "sklearn": "import sklearn",
        "sympy": "import sympy",
    }

    search_kw   = set(_keywords(gap_concept)) | task_kw
    lib_alias   = None   # e.g. "np"
    call_hint   = None   # e.g. "fft.fft" → constructs np.fft.fft(data)

    for chain in chains:
        chain_l = chain.lower()
        # Collect all concept words for DB search expansion
        for part in chain.split(" | "):
            part = part.strip()
            if not part.startswith("strength:"):
                search_kw.update(_keywords(part))

        # Library detection
        if lib_alias is None:
            for lib, alias in _KNOWN_LIBS.items():
                if lib in chain_l:
                    lib_alias = alias
                    break

        # Dot-notation function reference: "numpy.fft" / "np.sin" / "math.log"
        if call_hint is None:
            m = re.search(r'\b(?:numpy|np|scipy|math|pd|pandas)\.([\w.]+)', chain_l)
            if m:
                lib_alias = lib_alias or ("np" if "numpy" in chain_l or "np." in chain_l else lib_alias)
                call_hint = m.group(1)   # e.g. "fft.fft" or "sin"

    # ── 3. Construct template directly from CMS hints if confident ────────────
    if call_hint and lib_alias:
        imp    = _LIB_IMPORTS.get(lib_alias, f"import {lib_alias}")
        call   = f"{lib_alias}.{call_hint}"
        # Infer: if call ends in known terminal functions, make it a terminal
        _TERMINAL_CALLS = {"show", "savefig", "plot", "bar", "hist", "scatter"}
        is_terminal = any(call.endswith(t) for t in _TERMINAL_CALLS)
        if is_terminal:
            body, produces = f"{call}(data)", None
        else:
            body, produces = f"data = {call}(data)", "data"
        synth_code = f"{imp}\n{body}"
        print(f"    [synth] CMS hint → {call}(data)  libs={lib_alias}")
        op = {"concept": concept_id, "produces": produces,
              "consumes": "data", "desc": f"synthesized: {gap_concept}",
              "code": synth_code, "synthesized": True}
        _persist_synth(db, op, gap_concept)
        return op

    # ── 4. DB search with CMS-expanded keywords ───────────────────────────────
    # Concept → known function pattern synonyms (for when concept name ≠ function name)
    _CONCEPT_FUNC_SYNONYMS = {
        "fourier":     [r'np\.fft\.', r'scipy\.fft\.'],
        "fft":         [r'np\.fft\.', r'scipy\.fft\.'],
        "signal":      [r'scipy\.signal\.', r'np\.sin\b', r'np\.cos\b'],
        "sine":        [r'np\.sin\b', r'np\.linspace\b'],
        "cosine":      [r'np\.cos\b'],
        "wave":        [r'np\.sin\b', r'np\.cos\b', r'np\.linspace\b'],
        "matrix":      [r'np\.linalg\.', r'scipy\.linalg\.'],
        "eigenvalue":  [r'np\.linalg\.eig', r'scipy\.linalg\.eig', r'linalg\.eig', r'LA\.eig'],
        "svd":         [r'np\.linalg\.svd', r'linalg\.svd', r'LA\.svd'],
        "convolution": [r'np\.convolve', r'scipy\.signal\.convolve'],
        "correlation": [r'np\.correlate', r'np\.corrcoef'],
        "entropy":     [r'scipy\.stats\.entropy', r'from scipy\.stats import entropy', r'stats\.entropy\b', r'\bentropy\('],
        "gradient":    [r'np\.gradient'],
        "histogram":   [r'np\.histogram'],
        "cumsum":      [r'np\.cumsum'],
        "integrate":   [r'scipy\.integrate\.', r'np\.trapz'],
        "optimize":    [r'scipy\.optimize\.'],
        "cluster":     [r'sklearn\.cluster\.', r'scipy\.cluster\.'],
        "regression":  [r'sklearn\.linear_model\.', r'np\.polyfit'],
    }

    primary_pats = _CONCEPT_FUNC_SYNONYMS.get(g, [])
    if primary_pats:
        call_pat = re.compile("|".join(primary_pats), re.I)
    else:
        call_pat = re.compile(
            r'(?:np\.|scipy\.|pd\.|math\.|stats\.)?' +
            re.escape(g.replace(" ", "_")) + r'\s*\(', re.I)

    # Also add known synonyms as extra search terms
    _CONCEPT_SEARCH_SYNONYMS = {
        "fourier": ["fft", "frequency"],
        "fft":     ["fourier", "fft"],
        "signal":  ["sin", "cos", "linspace"],
        "sine":    ["sin", "linspace"],
        "cosine":  ["cos"],
        "wave":    ["sin", "cos", "linspace"],
        "eigenvalue": ["eig", "linalg"],
        "entropy":    ["scipy.stats", "entr", "log"],
    }
    # Synonyms go first — they're most likely to hit the right code domain
    synonyms = _CONCEPT_SEARCH_SYNONYMS.get(g, [])
    for extra in synonyms:
        search_kw.add(extra)

    # Search DB: synonyms first (guaranteed), then sorted remainder
    remaining = sorted(search_kw - set(synonyms) - {g})[:6]
    all_terms = [g] + synonyms + remaining
    # candidates: list of (id, source, parsed_code)
    candidates = []
    _CANDIDATE_SQL = """
        SELECT id, source, parsed_code FROM codeunits
        WHERE status='working' AND environment='python'
        AND parsed_code LIKE ?
        AND LENGTH(parsed_code) < 2000
        LIMIT 8
    """

    def _fetch_candidates(terms):
        found = []
        seen_ids = set()
        for term in terms:
            for uid, src, code in db.execute(_CANDIDATE_SQL, (f"%{term}%",)).fetchall():
                if uid not in seen_ids and code and call_pat.search(code):
                    seen_ids.add(uid)
                    found.append((uid, src, code))
        return found

    candidates = _fetch_candidates(all_terms)

    if not candidates and primary_pats:
        # ── Gap search: local files + web (only for known scientific domains) ──
        try:
            from selyrion_gap_search import search_gap
            found = search_gap(g, primary_pats, synonyms, db, web=True)
            if found:
                candidates = _fetch_candidates(all_terms)
        except Exception as e:
            print(f"    [gap_search] error: {e}")

    if not candidates:
        print(f"    [synth] no usable code found for '{gap_concept}' — Selyrion doesn't know this yet")
        return None

    best_uid, best_src, best = max(
        candidates, key=lambda t: _score_candidate(t[2], t[1], search_kw)
    )
    trust = _SOURCE_TRUST.get(best_src, 0.7)
    print(f"    [synth] source={best_src!r} trust={trust:.2f}")

    import_lines, body_lines, const_lines = [], [], []
    # Physics/simulation variable names that signal complex undefined context
    _COMPLEX_VARS = re.compile(
        r'\b(noise_amplitude|noise_frequency|num_points|theta|phi|epsilon|kappa|'
        r'cycle_radius|loop|rho|radius|n_steps|T_total|spiral|helix|torus|braid)\b')

    all_lines = best.splitlines()
    for line in all_lines:
        ls = line.strip()
        if not ls or ls.startswith("#"):
            continue
        if ls.startswith("import ") or ls.startswith("from "):
            import_lines.append(ls)
        elif re.match(r'^[a-z_]\w*\s*=\s*[\d.]+\s*$', ls):
            # Simple numeric constant (e.g. sample_rate = 1000) — capture for later
            const_lines.append(ls)
        elif call_pat.search(ls):
            if _COMPLEX_VARS.search(ls):
                continue   # skip lines with physics-context undefined vars
            # Normalize variable names but NOT method names (don't touch np.array → np.data)
            ls = re.sub(r'(?<!\.)\b(arr|nums|values|result|vec|fft_result|spectrum|waveform|series)\b', 'data', ls)
            if not ls.startswith(("def ", "class ", "return ")):
                body_lines.append(ls)

    if not body_lines:
        return None

    # Deduplicate body lines keeping only first occurrence of each function name
    seen_func = set()
    seen_bl   = set()
    body_lines_dedup = []
    for bl in body_lines:
        key = re.sub(r'\s+', ' ', bl.strip())
        if key in seen_bl:
            continue
        seen_bl.add(key)
        # Extract the function name to deduplicate by call (not just exact text)
        func_match = call_pat.search(bl)
        func_key = func_match.group(0) if func_match else key
        if func_key in seen_func:
            continue
        seen_func.add(func_key)
        body_lines_dedup.append(bl)

    # If no line already uses 'data', replace call arguments with data
    if not any('data' in bl for bl in body_lines_dedup):
        adapted = []
        for bl in body_lines_dedup:
            # Greedy: replace from opening ( to end of line → func(data)
            bl = re.sub(r'(\b(?:LA|np|scipy)\.\w+(?:\.\w+)*\s*\().+$', r'\1data)', bl)
            adapted.append(bl)
        body_lines_dedup = adapted

    # Sort: lines with 'data' first
    body_lines_dedup.sort(key=lambda l: (0 if 'data' in l else 1))
    body_lines = body_lines_dedup[:2]

    # Prepend any numeric constants referenced by body lines
    body_names = set(re.findall(r'\b[a-z_]\w*\b', " ".join(body_lines)))
    needed_consts = [c for c in const_lines if c.split("=")[0].strip() in body_names]
    body_lines = needed_consts + body_lines

    # Inject 2D reshape preamble for linalg concepts that require a matrix input
    _LINALG_CONCEPTS = {"eigenvalue", "svd", "matrix", "determinant", "inverse"}
    if gap_concept in _LINALG_CONCEPTS:
        if "import numpy as np" not in import_lines:
            import_lines = ["import numpy as np"] + import_lines
        body_lines = [
            "data = np.array(data).flatten()",
            "n = max(2, int(round(data.size**0.5)))",
            "data = np.pad(data, (0, n*n - data.size)) if data.size < n*n else data[:n*n]",
            "data = data.reshape(n, n)",
        ] + body_lines

    has_print  = any("print" in l for l in body_lines)
    has_assign = any(l.startswith("data") for l in body_lines)
    produces   = None if (has_print and not has_assign) else "data"

    synth_code = "\n".join(import_lines + body_lines)
    print(f"    [synth] DB match → {len(body_lines)} lines from code memory")
    op = {"concept": concept_id, "produces": produces,
          "consumes": "data", "desc": f"synthesized: {gap_concept}",
          "code": synth_code, "synthesized": True,
          "source_uid": best_uid, "source_trust": trust}
    # Register source uid for trust propagation on failure
    if best_src in ("web_search", "gap_search_local"):
        _PENDING_SOURCE_UIDS.append(best_uid)
    _persist_synth(db, op, gap_concept)
    return op


def _persist_synth(db: sqlite3.Connection, op: dict, gap_concept: str):
    """Upsert a synthesized op into codeunits (replaces stale prior version)."""
    concept_id = op["concept"]
    uid  = "cu.op." + hashlib.md5(concept_id.encode()).hexdigest()[:12]
    ctx  = json.dumps({"concept": concept_id, "produces": op["produces"],
                       "consumes": "data", "source": "synthesized",
                       "gap_concept": gap_concept})
    db.execute("""
        INSERT INTO codeunits
        (id,raw_input,parsed_code,error_class,subtype,environment,
         confidence,source,fix_text,context,created_at,status)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            parsed_code=excluded.parsed_code,
            raw_input=excluded.raw_input,
            context=excluded.context
    """, (uid, op["desc"], op["code"], concept_id, "operation", "python",
          0.7, "selyrion_synthesized", "", ctx, time.time(), "working"))
    db.commit()


def _detect_gaps(task_kw: set, pipeline: list, task_l: str) -> list[str]:
    """
    Identify concepts present in the task that have no coverage in the pipeline.
    Returns list of gap concept strings to synthesize.
    Collapses mean+std → print_stats (single terminal) to avoid scalar type errors.
    """
    TRACKABLE = {
        "mean":        ["mean"],
        "std":         ["std", "standard deviation", "deviation"],
        "median":      ["median"],
        "correlation": ["corr", "correlation"],
        "regression":  ["regression"],
        "histogram":   ["histogram", "hist"],
        "plot":        ["plot", "chart", "graph", "visuali"],
        "sort":        ["sort"],
        "filter":      ["filter"],
        "numpy":       ["numpy", "np."],
        "pandas":      ["pandas", "dataframe", "pd."],
        "scipy":       ["scipy"],
        "gradient":    ["gradient", "deriv"],
        "fourier":     ["fourier", "fft"],
        "entropy":     ["entropy"],
        "matrix":      ["matrix", "dot product", "matmul"],
    }

    pipeline_code = " ".join(op.get("code","") + op.get("concept","") for op in pipeline).lower()
    raw_gaps = []
    for concept, triggers in TRACKABLE.items():
        requested = any(t in task_l for t in triggers)
        covered   = any(t in pipeline_code for t in triggers)
        if requested and not covered:
            raw_gaps.append(concept)

    # Check if mean and std are BOTH requested (one may already be in pipeline as synth_mean)
    mean_requested = any(t in task_l for t in ["mean", "average"])
    std_requested  = any(t in task_l for t in ["std", "standard deviation", "deviation"])
    pipeline_concepts = {op.get("concept","") for op in pipeline}
    synth_mean_in_pipe = "synth_mean" in pipeline_concepts
    synth_std_in_pipe  = "synth_std"  in pipeline_concepts

    # If both mean+std requested and either is a scalar-collapsing synth transformer,
    # replace with a combined print_stats terminal
    if mean_requested and std_requested and (synth_mean_in_pipe or synth_std_in_pipe):
        gaps = [g for g in raw_gaps if g not in ("mean", "std")]
        if "print_stats" not in pipeline_code:
            gaps.append("print_stats")
        return gaps

    # Plain collapse: both detected as raw gaps
    has_mean = "mean" in raw_gaps
    has_std  = "std"  in raw_gaps
    if has_mean and has_std:
        gaps = [g for g in raw_gaps if g not in ("mean", "std")]
        if "print_stats" not in pipeline_code:
            gaps.append("print_stats")
        return gaps
    return raw_gaps


def compose(task: str, db: sqlite3.Connection) -> str | None:
    """
    Data-flow composition: match task to ordered operation pipeline,
    wire each step through a shared `data` variable.
    """
    import json as _json
    task_kw  = _keywords(task)
    task_kw  = _cms_expand(task_kw)   # enrich with CMS domain knowledge
    task_l   = task.lower()
    all_ops  = _fetch_operations(db)

    if not all_ops:
        return None

    # Score each operation against the task — require at least 2 keyword hits
    scored = []
    for op in all_ops:
        s = _score(op["desc"] + " " + op["concept"] + " " + op["code"], task_kw)
        # Synthesized ops: if their gap concept appears in task keywords, guarantee inclusion
        if s < 2 and op.get("synthesized"):
            gap = op.get("concept", "").replace("synth_", "")
            if gap and any(gap in kw or kw in gap for kw in task_kw):
                s = 3   # strong match — synthesized specifically for this concept
        if s >= 2:
            scored.append((s, op))
    scored.sort(key=lambda x: -x[0])

    if not scored:
        # Cold start: no existing ops match this task.
        # Synthesize from the decomposed concepts directly via CMS, then build from those.
        print(f"  No existing ops match task — attempting CMS cold-start synthesis...")
        cold_ops = []
        for concept in decompose_task(task):
            synth = _synthesize_operation(concept, task_kw, db)
            if synth:
                cold_ops.append((3, synth))
                all_ops.append(synth)   # make it available for producer/transformer/terminal logic
        if not cold_ops:
            return None
        scored = cold_ops

    # Build pipeline: seed → transform(s) → output
    # Rules: one producer (consumes=None), chain of transformers (consumes=data),
    #        one or more terminals (produces=None)
    # Standalone ops (consumes=None AND produces=None) are self-contained algorithms.
    standalones  = [(s,o) for s,o in scored if o["consumes"] is None and o["produces"] is None]
    producers    = [(s,o) for s,o in scored if o["consumes"] is None and o["produces"] == "data"]
    transformers = [(s,o) for s,o in scored if o["consumes"] == "data" and o["produces"] == "data"]
    terminals    = [(s,o) for s,o in scored if o["produces"] is None and o["consumes"] is not None]

    # Standalone algorithm: if best standalone beats all producers, use it directly
    if standalones:
        best_standalone_score = standalones[0][0]
        best_producer_score   = producers[0][0] if producers else 0
        if best_standalone_score >= best_producer_score:
            standalone_op = standalones[0][1]
            print(f"  Standalone algorithm: {standalone_op['concept']}")
            return standalone_op["code"]

    pipeline = []

    # Detect whether task is scalar or list context
    wants_list   = any(w in task_l for w in ("list","numbers","each","every","array","items","dataframe","dataset"))
    wants_scalar = any(w in task_l for w in ("a number","single","one number","the number")) and not wants_list

    def _producer_score(op):
        base = _score(op["desc"] + " " + op["concept"], task_kw)
        is_list_producer = "list" in op["concept"]
        # Bias: scalar task → prefer non-list producers, list task → prefer list producers
        if wants_scalar and not wants_list:
            base += 0 if is_list_producer else 2
        elif wants_list and not wants_scalar:
            base += 2 if is_list_producer else 0
        return base

    all_producers = [(_producer_score(o), o)
                     for o in all_ops
                     if o["consumes"] in (None, "input") and o["produces"] == "data"]
    all_producers.sort(key=lambda x: -x[0])

    if all_producers:
        pipeline.append(all_producers[0][1])
    else:
        default = next((o for o in all_ops if o["concept"] == "create_list"), None)
        if default:
            pipeline.append(default)

    # Mutual exclusion groups — never add both sides
    MUTEX = [{"filter_even", "filter_odd"}, {"sort_asc", "sort_desc"},
             {"print_indexed", "print_each", "print_result", "join_string"}]

    def _mutex_ok(concept, chosen):
        for group in MUTEX:
            if concept in group and any(c in group for c in chosen):
                return False
        return True

    # Add up to 3 best transformers (no duplicates, no mutex conflicts)
    seen_concepts = {pipeline[0]["concept"]} if pipeline else set()
    for _, op in transformers[:8]:
        if (op["concept"] not in seen_concepts
                and _mutex_ok(op["concept"], seen_concepts)
                and len(pipeline) < 5):
            pipeline.append(op)
            seen_concepts.add(op["concept"])

    # Add terminal — scalar context forces print_result, list context scores normally
    if wants_scalar and not wants_list:
        scalar_op = next((o for o in all_ops if o["concept"] == "print_result"), None)
        if scalar_op and scalar_op["concept"] not in seen_concepts:
            pipeline.append(scalar_op)
            seen_concepts.add(scalar_op["concept"])
    else:
        list_preferred = ["print_indexed","print_each","sum_data","count_items","max_min","average"]
        added = False
        for _, op in terminals:
            if op["concept"] in list_preferred and op["concept"] not in seen_concepts:
                pipeline.append(op)
                seen_concepts.add(op["concept"])
                added = True
                break
        if not added:
            for _, op in terminals[:1]:
                if op["concept"] not in seen_concepts:
                    pipeline.append(op)
                    seen_concepts.add(op["concept"])

    if len(pipeline) < 2:
        return None

    # Stats pipeline cleanup: if pipeline contains a stats terminal (numpy_stats / print_stats),
    # purge any transformers that corrupt data before stats (filter/dict/reshape).
    _STATS_TERMINAL_CONCEPTS = {"numpy_stats", "synth_print_stats", "numpy_mean", "numpy_std"}
    _STATS_UNSAFE = {
        "numpy_reshape", "synth_mean", "synth_std",
        "filter_even", "filter_odd", "filter_positive", "filter_negative",
        "dict_from_list", "print_indexed", "print_each", "print_result",
        "join_string", "factorial", "sqrt", "eval_expr",
    }
    if any(op["concept"] in _STATS_TERMINAL_CONCEPTS for op in pipeline):
        producer = pipeline[0]
        safe_middle = [op for op in pipeline[1:-1] if op["concept"] not in _STATS_UNSAFE]
        terminal = pipeline[-1]
        pipeline = [producer] + safe_middle + [terminal]

    # Gap synthesis: detect concepts in task not covered by current pipeline,
    # synthesize operation templates from code memory, insert before terminal
    gaps = _detect_gaps(task_kw, pipeline, task_l)
    if gaps:
        print(f"  Gaps detected: {gaps}")
        # If a stats terminal (print_stats) will be synthesized, remove shape-mangling
        # transformers (reshape) that would collapse the array before stats computation
        stats_terminal_coming = "print_stats" in gaps
        if stats_terminal_coming:
            # Keep only the producer and numpy-safe transformers; remove anything that
            # changes list→dict/scalar or filters elements before stats computation
            _STATS_UNSAFE = {
                "numpy_reshape", "synth_mean", "synth_std",
                "filter_even", "filter_odd", "filter_positive", "filter_negative",
                "dict_from_list", "print_indexed", "print_each", "print_result",
                "join_string", "factorial", "sqrt", "eval_expr",
                "pandas_dataframe", "pandas_read_csv",
            }
            producer = pipeline[0] if pipeline else None
            numpy_ops = [op for op in pipeline[1:] if op["concept"] not in _STATS_UNSAFE
                         and op["concept"].startswith("numpy")]
            pipeline = ([producer] if producer else []) + numpy_ops

        inserted = 0
        for gap in gaps[:3]:
            # print_stats is a composite terminal — construct directly, don't CMS-search
            if gap == "print_stats":
                synth = {
                    "concept": "synth_print_stats", "produces": None,
                    "consumes": "data", "synthesized": True,
                    "desc": "synthesized: print_stats",
                    "code": 'import numpy as np\nprint(f"Mean: {np.mean(data):.4f}  Std: {np.std(data):.4f}")',
                }
                _persist_synth(db, synth, "print_stats")
            else:
                synth = _synthesize_operation(gap, task_kw, db)
            if synth:
                print(f"  → Synthesized op for gap '{gap}': {synth['concept']}")
                # Stats terminal replaces existing terminal; others insert before it
                if synth["produces"] is None and len(pipeline) >= 1:
                    # Remove existing terminal if it's a list-printer (would fail on scalars)
                    pipeline = [op for op in pipeline
                                if op["concept"] not in
                                ("print_indexed","print_each","average","sum_data","max_min")]
                    pipeline.append(synth)
                elif len(pipeline) >= 2:
                    pipeline.insert(-1, synth)
                else:
                    pipeline.append(synth)
                inserted += 1
        if inserted:
            print(f"  Pipeline after synthesis ({inserted} ops added):")

    print(f"  Pipeline: {' → '.join(o['concept'] for o in pipeline)}")

    # Stitch: collect imports, then operations in order
    imports = []
    body    = []
    for op in pipeline:
        for line in op["code"].splitlines():
            ls = line.strip()
            if ls.startswith("import ") or ls.startswith("from "):
                if ls not in imports:
                    imports.append(ls)
            else:
                body.append(line)

    lines = imports + ([""] if imports else []) + body
    return "\n".join(lines).strip()


def retrieve(task: str, db: sqlite3.Connection, limit: int = 5) -> list[str]:
    """Retrieve best-matching working code units for the task."""
    task_kw = _cms_expand(_keywords(task))
    rows = db.execute("""
        SELECT parsed_code, raw_input FROM codeunits
        WHERE status='working' AND environment='python' AND LENGTH(parsed_code) < 400
    """).fetchall()

    scored = []
    for code, raw_input in rows:
        combined = (code or "") + " " + (raw_input or "")
        s = _score(combined, task_kw)
        if s > 0:
            scored.append((s, code))

    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:limit]]


def extract_snippet(candidates: list[str], task: str) -> str | None:
    """
    Return the best-matching self-contained code block.
    Prefers short complete scripts over fragments pulled from inside functions.
    """
    task_kw = _keywords(task)
    best_code = None
    best_score = 0

    for code in candidates:
        # Prefer whole units that are short and self-contained (no leading indent)
        lines = [l for l in code.splitlines() if l.strip()]
        if not lines:
            continue
        # Reject if first meaningful line is indented (mid-function fragment)
        if lines[0] and lines[0][0] == " ":
            continue
        s = _score(code, task_kw)
        if s > best_score:
            best_score = s
            best_code = code

    # Fallback: try any candidate, strip indented-only lines
    if not best_code:
        for code in candidates:
            clean = "\n".join(l for l in code.splitlines() if l and not l[0] == " ")
            if clean.strip():
                best_code = clean
                break

    return best_code


def adapt(snippet: str, task: str) -> str:
    """
    Apply simple adaptations to make the snippet match the task.
    e.g. swap string literals if task specifies different text.
    """
    # If task contains a quoted string, replace first string literal in snippet
    quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', task)
    if quoted:
        target = quoted[0][0] or quoted[0][1]
        snippet = re.sub(r'(?<=["\'])([^"\']+)(?=["\'])', target, snippet, count=1)

    # If task says "from Selyrion" or similar identity, inject it
    if "selyrion" in task.lower() and "Selyrion" not in snippet:
        snippet = re.sub(r'print\((["\'])(.+?)\1\)',
                         r'print(\1\2 from Selyrion\1)', snippet, count=1)
    return snippet


def fix_with_known_pairs(db, code: str, error: str, error_class: str) -> str | None:
    """Look up fix_pairs for this error class and try to apply the fix strategy."""
    rows = db.execute("""
        SELECT fp.problem, fp.fix FROM fix_pairs fp
        JOIN codeunits cu ON fp.unit_id = cu.id
        WHERE cu.error_class = ? AND fp.fix_status = 'verified'
        LIMIT 3
    """, (error_class,)).fetchall()

    if not rows:
        return None

    # Simple heuristic fixes based on known pair strategies
    fixed = code
    for problem, fix_desc in rows:
        if "fence" in (fix_desc or "").lower() or "markdown" in (fix_desc or "").lower():
            fixed = re.sub(r"```[a-z]*\n?|```", "", fixed).strip()
        if "indent" in (fix_desc or "").lower():
            fixed = "\n".join(l.expandtabs(4) for l in fixed.splitlines())
        if "import" in (fix_desc or "").lower():
            pass  # Can't blindly add imports without knowing what's missing

    return fixed if fixed != code else None


def run_task(task: str) -> dict:
    db = sqlite3.connect(str(CODE_DB))
    print(f"\n  Selyrion retrieving patterns for: {task!r}")

    # Try composition first (multi-concept tasks)
    concepts = decompose_task(task)
    compose_attempted = False
    if len(concepts) > 1:
        compose_attempted = True
        composed = compose(task, db)
        if composed:
            print(f"  Composing from {len(concepts)} concept(s)...")
            code = adapt(composed, task)
        else:
            code = None
    else:
        code = None

    # If composition was attempted but failed (multi-concept task with no known synthesis),
    # don't fall back to random retrieval — it will produce garbage code.
    if compose_attempted and not code:
        print("  Selyrion: I couldn't synthesize this yet — missing knowledge for one or more concepts.")
        db.close()
        return {"success": False, "reason": "synthesis_failed"}

    # Fallback: single best-match retrieval (single-concept tasks only)
    if not code:
        candidates = retrieve(task, db)
        if not candidates:
            print("  Selyrion: I don't have a pattern for this yet.")
            db.close()
            return {"success": False, "reason": "no_pattern"}
        print(f"  Found {len(candidates)} matching patterns.")
        snippet = extract_snippet(candidates, task)
        if not snippet:
            print("  Selyrion: Patterns found but couldn't extract a relevant snippet.")
            db.close()
            return {"success": False, "reason": "no_snippet"}
        code = adapt(snippet, task)
    broken_id = None
    error = ""
    result = {"success": False, "tries": 0, "output": "", "final_code": ""}
    # Snapshot pending source uids at task start; clear global so next task starts fresh
    task_source_uids = list(_PENDING_SOURCE_UIDS)
    _PENDING_SOURCE_UIDS.clear()

    for attempt in range(1, MAX_TRIES + 1):
        print(f"\n  [Attempt {attempt}] Code:\n  {code.replace(chr(10), chr(10)+'  ')}")
        success, stdout, err_msg = _execute(code)
        result["tries"] = attempt

        if success:
            print(f"  Output: {stdout.strip()}")
            print(f"  ✓ Selyrion succeeded on attempt {attempt}.")
            _log_working(db, code, task)
            if broken_id:
                _log_fix_pair(db, broken_id, error[:200], code)
                print("  → Fix pair learned and stored.")
            result.update({"success": True, "output": stdout.strip(), "final_code": code})
            break
        else:
            ec = _classify_error(err_msg)
            last = err_msg.strip().splitlines()[-1]
            print(f"  ✗ Error ({ec}): {last}")

            # Dependency resolution — attempt before logging as broken
            if "ModuleNotFoundError" in err_msg or "ImportError" in err_msg:
                missing = _extract_missing_module(err_msg)
                if missing:
                    installed = _resolve_and_install(missing, db)
                    if installed:
                        print(f"  → Retrying after install...")
                        continue   # retry same code, don't increment attempt meaningfully

            broken_id = _log_broken(db, code, task, err_msg, ec)
            print("  → Broken unit logged. Checking known fixes...")
            error = err_msg

            fixed = fix_with_known_pairs(db, code, err_msg, ec)
            if fixed:
                code = fixed
                print("  → Applied known fix strategy.")
            else:
                print("  → No fix pattern known. Selyrion needs to learn this.")
                break

    if not result["success"]:
        # Propagate failure back to web/gap-search source units
        for src_uid in task_source_uids:
            _mark_source_untested(db, src_uid)
        print(f"\n  Selyrion: I couldn't solve this yet. Error logged for learning.")

    db.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task",        default="print Hello from Selyrion")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()

    if args.interactive:
        print("  Selyrion Code — Symbolic Mode  (type 'quit' to exit)\n")
        while True:
            task = input("  Task> ").strip()
            if task.lower() in ("quit", "exit", "q"):
                break
            if task:
                run_task(task)
    else:
        run_task(args.task)


if __name__ == "__main__":
    main()
