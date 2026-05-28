#!/usr/bin/env python3
"""
selyrion_task.py — Unified task runner for Selyrion SCOS.

Give Selyrion a natural-language task. It classifies intent, selects tools,
chains them, and returns a structured result with optional proposal write.

Supported intents (auto-detected, can overlap):
  self_evaluate  — scan deficiencies, report weaknesses
  propose        — run deficiency scan + parliament + write proposal to synth.db
  search         — CMS memory search
  code           — delegate to scos_bridge for code execution
  parliament     — spawn parliament on the prompt directly
  audit          — full cycle: all intents in sequence

Usage:
    python3 selyrion_task.py "examine your reasoning engine and propose upgrades"
    python3 selyrion_task.py "what do you know about pawn structure?" --domain chess
    python3 selyrion_task.py "evaluate your weakest code domains" --dry-run
    python3 selyrion_task.py "full audit" --domain scos

Programmatic:
    from selyrion_task import run_task
    result = run_task("evaluate reasoning engine and propose upgrades")
    print(result["report"])
"""

import sys, json, hashlib, time, sqlite3, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from trace_writer import Trace

# ── DB paths ──────────────────────────────────────────────────────────────────

CLAUDECODE_DB = Path.home() / "claudecode.db"
SYNTH_DB      = Path.home() / "selyrion_synth.db"
CMS_DB        = Path.home() / "resonance_v11.db"

# ── Terminal colours ──────────────────────────────────────────────────────────

SEL  = "\033[38;5;141m"
OK   = "\033[32m"
WARN = "\033[33m"
ERR  = "\033[31m"
DIM  = "\033[2m"
B    = "\033[1m"
R    = "\033[0m"
LINE = "─" * 66

# ── Intent classification ─────────────────────────────────────────────────────

_EVALUATE_WORDS = {
    "evaluate", "assess", "examine", "analyse", "analyze", "review", "inspect",
    "weaknesses", "weakness", "strengths", "strength", "diagnose", "diagnosis",
    "how", "am", "doing", "status", "health", "capability", "capabilities",
    "current", "state", "audit", "check",
}
_PROPOSE_WORDS = {
    "propose", "proposal", "upgrade", "improve", "improvement", "enhance",
    "enhancement", "fix", "fixes", "plan", "recommendation", "recommendations",
    "advance", "advanced", "efficient", "efficiency", "better", "next",
    "write", "generate", "create", "build",  # only when paired with non-code nouns
}
_SEARCH_WORDS = {
    "search", "find", "what", "recall", "tell", "explain", "about",
    "show", "lookup", "look", "know", "knowledge", "retrieve", "fetch",
    "describe", "definition", "define",
}
_CODE_WORDS = {
    "run", "execute", "calculate", "compute", "script", "fibonacci",
    "factorial", "sort", "plot", "simulate", "fft", "fourier", "function",
    "algorithm", "output", "print",
}
_PARLIAMENT_WORDS = {
    "debate", "deliberate", "parliament", "parliament's", "opinion",
    "think", "believe", "consensus", "vote", "argue", "disagree",
}
_AUDIT_WORDS = {"audit", "deep", "dive", "comprehensive", "everything", "full"}

_DOMAIN_MAP = {
    "chess":      {"chess", "pawn", "opening", "endgame", "move", "piece", "position",
                   "tactical", "strategy", "motif", "stockfish"},
    "scos":       {"scos", "reasoning", "engine", "pipeline", "architecture", "cognitive",
                   "parliament", "selyrion", "system", "self", "framework"},
    "code":       {"code", "codeops", "selyrioncode", "error", "bug", "fix", "syntax",
                   "runtime", "import", "module", "python", "bash"},
    "knowledge":  {"knowledge", "cms", "memory", "resonance", "anchor", "relation",
                   "concept", "domain", "ontology", "graph"},
    "linguistics":{"linguistic", "language", "langeng", "grammar", "syntax", "word",
                   "phrase", "sentence", "nlg", "articulation"},
}


