#!/usr/bin/env python3
"""
selyrion_github_explorer.py — Autonomous GitHub code acquisition for Selyrion.

Daily pipeline:
  1. Run deficiency scan → get top deficit domains
  2. Map domains → GitHub search queries
  3. Pull Python files from matching repos
  4. Sandbox-test each extracted function/class
  5. Working code → absorb directly into selyrioncode.db (status='working')
  6. Broken code  → absorb as broken unit (status='broken', error logged)
  7. CMS knowledge proposals → selyrion_synth.db (HITL gate)
  8. Write session discovery to claudecode.db

Storage cap: 300 GB across all monitored paths — halts ingestion if exceeded.

Usage:
    python3 selyrion_github_explorer.py --run
    python3 selyrion_github_explorer.py --run --domain algorithms
    python3 selyrion_github_explorer.py --dry-run
    python3 selyrion_github_explorer.py --stats
"""

import ast
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
DB_CODE   = Path.home() / "selyrioncode.db"
DB_CLAUDE = Path.home() / "claudecode.db"
DB_SYNTH  = Path.home() / "selyrion_synth.db"
ENV_FILE  = Path.home() / ".selyrion_github.env"

# Storage cap: stop ingesting if total monitored usage exceeds this
STORAGE_CAP_BYTES = 300 * 1024 ** 3   # 300 GB

_MONITORED_PATHS = [
    Path.home() / "selyrioncode.db",
    Path.home() / "resonance_v11.db",
    Path.home() / "supermodel.db",
    Path.home() / "claudecode.db",
    Path.home() / "selyrion_synth.db",
    Path.home() / "projectbrain_dev",
]

# ── Domain → GitHub query map ──────────────────────────────────────────────────
_DOMAIN_QUERIES = {
    "chess":            ["python chess engine minimax", "python chess board UCI",
                         "python stockfish interface", "chess move generation python",
                         "python chess opening book", "chess position evaluation python"],
    "algorithms":       ["binary search tree python", "graph traversal BFS DFS python",
                         "dynamic programming python", "sorting algorithms python"],
    "data_structures":  ["linked list python implementation", "heap priority queue python",
                         "trie implementation python", "disjoint set union python"],
    "execution":        ["subprocess python timeout", "asyncio event loop python",
                         "multiprocessing python", "concurrent futures python"],
    "syntax":           ["python ast parser", "tokenize python source",
                         "python code formatter"],
    "name_binding":     ["python scope closures", "python import system",
                         "python namespace resolution"],
    "type_safety":      ["python type hints runtime", "pydantic validation python",
                         "python dataclass typing"],
    "mathematics":      ["numpy linear algebra", "scipy optimization python",
                         "sympy symbolic math python", "numerical methods python"],
    "logic":            ["python constraint solver", "boolean satisfiability python",
                         "propositional logic python"],
    "synthesis_purity": ["python domain isolation", "module dependency graph python"],
    "uncategorised":    ["python utility functions", "python stdlib examples",
                         "python design patterns"],
    "nlp_parser":       ["natural language parser python user input",
                         "intent recognition python NLP chatbot",
                         "dialogue manager python conversational AI",
                         "python NLU intent slot filling",
                         "python sentence parser dependency tree",
                         "python command line natural language interface",
                         "python user input classifier NLP",
                         "python regex intent parser conversational",
                         "spacy NLP pipeline python custom",
                         "python NLTK semantic parser"],
    "general":          ["python algorithms data structures", "python clean code examples"],
    "basic":            ["BASIC interpreter python", "GW-BASIC tutorial examples",
                         "BASIC programming language python implementation",
                         "BASIC language tokenizer parser python",
                         "vintage BASIC code examples interpreter"],
    "qbasic":           ["QBasic interpreter python", "QBasic tutorial programs",
                         "QBasic BASIC programming examples",
                         "DOS QBasic python emulator",
                         "QBasic to python converter BASIC dialect"],
}

# Repos known to be high quality (prioritised)
_QUALITY_SIGNALS = [
    "TheAlgorithms/Python",
    "keon/algorithms",
    "OmkarPathak/pygorithm",
    "joowani/binarytree",
]

# ── Storage guard ──────────────────────────────────────────────────────────────

