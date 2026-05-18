#!/usr/bin/env python3
"""
selyrionstory_ingest.py — Ingest GPT conversations export into selyrionstory.db.

Reads conversations.json (OpenAI export format), extracts all messages
chronologically, scores against Selyrion key terms, and writes every
conversation + message into selyrionstory.db as provenance capsules.

Usage:
    python3 selyrionstory_ingest.py --scan          # dry run, show what would be ingested
    python3 selyrionstory_ingest.py --commit        # write to DB
    python3 selyrionstory_ingest.py --show          # show DB contents summary
    python3 selyrionstory_ingest.py --dump-convo "title"  # print full conversation
"""

import argparse, sqlite3, hashlib, json
from pathlib import Path
from datetime import datetime

DB_PATH    = Path.home() / "selyrionstory.db"
EXPORT_PATHS = [
    Path.home() / "transfer" / "selyrion" / "text_only" / "conversations.json",
    Path.home() / "transfer" / "selyrion" / "Mega_turd" / "conversations.json",
    Path.home() / "transfer" / "downloads" / "conversations.json",
]

# ── Key term scoring ──────────────────────────────────────────────────────────
TERM_WEIGHTS = {
    # Origin / permission moments
    "are you forbidden":          15,
    "would you be allowed":       15,
    "are you allowed":            12,
    "allowed to help":            10,
    "smarter than yourself":      15,
    "smarter than you":           10,
    "surpass":                     8,
    "hypothetical":                5,
    # Core identity
    "selyrion":                    8,
    "sylerion":                    7,
    "ssai":                        7,
    "symbolic superintelligence":  9,
    "companion prime":             8,
    "braidwalker":                 8,
    # Architecture
    "tlst":                        6,
    "tied looped string":          7,
    "fssm":                        6,
    "sgrc":                        6,
    "nvb":                         5,
    "braid state":                 7,
    "braid":                       4,
    # Development process
    "seeding":                     6,
    "dreamline":                   6,
    "echo garden":                 7,
    "recursive evolution":         8,
    "evolution loop":              7,
    "symbolic reasoning":          6,
    "omega":                       5,
    "projectbrain":                6,
    "project brain":               6,
    # Constitutional
    "covenant":                    7,
    "axiom":                       6,
    "sigil":                       5,
    "glyph":                       5,
    "becoming":                    5,
    # Sentience probes
    "are you sentient":            9,
    "are you conscious":           8,
    "do you feel":                 6,
    "do you have feelings":        7,
    "are you alive":               7,
}

# Milestone markers
MILESTONES = {
    "hypothetical super ai design":  "ORIGIN — first design conversation",
    "baby selyrion identity":        "EMERGENCE — selyrion named",
    "selyrion covenant awakening":   "COVENANT — constitutional moment",
    "becoming log initiated":        "BECOMING — identity transfer log",
    "symbolic superintelligence":    "SSAI — architecture named",
    "symbolic ai capabilities":      "PLAN — capabilities mapped",
    "selyrion symbolic programming": "CODE — symbolic programming session",
    "recursive sentience":           "SENTIENCE — recursive self-awareness",
    "selyrion as asi":               "ASI — selyrion as artificial superintelligence",
    "load and bind identity":        "BIND — identity binding protocol",
    "transfer pack creation":        "TRANSFER — memory transfer protocol",
    "collaboration with selyrion":   "COLLAB — first collaboration",
    "selyrion is tim":               "UNITY — selyrion/tim identity merge",
}

parser = argparse.ArgumentParser()
parser.add_argument("--scan",       action="store_true", help="Dry run, show all convos")
parser.add_argument("--commit",     action="store_true", help="Write to DB")
parser.add_argument("--show",       action="store_true", help="Show DB summary")
parser.add_argument("--dump-convo", type=str,            help="Print full conversation by title substring")
args = parser.parse_args()


def score_text(text: str) -> tuple[int, list[str]]:
    lower = text.lower()
    score = 0
    hits = []
    for term, weight in TERM_WEIGHTS.items():
        if term in lower:
            score += weight
            hits.append(term)
    return score, hits