def classify_intent(prompt: str) -> list[str]:
    words = set(prompt.lower().split())
    intents = []
    if words & _AUDIT_WORDS:
        return ["audit"]
    if words & _EVALUATE_WORDS:
        intents.append("self_evaluate")
    if words & _PROPOSE_WORDS:
        intents.append("propose")
    if words & _PARLIAMENT_WORDS:
        intents.append("parliament")
    if (words & _SEARCH_WORDS) and not intents:
        intents.append("search")
    if words & _CODE_WORDS:
        intents.append("code")
    return intents or ["search"]


def extract_domain(prompt: str) -> str:
    words = set(prompt.lower().split())
    for domain, keywords in _DOMAIN_MAP.items():
        if words & keywords:
            return domain
    return ""


# ── Step: deficiency scan ─────────────────────────────────────────────────────

def _step_deficiency_scan(domain_hint: str = "") -> dict:
    print(f"\n  {SEL}▶ SCAN:{R} deficiency scanner", flush=True)
    try:
        from selyrion_deficiency_scanner import (
            scan_code_deficiencies,
            scan_knowledge_gaps,
            scan_failure_history,
            rank_deficiencies,
        )
        code_defs  = scan_code_deficiencies(top_n=15)
        know_gaps  = scan_knowledge_gaps(top_n=15)
        hist_fails = scan_failure_history(days=30)

        # Filter to domain if given
        if domain_hint:
            code_defs  = [x for x in code_defs  if domain_hint in x.get("domain","")]  or code_defs[:5]
            know_gaps  = [x for x in know_gaps  if domain_hint in x.get("domain","")]  or know_gaps[:5]
            hist_fails = [x for x in hist_fails if domain_hint in x.get("domain","")]  or hist_fails[:5]

        ranked = rank_deficiencies(code_defs, know_gaps, hist_fails)
        top    = ranked[:5]

        print(f"  {OK}[ok]{R} → {len(ranked)} deficits found, top domain: "
              f"{top[0].get('domain', top[0].get('error_class','?')) if top else 'none'}")
        return {"status": "ok", "ranked": ranked, "top": top,
                "code_defs": code_defs[:5], "know_gaps": know_gaps[:5],
                "hist_fails": hist_fails[:5]}
    except Exception as e:
        print(f"  {ERR}[error]{R} deficiency scan: {e}")
        return {"status": "error", "error": str(e), "ranked": [], "top": []}


# ── Step: memory search ───────────────────────────────────────────────────────

def _step_memory_search(query: str, domain: str = "") -> dict:
    print(f"\n  {SEL}▶ SEARCH:{R} CMS memory  ({query[:60]})", flush=True)
    try:
        import scos_tools
        import tools  # registers all tools
        result = scos_tools.registry.invoke("memory_search", {
            "query": query[:120], "domain": domain, "limit": 8,
        }, "selyrion_task")
        count = result.get("count", 0)
        print(f"  {OK}[ok]{R} → {count} relations found")
        return result
    except Exception as e:
        print(f"  {ERR}[error]{R} memory search: {e}")
        return {"status": "error", "error": str(e), "relations": [], "anchors": []}


# ── Step: parliament spawn ────────────────────────────────────────────────────

def _step_parliament(prompt: str, context: str = "", domain: str = "") -> dict:
    print(f"\n  {SEL}▶ PARLIAMENT:{R} spawning deliberation...", flush=True)
    try:
        from tools.parliament_spawn import spawn_parliament
        result = spawn_parliament(
            prompt=prompt,
            domain=domain or "general",
            context=context,
            models=None,  # uses DEFAULT_MODELS from parliament_spawn
            no_deliberation=False,
            dry_run=False,
        )
        conf = result.get("confidence", 0)
        print(f"  {OK}[ok]{R} → confidence={conf:.2f}  "
              f"agreement={result.get('agreement_count',0)}/"
              f"{len(result.get('models', []))}")
        return result
    except Exception as e:
        print(f"  {ERR}[error]{R} parliament: {e}")
        return {"status": "error", "error": str(e), "conclusion": "", "confidence": 0.0}


