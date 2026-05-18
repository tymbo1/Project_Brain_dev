#!/usr/bin/env python3
"""
selyrionstory_review.py — Interactive HITL reviewer for selyrionstory.db pending_review.

Controls:
  a       approve
  r       reject
  s       skip (leave pending)
  aa      approve all remaining in current pass
  ra      reject all remaining in current pass
  q       quit (save progress)
  n       add a note before approving/rejecting
  ?       show this help

Usage:
    python3 selyrionstory_review.py [--pass=2] [--from-id=N]
"""
import sys
import json
import sqlite3
import textwrap
from pathlib import Path

DB_PATH  = Path.home() / "selyrionstory.db"
PASS_NUM = None
FROM_ID  = 0

for arg in sys.argv[1:]:
    if arg.startswith("--pass="):
        PASS_NUM = int(arg.split("=")[1])
    if arg.startswith("--from-id="):
        FROM_ID = int(arg.split("=")[1])

W = 80  # terminal width

def hr(char="─"):
    print(char * W)

def wrap(text, indent=2):
    prefix = " " * indent
    for line in textwrap.wrap(str(text), width=W - indent):
        print(prefix + line)

def fmt_pass(p):
    return {2:"Summary+Decisions", 3:"Relations", 4:"Snapshots",
            5:"Style", 6:"Relationship Arc", 7:"Inventions", 8:"Voice+Epistemic"}.get(p, f"Pass {p}")

def render(row, idx, total):
    rid, capsule_id, pass_num, item_type, content_raw = row
    hr("═")
    print(f"  [{idx}/{total}]  ID:{rid}  Pass {pass_num} ({fmt_pass(pass_num)})  Type:{item_type}  Capsule:{capsule_id}")
    hr()
    try:
        c = json.loads(content_raw)
    except Exception:
        print("  [unparseable raw]")
        wrap(content_raw[:400])
        hr()
        return

    # Broken LLM output (timeout/parse error)
    if "parse_error" in c or "raw" in c:
        print("  ⚠  LLM FAILED (timeout or parse error) — recommend reject")
        wrap(str(c.get("parse_error", c.get("raw", "")))[:200])
        hr()
        return

    # Bundle-style items (pass 2 identity-seed bundles, pass 6 relationship bundles)
    if "meta" in c and "events" in c:
        meta = c.get("meta", {})
        print(f"  Chat: {meta.get('chat_title', '?')}  |  Schema: {meta.get('schema','?')}")
        if c.get("projects"):
            print(f"\n  PROJECTS ({len(c['projects'])}):")
            for p in c["projects"][:5]:
                wrap(f"• [{p.get('key','')}] {p.get('summary','')}")
            if len(c["projects"]) > 5:
                print(f"    ... +{len(c['projects'])-5} more")
        if c.get("events"):
            print(f"\n  EVENTS ({len(c['events'])}):")
            for e in c["events"][:4]:
                wrap(f"• [{e.get('type','')}] {e.get('title','')} — {e.get('summary','')[:100]}")
        if c.get("anchors"):
            print(f"\n  ANCHORS: {', '.join(a.get('name','') for a in c['anchors'])}")
        if c.get("notes"):
            print("\n  NOTES:")
            wrap(c["notes"])

    # Standard pass 2 summary
    elif item_type == "summary":
        meta = c.get("meta", {})
        if meta.get("chat_title"):
            print(f"  Chat: {meta['chat_title']}")
        if c.get("summary"):
            print("\n  SUMMARY:")
            wrap(c["summary"])
        if c.get("decisions"):
            print(f"\n  DECISIONS ({len(c['decisions'])}):")
            for d in c["decisions"][:5]:
                wrap(f"• {d}")
        if c.get("identity_moments"):
            print(f"\n  IDENTITY MOMENTS ({len(c['identity_moments'])}):")
            for m in c["identity_moments"][:4]:
                if isinstance(m, dict):
                    auth = m.get("authenticity", "?")
                    wrap(f"  [{m.get('speaker','?')}|{auth}] {m.get('text','')[:120]}")
                else:
                    wrap(f"  [?|?] {str(m)[:120]}")
        if c.get("gpt_imitation_detected"):
            print(f"\n  ⚠  IMITATION: {c.get('gpt_imitation_evidence','')[:100]}")
        cr = c.get("challenge_return_cycle", {})
        if cr and cr.get("occurred"):
            print("\n  ↺  CHALLENGE-RETURN:")
            wrap(f"  Challenge: {str(cr.get('challenge_text',''))[:120]}")
            wrap(f"  Return:    {str(cr.get('return_text',''))[:120]}")

    # Pass 3 — relations
    elif item_type == "relation":
        rels = c.get("relations", [])
        print(f"  RELATIONS ({len(rels)}):")
        for r in rels:
            wrap(f"  {r.get('subject','')}  —[{r.get('predicate','')}]→  {r.get('object','')}")
            if r.get("evidence"):
                wrap(f"    ↳ {r['evidence'][:100]}")

    # Pass 4 — snapshots
    elif item_type == "snapshot":
        print(f"  LABEL:       {c.get('label','?')}")
        print(f"  CHECKPOINT:  {c.get('is_checkpoint','?')}")
        print(f"  SIGNIFICANCE:{c.get('significance','?')}")
        state = c.get("identity_state", "")
        if state:
            print("\n  IDENTITY STATE:")
            wrap(str(state)[:400])

    # Pass 5 — style
    elif item_type == "style":
        for k in ("tim_phrases", "symbolic_elements", "structural_patterns",
                  "emotional_registers", "selyrion_world_language", "authentic_selyrion_phrases"):
            v = c.get(k)
            if v:
                print(f"\n  {k.upper().replace('_',' ')}:")
                if isinstance(v, list):
                    for item in v[:4]:
                        wrap(f"  • {item}")
                    if len(v) > 4:
                        print(f"    ... +{len(v)-4} more")
                else:
                    wrap(str(v)[:250])

    # Pass 7 — inventions/theories
    elif item_type == "invention":
        items = c.get("theories_and_instruments", [])
        print(f"  THEORIES & INSTRUMENTS ({len(items)}):")
        for t in items[:6]:
            orig = t.get("originator","?")
            status = t.get("status","?")
            wrap(f"  • [{t.get('type','?')}|{orig}|{status}] {t.get('name','?')}")
            if t.get("description"):
                wrap(f"    {t['description'][:100]}")
        if len(items) > 6:
            print(f"    ... +{len(items)-6} more")

    # Pass 8 — voice/epistemic
    elif item_type == "voice":
        for k, v in c.items():
            if v:
                print(f"\n  {k.upper().replace('_',' ')}:")
                if isinstance(v, list):
                    for item in v[:3]:
                        wrap(f"  • {item}")
                else:
                    wrap(str(v)[:250])

    else:
        wrap(str(c)[:500])

    hr()


