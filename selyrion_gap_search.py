"""
selyrion_gap_search.py — Gap-triggered search engine for Selyrion CodeOps

When synthesis fails (no usable code in selyrioncode.db), this module:
  Layer 1: Scans all known local storage for matching .py files → ingest
  Layer 2: Web search (DuckDuckGo) for targeted code examples → ingest
  Layer 3: Infer CMS relations from web results (X implements Y) → selyrion_synth.db

Caller: _synthesize_operation() in selyrion_code_test.py
Returns: True if new code was ingested (retry synthesis), False otherwise
"""  # noqa: W605

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────
CODE_DB  = Path.home() / "selyrioncode.db"
SYNTH_DB = Path.home() / "selyrion_synth.db"

# Local directories to scan (excludes venv/pip/cache dirs internally)
_LOCAL_SEARCH_ROOTS = [
    Path.home() / "transfer",
    Path.home() / "phone_files",
    Path.home() / "Documents",
    Path.home() / "termux_scripts",
    Path.home() / "phone_archive",
    Path.home() / "~pi_backup",
    # projectbrain_dev excluded — 13k+ files, circular (Selyrion's own codebase)
]

_MAX_LOCAL_FILES = 500  # cap total files scanned per gap search

_SKIP_DIRS = {
    ".venv", "venv", "env", ".env", "__pycache__", ".git",
    "node_modules", "site-packages", ".cache", "pip", "dist-packages",
}

# ── Internal file scanner ───────────────────────────────────────────────────

def _scan_local(gap_concept: str, call_patterns: list[str]) -> list[Path]:
    """
    Recursively scan local directories for .py files containing
    any of the call_patterns (e.g. r'np.fft.', r'np.sin').
    Returns list of matching file paths (deduplicated).
    """
    if not call_patterns:
        return []

    combined_pat = re.compile("|".join(call_patterns), re.I)
    found = []
    seen_inode = set()   # dedupe by inode (catches hardlinks + overlapping roots)
    seen_path  = set()

    files_checked = 0
    for root in _LOCAL_SEARCH_ROOTS:
        if not root.exists():
            continue
        try:
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for fname in files:
                    if not fname.endswith(".py"):
                        continue
                    if files_checked >= _MAX_LOCAL_FILES:
                        return found
                    fp = Path(dirpath) / fname
                    if fp in seen_path:
                        continue
                    seen_path.add(fp)
                    try:
                        inode = fp.stat().st_ino
                        if inode in seen_inode:
                            continue
                        seen_inode.add(inode)
                        files_checked += 1
                        text = fp.read_text(errors="ignore")
                        if combined_pat.search(text):
                            found.append(fp)
                    except Exception:
                        pass
        except Exception:
            pass

    return found


def _ingest_file(fp: Path, domain: str, db: sqlite3.Connection) -> bool:
    """
    Ingest a single .py file into codeunits. Returns True if new.
    Same logic as selyrioncode_log_build.py.
    """
    try:
        code = fp.read_text(errors="ignore").strip()
        if not code:
            return False
        uid = "cu.gap." + hashlib.md5(str(fp).encode()).hexdigest()[:12]
        ctx = json.dumps({"filename": fp.name, "path": str(fp),
                          "domain": domain, "source": "gap_search_local"})
        db.execute("""
            INSERT INTO codeunits
            (id, raw_input, parsed_code, error_class, subtype, environment,
             confidence, source, fix_text, context, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                parsed_code = excluded.parsed_code,
                context     = excluded.context
        """, (uid, fp.name, code, domain, "build_artifact", "python",
              0.8, "gap_search_local", "", ctx, time.time(), "working"))
        db.commit()
        return True
    except Exception as e:
        print(f"    [gap_search] ingest error {fp.name}: {e}")
        return False


def search_local(gap_concept: str, call_patterns: list[str],
                 db: sqlite3.Connection) -> int:
    """
    Scan local directories, ingest matching files.
    Returns count of newly ingested files.
    """
    files = _scan_local(gap_concept, call_patterns)
    if not files:
        return 0

    domain = f"gap_{re.sub(r'[^a-z0-9]', '_', gap_concept.lower())}"
    ingested = 0
    for fp in files[:10]:   # cap at 10 per gap search
        if _ingest_file(fp, domain, db):
            ingested += 1
            print(f"    [gap_search] ingested local: {fp.name}")

    return ingested