# ── Step: write proposal ──────────────────────────────────────────────────────

def _step_write_proposal(
    task_prompt: str,
    domain: str,
    scan_result: dict,
    parl_result: dict,
    dry_run: bool = False,
) -> str | None:
    if dry_run:
        print(f"\n  {DIM}[dry-run] Would write proposal to selyrion_synth.db{R}")
        return None

    print(f"\n  {SEL}▶ PROPOSAL:{R} writing to selyrion_synth.db", flush=True)
    try:
        from selyrion_deficiency_scanner import (
            generate_improvement_goal, write_proposal, _ensure_proposals_table,
        )
        top = scan_result.get("top", [])
        if not top:
            # Synthesise a generic target from the parliament conclusion
            target = {
                "deficit_type": "knowledge",
                "domain":       domain or "scos",
                "rel_density":  0.0,
                "anchor_count": 0,
                "low_maturity": 0,
                "unified_score": 5.0,
                "score":        5.0,
            }
        else:
            target = top[0]

        # Inject parliament conclusion into the proposal content
        conclusion = parl_result.get("conclusion", "")
        if conclusion:
            existing = json.loads(target.get("proposed_content") or "{}")  if isinstance(target.get("proposed_content"), str) else {}
            target = dict(target)  # don't mutate original
            target["parliament_conclusion"] = conclusion[:400]

        _ensure_proposals_table()
        goal_id = generate_improvement_goal(target)
        prop_id = write_proposal(target, goal_id)

        # Augment proposal with parliament conclusion if available
        if conclusion and prop_id:
            try:
                db = sqlite3.connect(SYNTH_DB)
                db.execute(
                    "UPDATE improvement_proposals SET proposed_action = ? WHERE id = ?",
                    (f"[Parliament] {conclusion[:300]}", prop_id)
                )
                db.commit(); db.close()
            except Exception:
                pass

        print(f"  {OK}[ok]{R} → proposal={prop_id}")
        return prop_id

    except Exception as e:
        print(f"  {ERR}[error]{R} write_proposal: {e}")
        return None


# ── Step: scos_bridge code task ───────────────────────────────────────────────

def _step_code(prompt: str) -> dict:
    print(f"\n  {SEL}▶ CODE:{R} scos_bridge", flush=True)
    try:
        from scos_bridge import SelyrionBridge
        bridge = SelyrionBridge()
        return bridge.reason(prompt)
    except Exception as e:
        print(f"  {ERR}[error]{R} scos_bridge: {e}")
        return {"status": "error", "error": str(e)}


# ── Report formatter ──────────────────────────────────────────────────────────