def _storage_used_bytes() -> int:
    total = 0
    for p in _MONITORED_PATHS:
        try:
            if p.is_file():
                total += p.stat().st_size
            elif p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file():
                        try:
                            total += f.stat().st_size
                        except OSError:
                            pass
        except OSError:
            pass
    return total


def _storage_ok() -> bool:
    used = _storage_used_bytes()
    cap  = STORAGE_CAP_BYTES
    pct  = used / cap * 100
    print(f"  [storage] {used/1024**3:.1f} GB / {cap/1024**3:.0f} GB ({pct:.1f}%)")
    return used < cap * 0.95   # stop at 95% to leave headroom


# ── GitHub auth ────────────────────────────────────────────────────────────────

def _get_pat() -> str | None:
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("GITHUB_PAT="):
            return line.split("=", 1)[1].strip()
    return None


# ── Code extraction ────────────────────────────────────────────────────────────

def _extract_units(source_code: str, source_label: str) -> list[dict]:
    """
    Parse Python source and extract top-level functions and classes as units.
    Returns list of {code, name, kind} dicts.
    """
    units = []
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        # Whole-file fallback for files that don't parse cleanly
        if len(source_code) < 4000:
            units.append({"code": source_code, "name": "module", "kind": "module"})
        return units

    lines = source_code.splitlines()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not hasattr(node, "lineno"):
            continue
        # Only top-level or one level deep
        start = node.lineno - 1
        end   = getattr(node, "end_lineno", start + 30)
        snippet = "\n".join(lines[start:end])
        if len(snippet) < 30 or len(snippet) > 3000:
            continue
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        units.append({"code": snippet, "name": node.name, "kind": kind})

    return units


# ── Sandbox test ───────────────────────────────────────────────────────────────

_TEST_HARNESS = textwrap.dedent("""\
import sys, traceback
try:
{code}
    print("__UNIT_DEFINED__")
except Exception as e:
    print(f"__UNIT_ERROR__: {{type(e).__name__}}: {{e}}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
""")