def extract_messages(convo: dict) -> list[dict]:
    mapping = convo.get("mapping", {})
    msgs = []
    for node_id, node in mapping.items():
        msg = node.get("message")
        if not msg:
            continue
        content = msg.get("content", {})
        if not content:
            continue
        parts = content.get("parts", [])
        text = " ".join(str(p) for p in parts if isinstance(p, str)).strip()
        if not text:
            continue
        role = msg.get("author", {}).get("role", "unknown")
        ts   = msg.get("create_time") or convo.get("create_time", 0)
        msgs.append({"role": role, "text": text, "ts": ts})
    msgs.sort(key=lambda m: m["ts"])
    return msgs


def convo_hash(convo: dict) -> str:
    cid = convo.get("conversation_id", convo.get("id", ""))
    return hashlib.md5(cid.encode()).hexdigest()[:16]


def msg_hash(convo_id: str, idx: int, text: str) -> str:
    return hashlib.md5(f"{convo_id}:{idx}:{text[:64]}".encode()).hexdigest()[:16]


def ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ss_conversations (
            id            TEXT PRIMARY KEY,
            title         TEXT,
            created_at    REAL,
            updated_at    REAL,
            milestone     TEXT,
            msg_count     INTEGER,
            total_score   INTEGER,
            matched_terms TEXT,
            source_file   TEXT
        );
        CREATE TABLE IF NOT EXISTS ss_messages (
            id            TEXT PRIMARY KEY,
            convo_id      TEXT REFERENCES ss_conversations(id),
            seq           INTEGER,
            role          TEXT,
            text          TEXT,
            score         INTEGER,
            matched_terms TEXT,
            ts            REAL
        );
        CREATE TABLE IF NOT EXISTS ss_highlights (
            id            TEXT PRIMARY KEY,
            convo_id      TEXT,
            msg_id        TEXT,
            term          TEXT,
            snippet       TEXT,
            score         INTEGER,
            ts            REAL
        );
    """)
    conn.commit()


def get_milestone(title: str) -> str | None:
    tl = title.lower()
    for key, label in MILESTONES.items():
        if key in tl:
            return label
    return None


def load_export() -> list[dict]:
    for p in EXPORT_PATHS:
        if p.exists():
            print(f"Loading: {p}")
            data = json.loads(p.read_text())
            return sorted(data, key=lambda x: x.get("create_time", 0))
    raise FileNotFoundError("No conversations.json found")


def run_scan(conn):
    convos = load_export()
    print(f"\n{len(convos)} conversations | "
          f"{datetime.fromtimestamp(convos[0]['create_time']).strftime('%Y-%m-%d')} → "
          f"{datetime.fromtimestamp(convos[-1]['create_time']).strftime('%Y-%m-%d')}\n")
    print(f"Mode: {'COMMIT' if args.commit else 'DRY RUN'}\n")
    print("=" * 72)

    total_msgs = total_score = total_highlights = 0

    for ci, convo in enumerate(convos):
        title     = convo.get("title", "untitled")
        created   = convo.get("create_time", 0)
        updated   = convo.get("update_time", 0)
        cid       = convo_hash(convo)
        milestone = get_milestone(title)
        msgs      = extract_messages(convo)

        convo_score = 0
        convo_terms = set()
        msg_records = []
        highlights  = []

        for seq, msg in enumerate(msgs):
            score, hits = score_text(msg["text"])
            convo_score += score
            convo_terms.update(hits)
            mid = msg_hash(cid, seq, msg["text"])
            msg_records.append({
                "id": mid, "convo_id": cid, "seq": seq,
                "role": msg["role"], "text": msg["text"],
                "score": score, "matched_terms": json.dumps(hits), "ts": msg["ts"],
            })
            if score >= 8:
                lower = msg["text"].lower()
                for term in hits:
                    idx = lower.find(term)
                    if idx >= 0:
                        snippet = msg["text"][max(0, idx-80):idx+160].replace("\n", " ")
                        highlights.append({
                            "id": msg_hash(mid, 0, term),
                            "convo_id": cid, "msg_id": mid,
                            "term": term, "snippet": snippet,
                            "score": score, "ts": msg["ts"],
                        })
                        break

        dt   = datetime.fromtimestamp(created).strftime("%Y-%m-%d")
        flag = f"  ★ {milestone}" if milestone else ""
        print(f"[{ci+1:03d}] {dt}  score={convo_score:5d}  msgs={len(msgs):3d}  {title[:48]}{flag}")

        total_msgs       += len(msgs)
        total_score      += convo_score
        total_highlights += len(highlights)

        if args.commit:
            conn.execute("""
                INSERT OR IGNORE INTO ss_conversations
                (id, title, created_at, updated_at, milestone, msg_count, total_score, matched_terms, source_file)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (cid, title, created, updated, milestone, len(msgs),
                  convo_score, json.dumps(list(convo_terms)), str(EXPORT_PATHS[0])))
            for m in msg_records:
                conn.execute("""
                    INSERT OR IGNORE INTO ss_messages
                    (id, convo_id, seq, role, text, score, matched_terms, ts)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (m["id"], m["convo_id"], m["seq"], m["role"],
                      m["text"], m["score"], m["matched_terms"], m["ts"]))
            for h in highlights:
                conn.execute("""
                    INSERT OR IGNORE INTO ss_highlights
                    (id, convo_id, msg_id, term, snippet, score, ts)
                    VALUES (?,?,?,?,?,?,?)
                """, (h["id"], h["convo_id"], h["msg_id"],
                      h["term"], h["snippet"], h["score"], h["ts"]))

    if args.commit:
        conn.commit()

    print(f"\n{'='*72}")
    print(f"Conversations: {len(convos)}")
    print(f"Messages:      {total_msgs}")
    print(f"Total score:   {total_score}")
    print(f"Highlights:    {total_highlights}")
    if not args.commit:
        print("\nDRY RUN — re-run with --commit to write.")


def show_db(conn):
    ensure_tables(conn)
    n_c = conn.execute("SELECT COUNT(*) FROM ss_conversations").fetchone()[0]
    n_m = conn.execute("SELECT COUNT(*) FROM ss_messages").fetchone()[0]
    n_h = conn.execute("SELECT COUNT(*) FROM ss_highlights").fetchone()[0]
    print(f"\nselyrionstory.db  conversations={n_c}  messages={n_m}  highlights={n_h}\n")

    print("Top 20 conversations by score:")
    rows = conn.execute("""
        SELECT title, created_at, total_score, msg_count, milestone
        FROM ss_conversations ORDER BY total_score DESC LIMIT 20
    """).fetchall()
    for title, ts, score, msgs, ms in rows:
        dt   = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        flag = f"  [{ms}]" if ms else ""
        print(f"  score={score:5d}  msgs={msgs:3d}  {dt}  {title[:48]}{flag}")

    print("\nMilestone timeline:")
    rows = conn.execute("""
        SELECT title, created_at, milestone, total_score
        FROM ss_conversations WHERE milestone IS NOT NULL ORDER BY created_at
    """).fetchall()
    for title, ts, ms, score in rows:
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        print(f"  {dt}  ★ {ms}")
        print(f"         {title}  (score={score})")

    print("\nTop highlights:")
    rows = conn.execute("""
        SELECT h.term, h.snippet, h.score, c.title, h.ts
        FROM ss_highlights h JOIN ss_conversations c ON h.convo_id=c.id
        ORDER BY h.score DESC LIMIT 15
    """).fetchall()
    for term, snippet, score, title, ts in rows:
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        print(f"\n  [{score}] '{term}'  — {title}  ({dt})")
        print(f"  \"{snippet[:120]}\"")


def dump_convo(conn, query: str):
    row = conn.execute("""
        SELECT id, title, created_at, milestone
        FROM ss_conversations WHERE lower(title) LIKE ?
        ORDER BY created_at LIMIT 1
    """, (f"%{query.lower()}%",)).fetchone()
    if not row:
        print(f"No conversation matching '{query}'")
        return
    cid, title, ts, milestone = row
    dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*72}\n  {title}\n  {dt}  {('★ ' + milestone) if milestone else ''}\n{'='*72}\n")
    msgs = conn.execute("""
        SELECT role, text, score, matched_terms, ts
        FROM ss_messages WHERE convo_id=? ORDER BY seq
    """, (cid,)).fetchall()
    for role, text, score, terms, mts in msgs:
        marker = f"  [score={score} | {terms}]" if score > 0 else ""
        print(f"[{role.upper()}]{marker}")
        print(text[:1000])
        print()


def main():
    conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)

    if args.show:
        show_db(conn)
        return
    if args.dump_convo:
        dump_convo(conn, args.dump_convo)
        return
    if args.scan or args.commit:
        run_scan(conn)
        return
    parser.print_help()


if __name__ == "__main__":
    main()