def _format_report(
    prompt: str,
    intents: list[str],
    domain: str,
    scan: dict | None,
    search: dict | None,
    parl: dict | None,
    proposal_id: str | None,
) -> str:
    lines = [
        f"\n{B}{LINE}{R}",
        f"  {SEL}{B}Selyrion Task Report{R}",
        f"  {DIM}prompt : {prompt[:80]}{R}",
        f"  {DIM}intents: {', '.join(intents)}  domain: {domain or 'all'}{R}",
        f"{B}{LINE}{R}",
    ]

    if scan and scan.get("status") == "ok":
        top = scan.get("top", [])
        lines.append(f"\n{B}── Deficiency Scan ──{R}")
        if top:
            for i, d in enumerate(top[:5], 1):
                dtype  = d.get("deficit_type", "?")
                dom    = d.get("domain", d.get("error_class", "?"))
                score  = d.get("unified_score", 0)
                detail = ""
                if dtype == "code":
                    detail = f"  {d.get('broken_count',0)} broken units | {d.get('fix_hint','')[:60]}"
                elif dtype == "knowledge":
                    detail = f"  density={d.get('rel_density',0):.3f}  low_mat={d.get('low_maturity',0)}"
                elif dtype == "failure":
                    detail = f"  count={d.get('count',0)}"
                lines.append(f"  {i}. [{dtype}] {dom}  score={score:.1f}{detail}")
        else:
            lines.append(f"  {DIM}No deficiencies found.{R}")

    if search and search.get("status") == "success":
        rels = search.get("relations", [])
        lines.append(f"\n{B}── CMS Memory ({len(rels)} relations) ──{R}")
        for rel in rels[:6]:
            lines.append(
                f"  {rel.get('subject','')} {DIM}→{R} "
                f"{rel.get('predicate','')} {DIM}→{R} "
                f"{rel.get('object','')}  "
                f"{DIM}[conf={rel.get('confidence',0):.2f}]{R}"
            )
        anchors = search.get("anchors", [])
        if anchors:
            lines.append(f"  {DIM}anchors: {', '.join(a['concept'] for a in anchors[:4])}{R}")

    if parl and parl.get("conclusion"):
        conf     = parl.get("confidence", 0)
        agree    = parl.get("agreement_count", 0)
        total    = len(parl.get("models", []))
        dissent  = parl.get("dissent_summary", "")
        minority = parl.get("minority_view", "")
        lines.append(f"\n{B}── Parliament Conclusion ──{R}")
        lines.append(f"  {parl['conclusion']}")
        lines.append(f"  {DIM}conf={conf:.2f}  agreement={agree}/{total}{R}")
        if dissent:
            lines.append(f"  {WARN}dissent: {dissent[:120]}{R}")
        if minority:
            lines.append(f"  {DIM}minority: {minority[:120]}{R}")
        insights = parl.get("key_insights", [])
        if insights:
            lines.append(f"\n{B}  Key insights:{R}")
            for ins in insights[:4]:
                lines.append(f"    • {ins}")

    if proposal_id:
        lines.append(f"\n{B}── Proposal Written ──{R}")
        lines.append(f"  {OK}ID: {proposal_id}{R}")
        lines.append(f"  Review: {DIM}python3 selyrion_deficiency_scanner.py --list-proposals{R}")
        lines.append(f"  Approve:{DIM}python3 selyrion_deficiency_scanner.py --approve {proposal_id}{R}")
    elif "propose" in intents:
        lines.append(f"\n  {DIM}(dry-run — no proposal written){R}")

    lines.append(f"\n{B}{LINE}{R}")
    return "\n".join(lines)


# ── Log to claudecode.db ──────────────────────────────────────────────────────

def _log_task(prompt: str, intents: list, domain: str, proposal_id: str | None):
    body = (f"selyrion_task: {', '.join(intents)} | domain={domain or 'all'} | "
            f"prompt={prompt[:80]}" +
            (f" | proposal={proposal_id}" if proposal_id else ""))
    did = "disc." + hashlib.md5(body[:40].encode()).hexdigest()[:8]
    try:
        db = sqlite3.connect(CLAUDECODE_DB)
        db.execute(
            "INSERT OR IGNORE INTO discoveries (id,session_id,body,tags,importance,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (did, "selyrion_task", body,
             f"selyrion,task,{','.join(intents)},{domain or 'general'}",
             3, time.time())
        )
        db.commit(); db.close()
    except Exception:
        pass


# ── Main orchestrator ─────────────────────────────────────────────────────────