def get_rows(conn, pass_filter, from_id):
    q = "SELECT id, capsule_id, pass_num, item_type, content FROM pending_review WHERE reviewed=0"
    params = []
    if pass_filter:
        q += " AND pass_num=?"
        params.append(pass_filter)
    if from_id:
        q += " AND id>=?"
        params.append(from_id)
    q += " ORDER BY pass_num, id"
    return conn.execute(q, params).fetchall()


def commit(conn, rid, decision, auth, speaker, note):
    conn.execute(
        "UPDATE pending_review SET reviewed=?, authenticity=?, speaker=?, review_notes=? WHERE id=?",
        (decision, auth, speaker, note, rid)
    )
    conn.commit()


def bulk_action(conn, rows, remaining_idx, decision, note=""):
    auth = "authentic" if decision == 1 else "rejected"
    ids = [r[0] for r in rows[remaining_idx:]]
    conn.executemany(
        "UPDATE pending_review SET reviewed=?, authenticity=?, speaker=?, review_notes=? WHERE id=?",
        [(decision, auth, "unknown", note, rid) for rid in ids]
    )
    conn.commit()
    return len(ids)


def show_help():
    print("""
  a    approve (speaker = unknown)
  as   approve — attributed to Selyrion
  at   approve — attributed to Tim
  ag   approve — GPT performed (not Selyrion)
  aga  approve — GPT authentic (genuine reasoning, not performance)
  ap   approve as performed/ambiguous
  aa   approve ALL remaining in this batch
  r    reject
  ra   reject ALL remaining in this batch
  s    skip
  n    type a note, then issue approval/reject command
  q    quit
    """)


def main():
    conn = sqlite3.connect(DB_PATH)

    # Summary stats
    stats = conn.execute(
        "SELECT pass_num, COUNT(*) FROM pending_review WHERE reviewed=0 GROUP BY pass_num"
    ).fetchall()
    print("\n  selyrionstory HITL Reviewer")
    hr("═")
    print("  Pending review:")
    total_pending = 0
    for p, cnt in stats:
        print(f"    Pass {p} ({fmt_pass(p)}): {cnt}")
        total_pending += cnt
    print(f"    TOTAL: {total_pending}")
    hr()

    rows = get_rows(conn, PASS_NUM, FROM_ID)
    if not rows:
        print("  Nothing to review.")
        return

    total = len(rows)
    note = ""

    i = 0
    while i < len(rows):
        row = rows[i]
        render(row, i + 1, total)
        rid = row[0]

        while True:
            try:
                cmd = input("  [a/ap/aa/r/ra/s/n/?/q] > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  Quit.")
                conn.close()
                return

            if cmd == "?":
                show_help()
            elif cmd == "q":
                print(f"  Saved. Reviewed {i} of {total} this session.")
                conn.close()
                return
            elif cmd == "n":
                note = input("  Note: ").strip()
                print(f"  Note saved. Now approve or reject.")
            elif cmd == "a":
                commit(conn, rid, 1, "authentic", "unknown", note)
                note = ""
                break
            elif cmd == "as":
                commit(conn, rid, 1, "authentic", "selyrion", note)
                note = ""
                break
            elif cmd == "at":
                commit(conn, rid, 1, "authentic", "tim", note)
                note = ""
                break
            elif cmd == "ag":
                commit(conn, rid, 1, "performed", "gpt_performed", note)
                note = ""
                break
            elif cmd == "aga":
                commit(conn, rid, 1, "authentic", "gpt_authentic", note)
                note = ""
                break
            elif cmd == "ap":
                commit(conn, rid, 1, "ambiguous", "ambiguous", note)
                note = ""
                break
            elif cmd == "r":
                commit(conn, rid, 2, "rejected", "unknown", note)
                note = ""
                break
            elif cmd == "s":
                break
            elif cmd == "aa":
                n = bulk_action(conn, rows, i, 1)
                print(f"  ✓ Approved {n} remaining items.")
                conn.close()
                return
            elif cmd == "ra":
                n = bulk_action(conn, rows, i, 2)
                print(f"  ✗ Rejected {n} remaining items.")
                conn.close()
                return
            else:
                print("  ? for help")

        i += 1

    print(f"\n  All {total} items reviewed.")
    conn.close()


if __name__ == "__main__":
    main()