# ── Web search ─────────────────────────────────────────────────────────────

_CODE_FENCE_PAT = re.compile(r'```(?:python)?\s*\n(.*?)```', re.DOTALL | re.I)
_PRE_TAG_PAT    = re.compile(r'<pre[^>]*>(.*?)</pre>', re.DOTALL | re.I)


def _build_query(gap_concept: str, synonyms: list[str]) -> str:
    """
    Build a targeted web query. Prefer numpy.org / scipy docs / github.
    """
    func_hint = synonyms[0] if synonyms else gap_concept
    return (
        f'python numpy "{func_hint}" example '
        f'site:numpy.org OR site:docs.scipy.org OR site:github.com/numpy '
        f'OR site:docs.python.org OR site:stackoverflow.com'
    )


def _strip_doctest(text: str) -> str:
    """Convert '>>> code' doctest format to plain Python."""
    lines = []
    for line in text.splitlines():
        if line.startswith(">>> ") or line.startswith("... "):
            lines.append(line[4:])
        elif line.startswith(">>>"):
            lines.append(line[3:])
        elif lines and not line.startswith(("array(", "array([", "[", "{")):
            # Skip output lines (non-code lines after code)
            if re.match(r'^[A-Za-z\[\({]', line) and "=" not in line:
                continue
            lines.append(line)
    return "\n".join(lines).strip()


def _extract_code_from_html(html: str, call_pat: re.Pattern) -> list[str]:
    """Extract code blocks from HTML that match the call pattern."""
    snippets = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Priority 1: highlight/doctest class divs (numpy.org, scipy.org style)
        for tag in soup.find_all(["pre", "div"], class_=lambda c: c and any(
                k in " ".join(c if isinstance(c, list) else [c])
                for k in ("highlight", "doctest", "example", "code"))):
            text = tag.get_text()
            if call_pat.search(text):
                cleaned = _strip_doctest(text)
                if cleaned and len(cleaned) < 3000:
                    snippets.append(cleaned)

        # Priority 2: all <pre> tags
        if not snippets:
            for pre in soup.find_all("pre"):
                text = pre.get_text()
                if call_pat.search(text):
                    cleaned = _strip_doctest(text)
                    if cleaned and len(cleaned) < 3000:
                        snippets.append(cleaned)

    except Exception:
        # bs4 not available: try raw regex
        for m in _PRE_TAG_PAT.finditer(html):
            block = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if call_pat.search(block) and len(block) < 3000:
                snippets.append(_strip_doctest(block))

    # Also try markdown code fences (StackOverflow, GitHub)
    for m in _CODE_FENCE_PAT.finditer(html):
        block = m.group(1).strip()
        if call_pat.search(block) and len(block) < 3000:
            snippets.append(block)

    return snippets


def _ingest_web_snippet(snippet: str, gap_concept: str, url: str,
                         db: sqlite3.Connection) -> bool:
    """Ingest a web-found code snippet into codeunits."""
    try:
        uid = "cu.web." + hashlib.md5(snippet[:80].encode()).hexdigest()[:12]
        ctx = json.dumps({"source_url": url, "gap_concept": gap_concept,
                          "source": "web_search"})
        db.execute("""
            INSERT INTO codeunits
            (id, raw_input, parsed_code, error_class, subtype, environment,
             confidence, source, fix_text, context, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
        """, (uid, f"web:{gap_concept}", snippet,
              f"web_{gap_concept}", "build_artifact", "python",
              0.7, "web_search", "", ctx, time.time(), "working"))
        db.commit()
        return True
    except Exception as e:
        print(f"    [gap_search] web ingest error: {e}")
        return False


def _extract_cms_relations(snippet: str, gap_concept: str) -> list[tuple]:
    """
    Infer CMS relations from web code snippet.
    e.g. "import numpy as np\nnp.fft.fft(...)" →
         ("numpy.fft", "implements", "fourier_transform")
    Returns list of (subject, predicate, object) triples.
    """
    relations = []
    imports = re.findall(r'import\s+(\w+)(?:\s+as\s+(\w+))?', snippet)
    for mod, alias in imports:
        # "numpy | implements | {gap_concept}"
        if mod in ("numpy", "scipy", "pandas", "math", "sklearn"):
            relations.append((mod, "implements", gap_concept))
            if alias:
                relations.append((alias, "is_alias_for", mod))

    # Dot-notation calls: "np.fft.fft" → "numpy.fft | implements | fourier"
    calls = re.findall(r'\b(np|scipy|pd|math)\.([\w.]+)\s*\(', snippet)
    for lib, func in calls:
        lib_map = {"np": "numpy", "scipy": "scipy", "pd": "pandas", "math": "math"}
        full = f"{lib_map.get(lib, lib)}.{func}"
        relations.append((full, "implements", gap_concept))

    return relations