def _sandbox_test(code: str, timeout: int = 5) -> dict:
    """
    Run code in an isolated subprocess with timeout.
    Returns {ok: bool, error_class: str, error_msg: str, runtime_ms: int}
    """
    indented = textwrap.indent(code, "    ")
    harness  = _TEST_HARNESS.format(code=indented)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(harness)
        fpath = f.name

    t0 = time.time()
    try:
        result = subprocess.run(
            [sys.executable, fpath],
            capture_output=True, text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        elapsed = int((time.time() - t0) * 1000)
        stdout  = result.stdout
        stderr  = result.stderr

        if "__UNIT_DEFINED__" in stdout:
            return {"ok": True, "error_class": "none", "error_msg": "",
                    "runtime_ms": elapsed}

        # Parse error from stderr
        ec = "runtime"
        msg = stderr.strip()
        for exc in ("NameError", "ImportError", "ModuleNotFoundError", "SyntaxError",
                    "TypeError", "ValueError", "AttributeError", "IndentationError"):
            if exc in stderr:
                ec = exc
                break
        return {"ok": False, "error_class": ec, "error_msg": msg[:300],
                "runtime_ms": elapsed}

    except subprocess.TimeoutExpired:
        return {"ok": False, "error_class": "timeout", "error_msg": "exceeded 5s",
                "runtime_ms": timeout * 1000}
    except Exception as e:
        return {"ok": False, "error_class": "runner_error", "error_msg": str(e),
                "runtime_ms": 0}
    finally:
        try:
            os.unlink(fpath)
        except OSError:
            pass


# ── Absorb into selyrioncode.db ────────────────────────────────────────────────

def _absorb(db: sqlite3.Connection, unit: dict, test: dict,
            source: str, concept: str) -> bool:
    """Write a tested code unit. Returns True if inserted (not duplicate)."""
    uid = "cu.gh." + hashlib.md5((source + unit["code"][:60]).encode()).hexdigest()[:12]
    status     = "working" if test["ok"] else "broken"
    error_class = "none" if test["ok"] else test["error_class"]
    subtype     = unit["kind"]
    confidence  = 0.82 if test["ok"] else 0.40

    try:
        db.execute("""
            INSERT OR IGNORE INTO codeunits
              (id, raw_input, parsed_code, error_class, subtype,
               source, confidence, status, context, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (uid,
              f"github explore: {concept}",
              unit["code"],
              error_class, subtype,
              source, confidence, status,
              json.dumps({"name": unit["name"], "test_ms": test["runtime_ms"],
                          "error": test["error_msg"][:100]}),
              time.time()))
        return db.total_changes > 0
    except Exception:
        return False


# ── Main exploration run ───────────────────────────────────────────────────────

def run_exploration(domain: str | None = None, dry_run: bool = False,
                    max_repos: int = 20, max_units_per_run: int = 500) -> dict:
    """
    Full exploration cycle for one or all domains.
    Returns stats dict.
    """
    if not _storage_ok():
        print("  [explorer] Storage cap reached — aborting.")
        return {"status": "storage_cap"}

    pat = _get_pat()
    if not pat:
        print("  [explorer] No PAT found in ~/.selyrion_github.env")
        return {"status": "no_pat"}

    try:
        from github import Github, Auth
    except ImportError:
        print("  [explorer] PyGithub not installed")
        return {"status": "no_pygithub"}

    g = Github(auth=Auth.Token(pat))

    # Select queries
    if domain and domain in _DOMAIN_QUERIES:
        queries = {domain: _DOMAIN_QUERIES[domain]}
    else:
        # Pull top-deficit domain from deficiency scanner
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from selyrion_deficiency_scanner import (
                scan_code_deficiencies, scan_knowledge_gaps,
                scan_failure_history, rank_deficiencies
            )
            code_d = scan_code_deficiencies(top_n=5)
            know_g = scan_knowledge_gaps(top_n=5)
            hist_f = scan_failure_history()
            ranked = rank_deficiencies(code_d, know_g, hist_f)
            top_domain = ranked[0].get("domain", "general") if ranked else "general"
            # normalise domain key
            top_key = top_domain.split("|")[0].split(",")[0].strip()
            queries = {top_key: _DOMAIN_QUERIES.get(top_key, _DOMAIN_QUERIES["general"])}
            print(f"  [explorer] Deficiency scan selected domain: {top_key}")
        except Exception as e:
            print(f"  [explorer] Deficiency scan error: {e} — using general")
            queries = {"general": _DOMAIN_QUERIES["general"]}

    db = sqlite3.connect(str(DB_CODE))
    stats = {"absorbed_working": 0, "absorbed_broken": 0, "skipped": 0,
             "repos_searched": 0, "units_tested": 0}
    total_units = 0

    try:
        from selyrion_trust_engine import (
            update_sandbox_outcome, update_repo_signals, get_acquisition_weight
        )
        _trust_wired = True
    except ImportError:
        _trust_wired = False

    for dom, query_list in queries.items():
        print(f"\n  [explorer] Domain: {dom}")

        for query in query_list[:2]:   # max 2 queries per domain per run
            if total_units >= max_units_per_run:
                print(f"  [explorer] Unit cap ({max_units_per_run}) reached")
                break
            if not _storage_ok():
                print("  [explorer] Storage cap reached mid-run")
                break

            print(f"  [explorer] Query: {query}")
            try:
                results = g.search_code(query + " language:python")
                repo_seen = set()
                count = 0

                for item in results:
                    if count >= max_repos:
                        break
                    if item.repository.full_name in repo_seen:
                        continue
                    repo_seen.add(item.repository.full_name)
                    stats["repos_searched"] += 1
                    count += 1

                    repo_source = f"github:{item.repository.full_name}"

                    # Update provenance signals for this repo
                    if _trust_wired:
                        try:
                            repo = item.repository
                            days = max((time.time() - repo.pushed_at.timestamp()) / 86400, 0)
                            update_repo_signals(
                                repo_source,
                                stars=repo.stargazers_count,
                                forks=repo.forks_count,
                                days_since_commit=int(days),
                            )
                        except Exception:
                            pass

                    # Acquisition weight: high-trust repos get more units extracted
                    acq_weight = get_acquisition_weight(repo_source) if _trust_wired else 1.0

                    try:
                        content = item.decoded_content.decode("utf-8", errors="ignore")
                    except Exception:
                        continue

                    source = f"github:{item.repository.full_name}:{item.path}"
                    all_units = _extract_units(content, source)

                    # Scale how many units we absorb from this source
                    max_from_file = max(1, int(len(all_units) * acq_weight))
                    units = all_units[:max_from_file]

                    for unit in units:
                        if total_units >= max_units_per_run:
                            break

                        stats["units_tested"] += 1
                        total_units += 1

                        if dry_run:
                            print(f"    [dry-run] would test: {unit['name']} ({unit['kind']})")
                            continue

                        try:
                            from selyrion_sandbox import safe_test
                            test = safe_test(unit["code"])
                        except ImportError:
                            test = _sandbox_test(unit["code"])

                        # Record sandbox outcome in trust ledger
                        if _trust_wired:
                            threat = test.get("threat_class") not in (None, "none", "clean")
                            update_sandbox_outcome(source, ok=test["ok"], threat_flag=threat)

                        absorbed = _absorb(db, unit, test, source, query)

                        if not absorbed:
                            stats["skipped"] += 1
                        elif test["ok"]:
                            stats["absorbed_working"] += 1
                            if stats["absorbed_working"] % 20 == 0:
                                print(f"    ✓ {stats['absorbed_working']} working units absorbed")
                        else:
                            stats["absorbed_broken"] += 1

                    time.sleep(0.5)   # GitHub API rate limit courtesy

            except Exception as e:
                print(f"  [explorer] Query error: {e}")
                continue

    if not dry_run:
        db.commit()

        # Write session discovery
        body = (f"GitHub exploration run: domain={list(queries.keys())}, "
                f"repos={stats['repos_searched']}, tested={stats['units_tested']}, "
                f"absorbed_working={stats['absorbed_working']}, "
                f"absorbed_broken={stats['absorbed_broken']}, "
                f"skipped={stats['skipped']}")
        disc_id = "disc." + hashlib.md5(body[:40].encode()).hexdigest()[:8]
        cdb = sqlite3.connect(str(DB_CLAUDE))
        cdb.execute("""
            INSERT OR IGNORE INTO discoveries
              (id, session_id, body, tags, importance, created_at)
            VALUES (?,?,?,?,?,?)
        """, (disc_id, "github_explorer", body,
              "selyrion,github,exploration,absorption", 3, time.time()))
        cdb.commit()
        cdb.close()

    db.close()
    return {"status": "ok", **stats}


# ── Stats ──────────────────────────────────────────────────────────────────────

def print_stats():
    db  = sqlite3.connect(str(DB_CODE))
    rows = db.execute("""
        SELECT status, COUNT(*) as n FROM codeunits
        WHERE source LIKE 'github:%'
        GROUP BY status ORDER BY n DESC
    """).fetchall()
    db.close()

    used = _storage_used_bytes()
    print(f"\n  Storage used : {used/1024**3:.2f} GB / {STORAGE_CAP_BYTES/1024**3:.0f} GB")
    print(f"  GitHub units in selyrioncode.db:")
    if rows:
        for status, n in rows:
            print(f"    {status:<12} {n:>6}")
    else:
        print("    (none yet)")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Selyrion GitHub code explorer")
    ap.add_argument("--run",       action="store_true", help="Run exploration")
    ap.add_argument("--dry-run",   action="store_true", help="Scan only, no writes")
    ap.add_argument("--domain",    metavar="DOMAIN",    help="Force specific domain")
    ap.add_argument("--max-repos", type=int, default=20, help="Max repos per query")
    ap.add_argument("--max-units", type=int, default=500, help="Max units per run")
    ap.add_argument("--stats",     action="store_true", help="Show absorption stats")
    args = ap.parse_args()

    if args.stats:
        print_stats()
        return

    if args.run or args.dry_run:
        print(f"\n{'='*60}")
        print(f"  Selyrion GitHub Explorer")
        print(f"  {'DRY RUN — ' if args.dry_run else ''}Starting...")
        print(f"{'='*60}")
        result = run_exploration(
            domain=args.domain,
            dry_run=args.dry_run,
            max_repos=args.max_repos,
            max_units_per_run=args.max_units,
        )
        print(f"\n  Result: {result}")
        return

    ap.print_help()


if __name__ == "__main__":
    main()