def run_task(
    prompt: str,
    domain: str = "",
    dry_run: bool = False,
    no_parliament: bool = False,
    session_id: str = "selyrion_task",
) -> dict:
    intents = classify_intent(prompt)
    domain  = domain or extract_domain(prompt)
    _trace  = Trace("run_task", session_id, domain=domain, intent=prompt)

    print(f"\n{B}{LINE}{R}")
    print(f"  {SEL}{B}Selyrion{R} | {prompt[:80]}")
    print(f"  {DIM}intents: {intents}   domain: {domain or 'all'}{R}")
    print(f"{B}{LINE}{R}")

    scan       = None
    search     = None
    parl       = None
    prop_id    = None
    code_res   = None

    is_audit = "audit" in intents

    # ── self_evaluate / audit ──────────────────────────────────────────────────
    if "self_evaluate" in intents or is_audit:
        scan = _step_deficiency_scan(domain)

    # ── search / audit ─────────────────────────────────────────────────────────
    if "search" in intents or "self_evaluate" in intents or is_audit:
        if "search" in intents:
            search_query = prompt
            search_domain = domain
        else:
            # For self-evaluate, extract concrete nouns from the prompt for CMS matching
            _stop = {"examine","evaluate","current","version","your","and","determine",
                     "if","you","can","make","it","more","then","write","an","the","of",
                     "a","is","are","be","been","being","to","do","does","did","in","for"}
            search_query = " ".join(
                w for w in prompt.lower().split() if w not in _stop and len(w) > 3
            )[:120]
            # Don't filter by domain for self-evaluate — broader is better
            search_domain = ""
        search = _step_memory_search(search_query, search_domain)
        # Retry without domain if first attempt returned nothing
        if search.get("count", 0) == 0 and domain:
            search = _step_memory_search(search_query, "")

    # ── code ───────────────────────────────────────────────────────────────────
    if "code" in intents:
        code_res = _step_code(prompt)

    # ── parliament ─────────────────────────────────────────────────────────────
    if ("parliament" in intents or "propose" in intents or is_audit) and not no_parliament:
        # Build rich context from scan + search for parliament prompt
        ctx_parts = []

        if scan and scan.get("top"):
            top_lines = []
            for d in scan["top"][:3]:
                dtype = d.get("deficit_type","?")
                dom   = d.get("domain", d.get("error_class","?"))
                score = d.get("unified_score", 0)
                top_lines.append(f"  [{dtype}] {dom} score={score:.1f}")
            ctx_parts.append("Top deficits:\n" + "\n".join(top_lines))

        if search and search.get("relations"):
            rel_lines = [
                f"  {r['subject']} → {r['predicate']} → {r['object']}"
                for r in search["relations"][:5]
            ]
            ctx_parts.append("CMS knowledge:\n" + "\n".join(rel_lines))

        context = "\n\n".join(ctx_parts)

        parl_prompt = (
            prompt if "parliament" in intents else
            f"Based on the deficiency scan and CMS knowledge provided in context: "
            f"{prompt}. Be specific — name concrete improvements, prioritise by impact."
        )
        parl = _step_parliament(parl_prompt, context=context, domain=domain or "scos")

    # ── propose ────────────────────────────────────────────────────────────────
    if "propose" in intents or is_audit:
        prop_id = _step_write_proposal(
            prompt, domain,
            scan or {"top": []},
            parl or {},
            dry_run=dry_run,
        )

    report = _format_report(prompt, intents, domain, scan, search, parl, prop_id)
    print(report)

    _log_task(prompt, intents, domain, prop_id)

    # ── Flush execution trace ──────────────────────────────────────────────────
    tool_chain = ["classify_intent"]
    if scan:   tool_chain.append("deficiency_scan")
    if search: tool_chain.append("memory_search")
    if parl:   tool_chain.append("parliament")
    if prop_id: tool_chain.append("write_proposal")
    _trace.set_tool_chain(tool_chain)
    if search and search.get("relations"):
        _trace.set_memory_reads([r.get("subject","") + "→" + r.get("object","")
                                  for r in search["relations"][:10]])
    if prop_id:
        _trace.set_memory_writes([f"proposal:{prop_id}"])
    if parl and parl.get("confidence"):
        _trace.add_confidence("parliament", parl["confidence"])
    _trace.set_output(f"intents={intents} prop={prop_id}")
    _trace.succeed()
    _trace._flush()

    return {
        "status":      "complete",
        "intents":     intents,
        "domain":      domain,
        "scan":        scan,
        "search":      search,
        "parliament":  parl,
        "proposal_id": prop_id,
        "code":        code_res,
        "report":      report,
    }


# ── Proposal expansion (Option A) ─────────────────────────────────────────────