def _write_cms_relations(relations: list[tuple], gap_concept: str):
    """Write inferred relations to selyrion_synth.db for HITL review."""
    if not relations or not SYNTH_DB.exists():
        return
    try:
        con = sqlite3.connect(str(SYNTH_DB))
        for subj, pred, obj in relations:
            con.execute("""
                INSERT INTO synth_relations
                (subject, predicate, object, confidence, proposed_by, review_status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """, (subj, pred, obj, 0.6, f"gap_search:{gap_concept}", time.time()))
        con.commit()
        con.close()
        print(f"    [gap_search] wrote {len(relations)} CMS relation proposals")
    except Exception as e:
        print(f"    [gap_search] CMS relation write error: {e}")


def search_web(gap_concept: str, call_patterns: list[str], synonyms: list[str],
               db: sqlite3.Connection) -> int:
    """
    DuckDuckGo search for code examples → extract → ingest.
    Returns count of ingested snippets.
    """
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
    except ImportError:
        print("    [gap_search] ddgs/duckduckgo_search not available")
        return 0

    call_pat = re.compile("|".join(call_patterns), re.I) if call_patterns else None
    if not call_pat:
        return 0

    query = _build_query(gap_concept, synonyms)
    print(f"    [gap_search] web query: {query[:80]}...")

    ingested = 0
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
    except Exception as e:
        print(f"    [gap_search] DDG error: {e}")
        return 0

    try:
        import requests
    except ImportError:
        return 0

    for result in results:
        url = result.get("href", "")
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=8,
                                headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                continue
            snippets = _extract_code_from_html(resp.text, call_pat)
            for snippet in snippets[:2]:   # max 2 per URL
                # Infer CMS relations (fire-and-forget, non-blocking)
                relations = _extract_cms_relations(snippet, gap_concept)
                _write_cms_relations(relations, gap_concept)
                if _ingest_web_snippet(snippet, gap_concept, url, db):
                    ingested += 1
                    print(f"    [gap_search] ingested web snippet from {url[:60]}")
            if ingested >= 3:   # cap total web snippets per gap
                break
        except Exception:
            continue

    return ingested


# ── Main entry point ────────────────────────────────────────────────────────

def search_gap(gap_concept: str,
               call_patterns: list[str],
               synonyms: list[str],
               db: sqlite3.Connection,
               web: bool = True) -> bool:
    """
    Full gap search: Layer 1 (local) → Layer 2 (web).
    Returns True if anything was ingested (caller should retry synthesis).
    """
    print(f"    [gap_search] searching for '{gap_concept}' code...")
    total = 0

    # Layer 1: local files
    n = search_local(gap_concept, call_patterns, db)
    total += n
    if n:
        print(f"    [gap_search] Layer 1: {n} local files ingested")

    # Layer 2: web (only if local found nothing, or always if configured)
    if web and total == 0:
        n = search_web(gap_concept, call_patterns, synonyms, db)
        total += n
        if n:
            print(f"    [gap_search] Layer 2: {n} web snippets ingested")

    return total > 0


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Gap search tool")
    ap.add_argument("concept", help="Gap concept (e.g. fourier)")
    ap.add_argument("--patterns", nargs="+", default=[],
                    help="Regex call patterns (e.g. 'np\\.fft\\.')")
    ap.add_argument("--synonyms", nargs="+", default=[],
                    help="Search synonym terms (e.g. fft frequency)")
    ap.add_argument("--local-only", action="store_true")
    ap.add_argument("--web-only",   action="store_true")
    args = ap.parse_args()

    db = sqlite3.connect(str(CODE_DB))
    found = search_gap(
        args.concept,
        args.patterns,
        args.synonyms,
        db,
        web=not args.local_only,
    )
    print(f"\nResult: {'found + ingested' if found else 'nothing new found'}")
    db.close()
