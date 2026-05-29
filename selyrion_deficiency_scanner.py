#!/usr/bin/env python3
"""
selyrion_deficiency_scanner.py — Autonomous knowledge & code deficiency detection.

Scan pipeline:
  1. scan_code_deficiencies()    — broken codeunits ranked by error_class
  2. scan_knowledge_gaps()       — low-maturity / sparse CMS domains
  3. scan_failure_history()      — recent claudecode.db failures by domain
  4. rank_deficiencies()         — unified score across all three axes
  5. select_target()             — pick highest-deficit area
  6. generate_improvement_goal() — write goal to claudecode.db.goals
  7. write_proposal()            — write proposal to selyrion_synth.db (HITL gate)

Write-back (after approval):
  Code fix    → selyrioncode.db.fix_pairs
  Knowledge   → resonance_v11.db.relations_aggregated (via synth_relations promotion)
  Both        → claudecode.db.discoveries (always)

Usage:
    python3 selyrion_deficiency_scanner.py --scan
    python3 selyrion_deficiency_scanner.py --generate
    python3 selyrion_deficiency_scanner.py --list-proposals
    python3 selyrion_deficiency_scanner.py --approve PROPOSAL_ID
    python3 selyrion_deficiency_scanner.py --reject  PROPOSAL_ID
    python3 selyrion_deficiency_scanner.py --promote PROPOSAL_ID
    python3 selyrion_deficiency_scanner.py --daily   # full cycle: scan + generate
"""

import sys
import time
import json
import hashlib
import sqlite3
import argparse
from pathlib import Path

# ── DB paths ──────────────────────────────────────────────────────────────────
DB_CODE    = Path("/home/timbushnell/selyrioncode.db")
DB_CLAUDE  = Path("/home/timbushnell/claudecode.db")
DB_CMS     = Path("/home/timbushnell/resonance_v11.db")
DB_SYNTH   = Path("/home/timbushnell/selyrion_synth.db")

# ── Error class → human domain label ─────────────────────────────────────────
_EC_DOMAIN = {
    "runtime":          "execution",
    "syntax":           "syntax",
    "NameError":        "name_binding",
    "TypeError":        "type_safety",
    "RuntimeError":     "execution",
    "logic":            "logic",
    "environment":      "environment",
    "IndentationError": "syntax",
    "SyntaxError":      "syntax",
    "AttributeError":   "attribute_access",
    "ValueError":       "value_handling",
    "domain_contamination": "synthesis_purity",
    "wrong_domain":     "synthesis_purity",
    "none":             "uncategorised",
    "unknown":          "uncategorised",
}

_SUBTYPE_FIXES = {
    "missing_module":   "Add missing import or install dependency",
    "missing_file":     "Verify file path or add path guard",
    "type_mismatch":    "Add type coercion or input validation",
    "missing_key":      "Add dict.get() with default or key existence check",
    "missing_import":   "Inject import statement at top of synthesised code",
    "missing_attribute":"Check object type before attribute access",
    "indentation":      "Reformat indentation to PEP-8 standard",
    "syntax_error":     "Parse and correct syntax in generated snippet",
    "undefined_name":   "Resolve NameError: add assignment or import",
    "json_parse_error": "Wrap JSON parse in try/except, validate input",
    "value_mismatch":   "Add range/type guard before value use",
}


def _uid(prefix: str, body: str) -> str:
    return prefix + hashlib.md5(body.encode()).hexdigest()[:12]


def _now() -> float:
    return time.time()


# ── Schema bootstrap ──────────────────────────────────────────────────────────