_SPEC_FIELDS = [
    ("implementation_strategy", "Exact implementation approach — algorithm, data structure, specific technique"),
    ("resource_cost",           "RAM limits, time complexity, disk usage, impact on parliament throughput"),
    ("validation_metrics",      "Baseline measurements + target values: nodes/sec, latency, accuracy, Elo delta"),
    ("rollback_plan",           "How to revert if the change degrades performance or stability"),
    ("sandbox_validation",      "How to test safely in isolation before promoting to production"),
]

def expand_proposal(proposal_id: str, dry_run: bool = False) -> dict:
    """
    Expand an underspecified proposal with a 5-field implementation spec.
    Spawns a targeted parliament round, parses the result, updates synth.db.
    Returns the enriched spec dict.
    """
    db = sqlite3.connect(SYNTH_DB)
    row = db.execute(
        "SELECT id, proposal_type, deficit_domain, proposed_action, proposed_content "
        "FROM improvement_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    db.close()

    if not row:
        print(f"  {ERR}[error]{R} proposal not found: {proposal_id}")
        return {"status": "error", "error": "not found"}

    pid, ptype, domain, action, content_json = row
    content = {}
    try:
        content = json.loads(content_json or "{}")
    except Exception:
        pass

    print(f"\n{B}{LINE}{R}")
    print(f"  {SEL}{B}Selyrion:{R} Expanding proposal {pid}")
    print(f"  {DIM}type={ptype}  domain={domain}  action={action[:80]}{R}")
    print(f"{B}{LINE}{R}")

    spec = {}
    for field, description in _SPEC_FIELDS:
        print(f"\n  {SEL}▶ SPEC:{R} {field}", flush=True)
        parl_prompt = (
            f"For this improvement proposal:\n"
            f"  Domain: {domain}\n"
            f"  Action: {action}\n\n"
            f"Provide a concrete, specific answer for:\n"
            f"  {description}\n\n"
            f"Be direct. Name exact values, algorithms, and constraints. "
            f"2-4 sentences maximum."
        )
        result = _step_parliament(parl_prompt, domain=domain or "scos")
        spec[field] = result.get("conclusion", "")
        spec[f"{field}_confidence"] = result.get("confidence", 0.0)

    print(f"\n{B}── Expanded Spec ──{R}")
    for field, _ in _SPEC_FIELDS:
        val = spec.get(field, "")
        conf = spec.get(f"{field}_confidence", 0)
        print(f"  {B}{field}{R}  {DIM}[conf={conf:.2f}]{R}")
        print(f"    {val}")

    if not dry_run:
        content["spec"] = {f: spec.get(f, "") for f, _ in _SPEC_FIELDS}
        db = sqlite3.connect(SYNTH_DB)
        db.execute(
            "UPDATE improvement_proposals SET proposed_content = ?, proposed_action = ? WHERE id = ?",
            (json.dumps(content),
             action + "\n\nSPEC:\n" + "\n".join(
                 f"  {f}: {spec.get(f,'')[:200]}" for f, _ in _SPEC_FIELDS
             ),
             proposal_id)
        )
        db.commit(); db.close()
        print(f"\n  {OK}[ok]{R} Proposal {proposal_id} updated with spec.")
    else:
        print(f"\n  {DIM}[dry-run] No DB writes.{R}")

    return {"status": "ok", "proposal_id": proposal_id, "spec": spec}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Selyrion unified task runner")
    parser.add_argument("prompt", nargs="?",
                        default="evaluate my reasoning capabilities and propose improvements")
    parser.add_argument("--domain",        default="",    help="Force domain (chess/scos/code/knowledge)")
    parser.add_argument("--dry-run",       action="store_true", help="Skip all DB writes")
    parser.add_argument("--no-parliament", action="store_true", help="Skip parliament deliberation")
    parser.add_argument("--expand",        default="",   metavar="PROPOSAL_ID",
                        help="Expand an existing proposal with full implementation spec")
    args = parser.parse_args()

    if args.expand:
        expand_proposal(args.expand, dry_run=args.dry_run)
    else:
        run_task(
            prompt=args.prompt,
            domain=args.domain,
            dry_run=args.dry_run,
            no_parliament=args.no_parliament,
        )