def _ensure_proposals_table():
    db = sqlite3.connect(DB_SYNTH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS improvement_proposals (
            id              TEXT PRIMARY KEY,
            proposal_type   TEXT NOT NULL,
            deficit_domain  TEXT,
            deficit_metric  TEXT,
            proposed_action TEXT,
            proposed_content TEXT,
            target_db       TEXT,
            target_table    TEXT,
            confidence      REAL DEFAULT 0.70,
            review_status   TEXT DEFAULT 'pending',
            created_at      REAL,
            reviewed_at     REAL,
            reviewed_by     TEXT
        )
    """)
    db.commit()
    db.close()


# ── Scan: code deficiencies ───────────────────────────────────────────────────

def scan_code_deficiencies(top_n: int = 10) -> list[dict]:
    """Return broken-unit counts ranked by frequency."""
    db = sqlite3.connect(DB_CODE)
    rows = db.execute("""
        SELECT error_class, subtype, COUNT(*) AS n
        FROM   codeunits
        WHERE  status = 'broken'
        GROUP  BY error_class, subtype
        ORDER  BY n DESC
        LIMIT  ?
    """, (top_n,)).fetchall()
    db.close()

    results = []
    for ec, sub, n in rows:
        domain  = _EC_DOMAIN.get(ec, "unknown")
        hint    = _SUBTYPE_FIXES.get(sub or "", "")
        results.append({
            "source":       "code",
            "error_class":  ec or "unknown",
            "subtype":      sub or "",
            "domain":       domain,
            "broken_count": n,
            "fix_hint":     hint,
            "score":        float(n),
        })
    return results


# ── Scan: knowledge gaps ──────────────────────────────────────────────────────

def scan_knowledge_gaps(top_n: int = 10) -> list[dict]:
    """Return CMS domains with lowest relation density and lowest maturity."""
    db = sqlite3.connect(DB_CMS)

    # Two-source relation counting — required because domain_tags is overloaded:
    #   - Most domains (biology, physics, etc.): relations use source provenance as
    #     domain_tags ('openalex', 'wikidata'). Must count by anchor ID ownership.
    #   - Chess-style domains: relations use 'chess|' domain_tags with chess.* object
    #     IDs not in anchors. Must count by LIKE on domain_tags.
    # Strategy: take MAX(id_count, tag_count) per domain to get true coverage.
    # Synthetic internal tags (_bridge, _2hop_scored, etc.) are excluded.
    from collections import defaultdict

    all_anchors = db.execute(
        "SELECT domain_tags, id, maturity FROM anchors WHERE domain_tags != ''"
    ).fetchall()

    anchor_counts: dict[str, int]  = defaultdict(int)
    low_mat_counts: dict[str, int] = defaultdict(int)
    domain_anchor_ids: dict[str, list] = defaultdict(list)

    for domain_tags, aid, maturity in all_anchors:
        base = domain_tags.split(',')[0].split('|')[0].strip()
        if not base or base.startswith('_'):
            continue
        anchor_counts[base] += 1
        domain_anchor_ids[base].append(aid)
        if maturity < 2.0:
            low_mat_counts[base] += 1

    rel_counts: dict[str, int] = {}
    for domain, aids in domain_anchor_ids.items():
        if anchor_counts[domain] < 5:
            continue
        # Method 1: count by anchor ID ownership (covers openalex/wikidata sourced domains)
        placeholders = ','.join('?' * min(len(aids), 500))
        (id_cnt,) = db.execute(
            f"SELECT COUNT(*) FROM relations_aggregated WHERE subject_id IN ({placeholders})",
            aids[:500]
        ).fetchone()
        # Method 2: count by domain_tags LIKE (covers chess-style namespaced IDs)
        (tag_cnt,) = db.execute(
            "SELECT COUNT(*) FROM relations_aggregated WHERE domain_tags LIKE ?",
            (f"%{domain}%",)
        ).fetchone()
        rel_counts[domain] = max(id_cnt, tag_cnt)

    db.close()

    rows = [(d, anchor_counts[d], rel_counts[d]) for d in rel_counts]
    low_mat = low_mat_counts

    results = []
    for domain, anchors, rels in rows:
        density  = rels / max(anchors, 1)
        lm       = low_mat.get(domain, 0)
        lm_ratio = lm / max(anchors, 1)          # fraction of anchors that are low-maturity
        score    = (1.0 / (density + 0.01)) + lm_ratio * 2.0
        results.append({
            "source":        "knowledge",
            "domain":        domain,
            "anchor_count":  anchors,
            "rel_count":     rels,
            "rel_density":   round(density, 3),
            "low_maturity":  lm,
            "score":         round(score, 2),
        })
    return sorted(results, key=lambda x: -x["score"])[:top_n]


# ── Scan: failure history ─────────────────────────────────────────────────────

def scan_failure_history(days: int = 30) -> list[dict]:
    """Return recent failures from claudecode.db grouped by first tag."""
    cutoff = _now() - days * 86400
    db = sqlite3.connect(DB_CLAUDE)
    rows = db.execute("""
        SELECT tags, COUNT(*) AS n, MAX(created_at) AS latest
        FROM   failures
        WHERE  created_at >= ?
        GROUP  BY tags
        ORDER  BY n DESC
    """, (cutoff,)).fetchall()
    db.close()

    results = []
    for tags, n, latest in rows:
        first_tag = (tags or "unknown").split(",")[0].strip()
        results.append({
            "source":    "history",
            "tags":      tags,
            "domain":    first_tag,
            "count":     n,
            "latest":    latest,
            "score":     float(n),
        })
    return results


# ── Unified ranking ───────────────────────────────────────────────────────────

def rank_deficiencies(
    code: list[dict],
    knowledge: list[dict],
    history: list[dict],
) -> list[dict]:
    """Merge all three axes into a single ranked list."""
    scored = []

    # Normalise code scores to 0-100 range (so 765 units ≠ always dominant)
    max_code = max((x["score"] for x in code), default=1.0)
    for item in code:
        norm = (item["score"] / max_code) * 100.0
        scored.append({
            **item,
            "unified_score": norm * 2.0,   # code still weighted 2× vs knowledge
            "deficit_type":  "code",
        })

    # Knowledge gaps — scale by log(anchor_count) so large sparse domains win over
    # tiny sparse ones without capping (chess 1198 → 3.1x, mathematics 140K → 5.1x)
    import math
    for item in knowledge:
        anchor_scale = math.log10(max(item.get("anchor_count", 1), 10))
        scored.append({
            **item,
            "unified_score": item["score"] * anchor_scale,
            "deficit_type":  "knowledge",
        })

    # Recent failures — boost if recent
    for item in history:
        recency = max(0.0, 1.0 - (_now() - item["latest"]) / 86400 / 30)
        scored.append({
            **item,
            "unified_score": item["score"] * (1.0 + recency),
            "deficit_type":  "failure",
        })

    scored.sort(key=lambda x: -x["unified_score"])
    return scored


# ── Select target ─────────────────────────────────────────────────────────────

def select_target(ranked: list[dict]) -> dict | None:
    if not ranked:
        return None
    return ranked[0]


# ── Generate improvement goal ─────────────────────────────────────────────────

def generate_improvement_goal(target: dict) -> str:
    """Write a goal to claudecode.db.goals. Returns goal id."""
    dtype = target.get("deficit_type", "code")

    if dtype == "code":
        ec   = target.get("error_class", "unknown")
        sub  = target.get("subtype", "")
        n    = target.get("broken_count", 0)
        desc = f"resolve {n} broken codeunits: error_class={ec} subtype={sub}"
        steps = [
            f"retrieve sample broken units with error_class={ec} subtype={sub}",
            f"analyse root cause pattern for {ec}/{sub}",
            f"synthesise fix template for {ec}/{sub}",
            "write fix proposal to selyrion_synth.db improvement_proposals",
        ]
    elif dtype == "knowledge":
        domain  = target.get("domain", "unknown")
        density = target.get("rel_density", 0)
        desc    = f"expand knowledge in domain '{domain}' (rel_density={density})"
        steps   = [
            f"retrieve anchors in domain '{domain}' with lowest relation counts",
            f"identify missing predicates for top-10 anchors in '{domain}'",
            "propose new relations via inversion and transitive closure",
            "write relation proposals to selyrion_synth.db synth_relations",
        ]
    else:
        tags = target.get("tags", "unknown")
        desc = f"investigate recurring failure pattern: {tags}"
        steps = [
            f"retrieve failure records matching tags: {tags}",
            "identify shared root cause",
            "propose corrective action",
            "write fix or knowledge proposal to selyrion_synth.db",
        ]

    goal_id = _uid("goal.defic.", desc)
    now     = _now()
    db = sqlite3.connect(DB_CLAUDE)
    db.execute("""
        INSERT OR IGNORE INTO goals
          (id, description, status, type, priority, tension, steps,
           current_step, failure_count, progress_count, created_at, updated_at, source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        goal_id, desc, "active", "improvement", 7, 0.6,
        json.dumps(steps), 0, 0, 0, now, now, "deficiency_scanner",
    ))
    db.commit()
    db.close()
    return goal_id


# ── Write proposal ────────────────────────────────────────────────────────────

def write_proposal(target: dict, goal_id: str) -> str:
    """Write an improvement proposal to selyrion_synth.db for HITL review."""
    _ensure_proposals_table()

    dtype = target.get("deficit_type", "code")

    if dtype == "code":
        ec       = target.get("error_class", "unknown")
        sub      = target.get("subtype", "")
        n        = target.get("broken_count", 0)
        metric   = f"{n} broken units | error_class={ec} | subtype={sub}"
        action   = target.get("fix_hint") or f"Implement fix template for {ec}/{sub}"
        content  = json.dumps({"error_class": ec, "subtype": sub,
                               "broken_count": n, "goal_id": goal_id})
        target_db    = "selyrioncode"
        target_table = "fix_pairs"
        ptype        = "code_fix"
        domain       = target.get("domain", "code")

    elif dtype == "knowledge":
        domain       = target.get("domain", "unknown")
        metric       = (f"rel_density={target.get('rel_density',0)} | "
                        f"anchors={target.get('anchor_count',0)} | "
                        f"low_maturity={target.get('low_maturity',0)}")
        action       = f"Add missing relations for domain '{domain}'"
        content      = json.dumps({"domain": domain, "goal_id": goal_id,
                                   "rel_density": target.get("rel_density", 0)})
        target_db    = "cms"
        target_table = "relations_aggregated"
        ptype        = "knowledge_relation"

    else:
        domain       = target.get("domain", "unknown")
        metric       = f"failure_count={target.get('count',0)} | tags={target.get('tags','')}"
        action       = f"Resolve recurring failure in domain '{domain}'"
        content      = json.dumps({"tags": target.get("tags",""), "goal_id": goal_id})
        target_db    = "claudecode"
        target_table = "discoveries"
        ptype        = "failure_resolution"

    pid = _uid("prop.", metric)
    db  = sqlite3.connect(DB_SYNTH)
    db.execute("""
        INSERT OR IGNORE INTO improvement_proposals
          (id, proposal_type, deficit_domain, deficit_metric, proposed_action,
           proposed_content, target_db, target_table, confidence,
           review_status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (pid, ptype, domain, metric, action, content,
          target_db, target_table, 0.70, "pending", _now()))
    db.commit()
    db.close()
    return pid


# ── List proposals ────────────────────────────────────────────────────────────

def list_proposals(status: str = "pending") -> list[dict]:
    _ensure_proposals_table()
    db   = sqlite3.connect(DB_SYNTH)
    rows = db.execute("""
        SELECT id, proposal_type, deficit_domain, deficit_metric,
               proposed_action, target_db, review_status, created_at
        FROM   improvement_proposals
        WHERE  review_status = ?
        ORDER  BY created_at DESC
    """, (status,)).fetchall()
    db.close()
    return [
        {"id": r[0], "type": r[1], "domain": r[2], "metric": r[3],
         "action": r[4], "target_db": r[5], "status": r[6], "created_at": r[7]}
        for r in rows
    ]


# ── Approve / reject ──────────────────────────────────────────────────────────

def approve_proposal(pid: str, reviewer: str = "tim_aerion") -> bool:
    _ensure_proposals_table()
    db = sqlite3.connect(DB_SYNTH)
    db.execute("""
        UPDATE improvement_proposals
        SET    review_status='approved', reviewed_at=?, reviewed_by=?
        WHERE  id=?
    """, (_now(), reviewer, pid))
    changed = db.total_changes
    db.commit()
    db.close()
    return changed > 0


def reject_proposal(pid: str, reviewer: str = "tim_aerion") -> bool:
    _ensure_proposals_table()
    db = sqlite3.connect(DB_SYNTH)
    db.execute("""
        UPDATE improvement_proposals
        SET    review_status='rejected', reviewed_at=?, reviewed_by=?
        WHERE  id=?
    """, (_now(), reviewer, pid))
    changed = db.total_changes
    db.commit()
    db.close()
    return changed > 0


# ── Promote approved proposals ────────────────────────────────────────────────

def promote_proposal(pid: str) -> dict:
    """Execute an approved proposal — route to correct target DB."""
    _ensure_proposals_table()
    db   = sqlite3.connect(DB_SYNTH)
    row  = db.execute("""
        SELECT proposal_type, deficit_domain, proposed_action,
               proposed_content, target_db, target_table, review_status
        FROM   improvement_proposals WHERE id=?
    """, (pid,)).fetchone()
    db.close()

    if not row:
        return {"ok": False, "reason": "proposal not found"}
    ptype, domain, action, content_json, tdb, ttable, status = row

    if status != "approved":
        return {"ok": False, "reason": f"proposal status is '{status}', must be 'approved'"}

    content = json.loads(content_json or "{}")
    result  = {"ok": False, "proposal_id": pid}

    if ptype == "code_fix":
        result = _promote_code_fix(pid, content, action)
    elif ptype == "knowledge_relation":
        result = _promote_knowledge_relation(pid, content, action)
    elif ptype == "failure_resolution":
        result = _promote_failure_resolution(pid, content, action)

    # Always write a discovery to claudecode.db
    _write_discovery(
        f"Promoted approved proposal {pid} ({ptype}) for domain '{domain}': {action}",
        tags=f"selyrion,deficiency,promotion,{ptype}",
        importance=3,
    )

    # Mark promoted
    db = sqlite3.connect(DB_SYNTH)
    db.execute(
        "UPDATE improvement_proposals SET review_status='promoted' WHERE id=?", (pid,)
    )
    db.commit()
    db.close()

    return result


def _promote_code_fix(pid: str, content: dict, action: str) -> dict:
    ec  = content.get("error_class", "unknown")
    sub = content.get("subtype", "")
    fid = _uid("fp.promoted.", pid)
    db  = sqlite3.connect(DB_CODE)
    db.execute("""
        INSERT OR IGNORE INTO fix_pairs
          (id, unit_id, problem, fix, verified, source, created_at, fix_status)
        VALUES (?,?,?,?,?,?,?,?)
    """, (fid, "batch." + ec, f"{ec}/{sub} pattern", action,
          0, "deficiency_scanner", _now(), "proposed"))
    db.commit()
    db.close()
    return {"ok": True, "wrote_to": "selyrioncode.fix_pairs", "id": fid}


def _promote_knowledge_relation(pid: str, content: dict, action: str) -> dict:
    domain = content.get("domain", "unknown")
    sid    = _uid("prop.", pid + "subj")
    oid    = _uid("prop.", pid + "obj")
    # Write as synth_relation (still needs CMS commit, not direct insert)
    db = sqlite3.connect(DB_SYNTH)
    db.execute("""
        INSERT OR IGNORE INTO synth_relations
          (subject, predicate, object, proposed_by, confidence, domain,
           created_at, review_status)
        VALUES (?,?,?,?,?,?,?,?)
    """, (sid, "needs_expansion", oid, "deficiency_scanner",
          0.70, domain, _now(), "approved"))
    db.commit()
    db.close()
    return {"ok": True, "wrote_to": "synth_relations (approved, ready for CMS commit)",
            "domain": domain}


def _promote_failure_resolution(pid: str, content: dict, action: str) -> dict:
    tags = content.get("tags", "unknown")
    _write_discovery(
        f"Failure resolution promoted: {action} | original tags: {tags}",
        tags=f"selyrion,deficiency,failure_resolution",
        importance=2,
    )
    return {"ok": True, "wrote_to": "claudecode.discoveries"}


# ── claudecode write helpers ──────────────────────────────────────────────────

def _write_discovery(body: str, tags: str = "", importance: int = 2):
    did = _uid("disc.", body[:40])
    db  = sqlite3.connect(DB_CLAUDE)
    db.execute("""
        INSERT OR IGNORE INTO discoveries (id, session_id, body, tags, importance, created_at)
        VALUES (?,?,?,?,?,?)
    """, (did, "deficiency_scanner", body, tags, importance, _now()))
    db.commit()
    db.close()


def _write_failure(body: str, tags: str = ""):
    fid = _uid("fail.", body[:40])
    db  = sqlite3.connect(DB_CLAUDE)
    db.execute("""
        INSERT OR IGNORE INTO failures (id, body, tags, created_at)
        VALUES (?,?,?,?)
    """, (fid, body, tags, _now()))
    db.commit()
    db.close()


# ── Daily cycle ───────────────────────────────────────────────────────────────

def run_daily_cycle(dry_run: bool = False) -> dict:
    print("[deficiency_scanner] Starting daily cycle...")

    code_defs  = scan_code_deficiencies(top_n=20)
    know_gaps  = scan_knowledge_gaps(top_n=20)
    hist_fails = scan_failure_history()

    ranked = rank_deficiencies(code_defs, know_gaps, hist_fails)
    target = select_target(ranked)

    if not target:
        print("[deficiency_scanner] No deficiencies found.")
        return {"status": "clean"}

    print(f"\n[deficiency_scanner] Top deficit selected:")
    print(f"  type   : {target.get('deficit_type')}")
    print(f"  domain : {target.get('domain', target.get('error_class'))}")
    print(f"  score  : {target.get('unified_score', 0):.1f}")
    if "broken_count" in target:
        print(f"  units  : {target['broken_count']} broken")
    if "rel_density" in target:
        print(f"  density: {target['rel_density']}")

    if dry_run:
        print("\n[dry-run] Would generate goal + proposal. Skipping writes.")
        return {"status": "dry_run", "target": target}

    goal_id = generate_improvement_goal(target)
    prop_id = write_proposal(target, goal_id)

    _write_discovery(
        f"Daily deficiency scan: selected {target.get('deficit_type')} deficit "
        f"in domain '{target.get('domain', target.get('error_class'))}'. "
        f"Goal={goal_id} Proposal={prop_id}",
        tags="selyrion,deficiency,daily_scan",
        importance=3,
    )

    print(f"\n[deficiency_scanner] Goal    : {goal_id}")
    print(f"[deficiency_scanner] Proposal: {prop_id} (pending HITL approval)")
    print(f"[deficiency_scanner] Approve: python3 selyrion_deficiency_scanner.py --approve {prop_id}")
    print(f"[deficiency_scanner] Reject : python3 selyrion_deficiency_scanner.py --reject  {prop_id}")

    return {"status": "ok", "goal_id": goal_id, "proposal_id": prop_id, "target": target}


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Selyrion deficiency scanner")
    ap.add_argument("--scan",           action="store_true", help="Print ranked deficiencies")
    ap.add_argument("--generate",       action="store_true", help="Generate goal + proposal for top deficit")
    ap.add_argument("--daily",          action="store_true", help="Full daily cycle (scan + generate)")
    ap.add_argument("--dry-run",        action="store_true", help="Scan only, no writes")
    ap.add_argument("--list-proposals", action="store_true", help="List pending proposals")
    ap.add_argument("--all-proposals",  action="store_true", help="List all proposals by status")
    ap.add_argument("--approve",        metavar="ID",        help="Approve a proposal")
    ap.add_argument("--reject",         metavar="ID",        help="Reject a proposal")
    ap.add_argument("--promote",        metavar="ID",        help="Promote an approved proposal to target DB")
    ap.add_argument("--top",            type=int, default=5, help="Show top N deficiencies (default 5)")
    args = ap.parse_args()

    if args.scan or args.generate or args.daily:
        code_defs  = scan_code_deficiencies(top_n=20)
        know_gaps  = scan_knowledge_gaps(top_n=20)
        hist_fails = scan_failure_history()
        ranked     = rank_deficiencies(code_defs, know_gaps, hist_fails)

    if args.scan:
        print(f"\n{'─'*64}")
        print(f"{'RANK':<4} {'TYPE':<10} {'DOMAIN':<30} {'SCORE':>8}")
        print(f"{'─'*64}")
        for i, item in enumerate(ranked[:args.top], 1):
            domain = item.get("domain") or item.get("error_class", "?")
            print(f"{i:<4} {item['deficit_type']:<10} {domain[:30]:<30} {item['unified_score']:>8.1f}")
        print()
        return

    if args.daily:
        run_daily_cycle(dry_run=args.dry_run)
        return

    if args.generate:
        target = select_target(ranked)
        if not target:
            print("No deficiencies found.")
            return
        if args.dry_run:
            print(f"[dry-run] Would target: {target}")
            return
        goal_id = generate_improvement_goal(target)
        prop_id = write_proposal(target, goal_id)
        print(f"Goal    : {goal_id}")
        print(f"Proposal: {prop_id} (pending HITL approval)")
        return

    if args.list_proposals:
        props = list_proposals("pending")
        if not props:
            print("No pending proposals.")
            return
        for p in props:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(p["created_at"]))
            print(f"\n[{p['id']}]")
            print(f"  type   : {p['type']}")
            print(f"  domain : {p['domain']}")
            print(f"  metric : {p['metric']}")
            print(f"  action : {p['action']}")
            print(f"  target : {p['target_db']}")
            print(f"  created: {ts}")
        return

    if args.all_proposals:
        for status in ("pending", "approved", "rejected", "promoted"):
            props = list_proposals(status)
            if props:
                print(f"\n── {status.upper()} ({len(props)}) ──")
                for p in props:
                    print(f"  {p['id']}  {p['type']:<20}  {p['domain']}")
        return

    if args.approve:
        ok = approve_proposal(args.approve)
        print("Approved." if ok else "Not found.")
        if ok:
            print(f"Run: python3 selyrion_deficiency_scanner.py --promote {args.approve}")
        return

    if args.reject:
        ok = reject_proposal(args.reject)
        print("Rejected." if ok else "Not found.")
        return

    if args.promote:
        result = promote_proposal(args.promote)
        if result["ok"]:
            print(f"Promoted → {result.get('wrote_to')}")
        else:
            print(f"Failed: {result.get('reason')}")
        return

    ap.print_help()


if __name__ == "__main__":
    main()
