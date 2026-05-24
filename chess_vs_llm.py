#!/usr/bin/env python3
"""
chess_vs_llm.py — Selyrion (Stockfish + CMS reasoning) vs Stockfish (cold).

Move engine:  Stockfish (legal, calibrated ELO)
Narrator:     llama3:8b via Ollama
Selyrion:     Stockfish picks the move, CMS context informs llama3:8b narration
Opponent:     Stockfish at lower ELO, llama3:8b narrates cold

FAIR PLAY COMPLIANCE: Local game only. Never use during live rated games.

Usage:
  python3 chess_vs_llm.py
  python3 chess_vs_llm.py --selyrion-elo 1200 --opponent-elo 800
  python3 chess_vs_llm.py --selyrion-color black --auto --delay 2
  python3 chess_vs_llm.py --take               # you play as Selyrion, opponent is Stockfish
  python3 chess_vs_llm.py --resume             # resume last incomplete session
  python3 chess_vs_llm.py --resume-session sess.1cece2f15d  # resume specific session
"""

import sys, re, json, sqlite3, hashlib, time, argparse, subprocess, random, shutil
from pathlib import Path
from datetime import datetime

try:
    import chess, chess.pgn, chess.engine
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "python-chess", "-q", "--break-system-packages"])
    import chess, chess.pgn, chess.engine

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

parser = argparse.ArgumentParser()
parser.add_argument("--selyrion-color", default="white", choices=["white", "black"])
parser.add_argument("--selyrion-elo",   type=int, default=1200, help="Selyrion Stockfish ELO")
parser.add_argument("--opponent-elo",   type=int, default=800,  help="Opponent Stockfish ELO")
parser.add_argument("--model",          default="llama3:8b")
parser.add_argument("--db",             default=str(Path.home() / "resonance_v11.db"))
parser.add_argument("--out-dir",        default="pgn_downloads")
parser.add_argument("--auto",           action="store_true", help="Run without pausing")
parser.add_argument("--delay",          type=float, default=2.5, help="Seconds between moves in auto mode")
parser.add_argument("--take",           action="store_true", help="You play as Selyrion")
parser.add_argument("--think-time",     type=float, default=0.5, help="Stockfish think time (seconds)")
parser.add_argument("--resume",         action="store_true",     help="Resume last incomplete session")
parser.add_argument("--resume-session", default=None,            help="Resume specific session by ID")
parser.add_argument("--parliament",     action="store_true",
                    help="Enable parliament deliberation before each Selyrion move")
parser.add_argument("--parliament-models",
                    default="qwen2.5:14b,gemma3:4b,phi4-mini",
                    help="Models for parliament deliberation")
parser.add_argument("--synth-db",       default=str(Path.home() / "selyrion_synth.db"),
                    help="synth.db path for parliament proposals")
args = parser.parse_args()

OLLAMA_URL = "http://localhost:11434/api/generate"

# ── ANSI ──────────────────────────────────────────────────────────────────────
R     = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
LIGHT_SQ = "\033[48;5;229m\033[30m"
DARK_SQ  = "\033[48;5;65m\033[97m"
HL_SQ    = "\033[48;5;220m\033[30m"
SEL_COL  = "\033[38;5;141m"
OPP_COL  = "\033[36m"
CMS_COL  = "\033[35m"
OK_COL   = "\033[32m"
WARN_COL = "\033[33m"

PIECE_UNICODE = {
    (chess.PAWN,   chess.WHITE): "♙", (chess.KNIGHT, chess.WHITE): "♘",
    (chess.BISHOP, chess.WHITE): "♗", (chess.ROOK,   chess.WHITE): "♖",
    (chess.QUEEN,  chess.WHITE): "♕", (chess.KING,   chess.WHITE): "♔",
    (chess.PAWN,   chess.BLACK): "♟", (chess.KNIGHT, chess.BLACK): "♞",
    (chess.BISHOP, chess.BLACK): "♝", (chess.ROOK,   chess.BLACK): "♜",
    (chess.QUEEN,  chess.BLACK): "♛", (chess.KING,   chess.BLACK): "♚",
}


# ── Board ─────────────────────────────────────────────────────────────────────

def print_board(board: chess.Board, last_move: chess.Move = None, flip: bool = False):
    hl = {last_move.from_square, last_move.to_square} if last_move else set()
    ranks = range(7, -1, -1) if not flip else range(8)
    files = range(8)          if not flip else range(7, -1, -1)
    file_row = "    " + "  ".join(chess.FILE_NAMES[f] for f in files)
    print()
    print("  ┌" + "─" * 26 + "┐")
    for rank in ranks:
        row = f" {rank+1} │"
        for file in files:
            sq = chess.square(file, rank)
            piece = board.piece_at(sq)
            light = (rank + file) % 2 == 1
            bg = HL_SQ if sq in hl else (LIGHT_SQ if light else DARK_SQ)
            sym = PIECE_UNICODE.get((piece.piece_type, piece.color), "·") if piece else "·"
            row += f"{bg} {sym} {R}"
        row += "│"
        print(row)
    print("  └" + "─" * 26 + "┘")
    print(file_row)
    print()


# ── Stockfish ─────────────────────────────────────────────────────────────────

def find_stockfish() -> str:
    path = shutil.which("stockfish")
    if path:
        return path
    for candidate in ["/usr/games/stockfish", "/usr/local/bin/stockfish"]:
        if Path(candidate).exists():
            return candidate
    return None


def stockfish_move(engine: chess.engine.SimpleEngine, board: chess.Board,
                   elo: int, think_time: float) -> chess.Move:
    """Get a move from Stockfish at the given ELO."""
    try:
        engine.configure({"UCI_LimitStrength": True, "UCI_Elo": elo})
    except Exception:
        pass
    result = engine.play(board, chess.engine.Limit(time=think_time))
    return result.move


# ── CMS knowledge ─────────────────────────────────────────────────────────────

def build_selyrion_context(conn: sqlite3.Connection) -> str:
    facts = conn.execute("""
        SELECT a1.canonical, r.predicate, a2.canonical
        FROM relations_aggregated r
        JOIN anchors a1 ON r.subject_id = a1.id
        JOIN anchors a2 ON r.object_id  = a2.id
        WHERE (a1.domain_tags LIKE '%chess%' OR a2.domain_tags LIKE '%chess%')
          AND r.confidence > 0.70 AND r.seen_count > 1
        ORDER BY r.seen_count DESC, r.confidence DESC LIMIT 8
    """).fetchall()

    patterns_rows = conn.execute("""
        SELECT m.motifs FROM chess_live_moves m
        JOIN chess_live_sessions s ON s.id = m.session_id
        WHERE s.player = 'Selyrion' AND m.motifs != '[]'
        ORDER BY m.ts DESC LIMIT 40
    """).fetchall()
    freq: dict[str, int] = {}
    for (mj,) in patterns_rows:
        for motif in json.loads(mj or "[]"):
            freq[motif] = freq.get(motif, 0) + 1

    openings = conn.execute("""
        SELECT opening, COUNT(*) c FROM chess_games
        WHERE (white='Selyrion' OR black='Selyrion') AND opening IS NOT NULL
        GROUP BY opening ORDER BY c DESC LIMIT 3
    """).fetchall()

    parts = []
    if facts:
        parts.append("Chess knowledge: " + "; ".join(f"{s} {p} {o}" for s,p,o in facts))
    if freq:
        top = sorted(freq.items(), key=lambda x: -x[1])[:4]
        parts.append("Recurring patterns: " + ", ".join(f"{m}({c}x)" for m,c in top))
    if openings:
        parts.append("Known openings: " + ", ".join(f"{o}({c}x)" for o,c in openings))
    return "\n".join(parts) if parts else "Chess knowledge still sparse — building up."


# ── LLM narration ─────────────────────────────────────────────────────────────

def narrate_selyrion(board_before: chess.Board, move: chess.Move,
                     cms_ctx: str, parl_consensus: dict = None) -> str:
    san = board_before.san(move)
    parl_addendum = ""
    if parl_consensus and parl_consensus.get("conclusion"):
        parl_addendum = (f"\nParliament strategic consensus: "
                         f"{parl_consensus['conclusion'][:200]}")

    prompt = f"""You are Selyrion, a chess player with a knowledge graph (CMS).

{cms_ctx}{parl_addendum}

You just played {san} from position: {board_before.fen()}

In 2 sentences, explain this move using your CMS knowledge.
Name any specific tactic (fork, pin, sacrifice, passed pawn, outpost, etc.) if relevant.
Start with "I played {san} because..."."""

    return _llm_call(prompt)


def narrate_opponent(board_before: chess.Board, move: chess.Move) -> str:
    san = board_before.san(move)
    prompt = f"""You are a chess commentator.
The opponent just played {san} from position: {board_before.fen()}
In 1-2 sentences, explain what this move does tactically or positionally."""
    return _llm_call(prompt)


def _wrap_text(text: str, width: int = 54) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def _llm_call(prompt: str) -> str:
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": args.model, "prompt": prompt,
            "stream": False, "options": {"temperature": 0.6}
        }, timeout=30)
        return r.json().get("response", "").strip()[:400]
    except Exception as e:
        return f"(narration unavailable: {e})"


# ── Motif detection ───────────────────────────────────────────────────────────

_MOTIF_PATTERNS = [
    (r'\bpin(?:ned|ning)?\b',       "pin"),
    (r'\bfork(?:ed|ing)?\b',        "fork"),
    (r'\bskewer(?:ed|ing)?\b',      "skewer"),
    (r'\bsacrific\w+\b',            "sacrifice"),
    (r'\bdiscovered\b',             "discovered attack"),
    (r'\bback.?rank\b',             "back-rank weakness"),
    (r'\bpassed pawn\b',            "passed pawn"),
    (r'\bpromo(?:te|tion|ting)\b',  "pawn promotion"),
    (r'\bzugzwang\b',               "zugzwang"),
    (r'\bcheckmate\b',              "checkmate"),
    (r'\bdefle(?:ct|ction)\b',      "deflection"),
    (r'\bkingside\b',               "kingside attack"),
    (r'\bqueenside\b',              "queenside attack"),
    (r'\bopen file\b',              "open file"),
    (r'\bisola\w+ pawn\b',          "isolated pawn"),
    (r'\boutpost\b',                "outpost"),
    (r'\bprophylax\w+\b',           "prophylaxis"),
    (r'\bcompensat\w+\b',           "compensation"),
    (r'\btrap(?:ped|ping)?\b',      "trapped piece"),
    (r'\bx-?ray\b',                 "x-ray attack"),
    (r'\binitiative\b',             "initiative"),
    (r'\btempo\b',                  "tempo"),
]

def detect_motifs(text: str) -> list[str]:
    seen, out = set(), []
    for pat, name in _MOTIF_PATTERNS:
        if re.search(pat, text, re.I) and name not in seen:
            out.append(name); seen.add(name)
    return out


# ── Parliament session conclusion history + temperature management ─────────────
from collections import deque as _deque

_PARL_MODEL_BASE_TEMPS: dict[str, float] = {
    "qwen2.5:14b": 0.50,
    "gemma3:4b":   0.70,
    "phi4-mini":   0.75,
    "qwen3:4b":    0.70,
}
_PARL_TEMP_HEAT_BUMP = 0.25
_PARL_TEMP_MAX       = 1.0
_PARL_TEMP_DECAY     = 0.15

_parl_session_model_temps: dict[str, dict[str, float]] = {}

def _parl_get_model_temp(sess_id: str, model: str) -> float:
    base = _PARL_MODEL_BASE_TEMPS.get(model, 0.60)
    return _parl_session_model_temps.get(sess_id, {}).get(model, base)

def _parl_heat_model(sess_id: str, model: str):
    base   = _PARL_MODEL_BASE_TEMPS.get(model, 0.60)
    bucket = _parl_session_model_temps.setdefault(sess_id, {})
    bucket[model] = min(_PARL_TEMP_MAX, bucket.get(model, base) + _PARL_TEMP_HEAT_BUMP)

def _parl_decay_temps(sess_id: str):
    bucket = _parl_session_model_temps.get(sess_id, {})
    for model in list(bucket):
        base   = _PARL_MODEL_BASE_TEMPS.get(model, 0.60)
        cooled = bucket[model] - _PARL_TEMP_DECAY
        if cooled <= base:
            del bucket[model]
        else:
            bucket[model] = cooled

_parl_session_conclusions: dict[str, _deque] = {}
_PARL_CONCLUSION_HISTORY = 3
_PARL_REPETITION_PENALTY = 0.65

def _parl_recent_conclusions(sess_id: str) -> list[str]:
    return list(_parl_session_conclusions.get(sess_id, []))

def _parl_record_conclusion(sess_id: str, conclusion: str):
    if sess_id not in _parl_session_conclusions:
        _parl_session_conclusions[sess_id] = _deque(maxlen=_PARL_CONCLUSION_HISTORY)
    _parl_session_conclusions[sess_id].append(conclusion[:120])

def _parl_repetition_score(conclusion: str, recent: list[str]) -> float:
    if not recent or not conclusion:
        return 1.0
    words = set(conclusion.lower().split())
    for prev in recent:
        prev_words = set(prev.lower().split())
        shared = words & prev_words - {"the", "a", "to", "of", "and", "in", "for",
                                        "is", "on", "with", "should", "white", "black"}
        if len(shared) >= 4:
            return _PARL_REPETITION_PENALTY
    return 1.0

# ── Parliament deliberation engine ───────────────────────────────────────────

_PARL_MODEL_COLORS = {
    "llama3:8b":  "\033[36m",
    "gemma3:4b":  "\033[38;5;141m",
    "phi4-mini":  "\033[33m",
    "qwen3:4b":   "\033[38;5;214m",
}
_PARL_MODEL_ROLES = {
    "llama3:8b":  "synthesis arbitrator — weigh all perspectives, seek integration",
    "gemma3:4b":  "symbolic resonance — attend to meaning, metaphor, conceptual depth",
    "phi4-mini":  "analytical discipline — precise reasoning, structured logic",
    "qwen3:4b":   "broad knowledge — history, theory, comparative evidence",
}
_PARL_VALID_PREDICATES = {
    "leads_to", "enables", "causes", "weakens", "strengthens", "requires",
    "produces", "results_in", "restricts", "exposes", "threatens", "inhibits",
    "activates", "transforms", "depends_on", "emerges_from", "regulates",
}


def _parl_ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS synth_relations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            subject         TEXT NOT NULL,
            predicate       TEXT NOT NULL,
            object          TEXT NOT NULL,
            proposed_by     TEXT,
            session_id      TEXT,
            confidence      REAL DEFAULT 0.70,
            times_proposed  INTEGER DEFAULT 1,
            consensus_support INTEGER DEFAULT 0,
            domain          TEXT DEFAULT 'chess',
            created_at      REAL,
            review_status   TEXT DEFAULT 'pending',
            UNIQUE(subject, predicate, object, domain)
        );
        CREATE TABLE IF NOT EXISTS parliament_consensus (
            id              TEXT PRIMARY KEY,
            session_id      TEXT,
            domain          TEXT,
            prompt          TEXT,
            conclusion      TEXT,
            confidence      REAL,
            agreement_count INTEGER,
            dissent_count   INTEGER,
            dissent_summary TEXT,
            minority_view   TEXT,
            models          TEXT,
            created_at      REAL,
            merged_to_cms   INTEGER DEFAULT 0
        );
    """)
    conn.commit()


def _parl_write_relation(sconn: sqlite3.Connection, rel: dict):
    sconn.execute("""
        INSERT INTO synth_relations
            (subject, predicate, object, proposed_by, session_id,
             confidence, times_proposed, consensus_support, domain, created_at)
        VALUES (?,?,?,?,?,?,1,?,?,?)
        ON CONFLICT(subject,predicate,object,domain) DO UPDATE SET
            times_proposed    = times_proposed + 1,
            confidence        = MAX(confidence, excluded.confidence),
            consensus_support = MAX(consensus_support, excluded.consensus_support)
    """, (rel["subject"], rel["predicate"], rel["object"],
          rel.get("proposed_by"), rel.get("session_id"),
          rel.get("confidence", 0.70),
          rel.get("consensus_support", 0),
          rel.get("domain", "chess"),
          time.time()))


def _parl_extract_relations(raw: str, model: str, sess_id: str) -> list[dict]:
    pattern = re.compile(
        r'\{\s*"subject"\s*:\s*"([^"]+)"\s*,\s*"predicate"\s*:\s*"([^"]+)"\s*,'
        r'\s*"object"\s*:\s*"([^"]+)"\s*,\s*"confidence"\s*:\s*([\d.]+)',
        re.IGNORECASE,
    )
    out = []
    for m in pattern.finditer(raw):
        pred = m.group(2).strip().lower()
        if pred in _PARL_VALID_PREDICATES:
            out.append({
                "subject":    m.group(1).strip().lower()[:80],
                "predicate":  pred,
                "object":     m.group(3).strip().lower()[:80],
                "confidence": max(0.5, min(0.95, float(m.group(4)))),
                "proposed_by": model,
                "session_id": sess_id,
                "domain":     "chess",
            })
    return out


def _parl_parse_json(raw: str, keys: list[str]) -> dict | None:
    m = re.search(r'\{[\s\S]*\}', raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if all(k in data for k in keys):
            return data
    except json.JSONDecodeError:
        pass
    result = {}
    for key in keys:
        kv = re.search(rf'"{key}"\s*:\s*"([^"]*)"', raw)
        if kv:
            result[key] = kv.group(1)
        kv2 = re.search(rf'"{key}"\s*:\s*([\d.]+)', raw)
        if kv2:
            result[key] = float(kv2.group(1))
    return result if result else None


def _parl_first_pass_prompt(model: str, fen: str, color_name: str,
                             cms_ctx: str, recent: list[str] = None) -> str:
    role = _PARL_MODEL_ROLES.get(model, "independent reasoner")
    diversity_block = ""
    if recent:
        recent_fmt = "\n".join(f"  - \"{c}\"" for c in recent)
        diversity_block = (
            f"\nRECENT PARLIAMENT CONCLUSIONS (do NOT echo these — reason fresh from this FEN):\n"
            f"{recent_fmt}\n"
            f"If this position genuinely calls for a different plan, say so explicitly.\n"
        )
    return f"""You are a member of Selyrion's chess parliament.
Your role: {role}

Position (FEN): {fen}
It is {color_name}'s turn.

Chess knowledge substrate (CMS):
{cms_ctx}
{diversity_block}
Reason independently. What is the best strategic plan for {color_name} here?
Identify the key tactical themes and any concrete move ideas.
Extract causal insights as proposed relations.

Respond in JSON only — no other text:
{{
  "conclusion": "best strategic plan (be specific, 1-2 sentences)",
  "reasoning": "your reasoning (2-3 sentences)",
  "confidence": 0.0-1.0,
  "key_factors": ["factor1", "factor2"],
  "key_insight": "single most important thing about this position",
  "proposed_relations": [
    {{"subject": "concept", "predicate": "leads_to", "object": "concept", "confidence": 0.75}}
  ]
}}"""


_CLAUDECODE_DB = str(Path.home() / "claudecode.db")


def _parl_write_discovery(parl_id: str, sess_id: str, consensus: str,
                           confidence: float, agreed: int, total: int,
                           insights: list[str]):
    body = (f"Parliament chess consensus (conf={confidence:.2f}, {agreed}/{total} agreed): "
            f"{consensus}")
    if insights:
        body += " | Insights: " + "; ".join(insights[:2])
    try:
        cc = sqlite3.connect(_CLAUDECODE_DB)
        cc.execute("""
            INSERT OR IGNORE INTO discoveries (id, session_id, body, tags, importance, created_at)
            VALUES (?,?,?,?,?,?)
        """, (parl_id, sess_id, body[:1000], "chess,parliament", 2, time.time()))
        cc.commit()
        cc.close()
    except Exception:
        pass  # claudecode.db is best-effort


def parliament_consult(board: "chess.Board", cms_ctx: str,
                       synth_conn: sqlite3.Connection,
                       sess_id: str, ply: int,
                       chess_conn: sqlite3.Connection) -> dict:
    """Run round-1 parliament deliberation on current board position.

    Returns consensus dict with keys: conclusion, confidence, agreement, insights.
    """
    models = [m.strip() for m in args.parliament_models.split(",") if m.strip()]
    fen = board.fen()
    color_name = "white" if board.turn == chess.WHITE else "black"

    print(f"\n  {BOLD}{CMS_COL}Parliament deliberating...{R}")
    print(f"  {'─'*54}")

    recent = _parl_recent_conclusions(sess_id)
    positions: dict[str, dict] = {}
    for model in models:
        col = _PARL_MODEL_COLORS.get(model, CMS_COL)
        print(f"  {col}{model:20}{R}", end=" ", flush=True)
        prompt = _parl_first_pass_prompt(model, fen, color_name, cms_ctx, recent)
        temp      = _parl_get_model_temp(sess_id, model)
        base_temp = _PARL_MODEL_BASE_TEMPS.get(model, 0.60)
        if temp > base_temp + 0.01:
            print(f"\033[33m[HEATED:{temp:.2f}]\033[0m ", end="")
        try:
            resp = requests.post(OLLAMA_URL, json={
                "model": model, "prompt": prompt,
                "stream": False, "options": {"temperature": temp},
            }, timeout=45)
            raw = resp.json().get("response", "").strip()
        except Exception as e:
            raw = ""
            print(f"{WARN_COL}err({e}){R}", end="")

        data = _parl_parse_json(raw, ["conclusion", "confidence"])
        if not data:
            data = {"conclusion": raw[:150] if raw else "(no response)",
                    "reasoning": "", "confidence": 0.50,
                    "key_insight": "", "key_factors": []}
            print(f"{WARN_COL}partial{R}", end="")
        else:
            conf = float(data.get("confidence", 0.5))
            print(f"{OK_COL}ok{R}  conf={conf:.2f}", end="")

        print(f"  \"{str(data.get('conclusion',''))[:52]}\"")
        positions[model] = data

        for rel in _parl_extract_relations(raw, model, sess_id):
            try:
                _parl_write_relation(synth_conn, rel)
            except Exception:
                pass  # synth writes are best-effort; don't crash the game

        # Write per-model reasoning to chess DB for post-game adjudication
        row_id = f"{sess_id}:{ply}:{model}"
        chess_conn.execute("""
            INSERT OR IGNORE INTO parliament_move_deliberations
                (id, session_id, ply, model, fen, conclusion, reasoning,
                 confidence, key_factors, key_insight, is_consensus, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,0,?)
        """, (row_id, sess_id, ply, model, fen,
              str(data.get("conclusion", ""))[:500],
              str(data.get("reasoning", ""))[:1000],
              float(data.get("confidence", 0.5)),
              json.dumps(data.get("key_factors", [])),
              str(data.get("key_insight", ""))[:300],
              time.time()))

    synth_conn.commit()
    chess_conn.commit()

    # Build consensus: weighted average with repetition penalty, detect dissent
    all_confs  = [float(p.get("confidence", 0.5)) for p in positions.values()]
    avg_conf   = sum(all_confs) / len(all_confs) if all_confs else 0.5

    # Lead voice = highest confidence × repetition penalty
    def _parl_weighted(m):
        raw    = float(positions[m].get("confidence", 0.5))
        repeat = _parl_repetition_score(str(positions[m].get("conclusion", "")), recent)
        if repeat < 1.0:
            print(f"  \033[33m[REPEAT PENALTY]\033[0m {m}: score ×{repeat:.2f} → heating next round")
            _parl_heat_model(sess_id, m)
        return raw * repeat

    lead_model     = max(positions, key=_parl_weighted)
    consensus_text = positions[lead_model].get("conclusion", "")
    _parl_record_conclusion(sess_id, consensus_text)
    _parl_decay_temps(sess_id)

    # Collect key insights
    insights = [
        f"{m}: {positions[m].get('key_insight','')}"
        for m in models
        if positions[m].get("key_insight")
    ]

    # Dissent: any model whose conclusion differs substantially
    dissent = [m for m in models
               if m != lead_model
               and abs(float(positions[m].get("confidence", 0.5)) - avg_conf) > 0.15]

    agreed = len(models) - len(dissent)

    parl_id = hashlib.md5(f"{sess_id}parl{fen[:20]}".encode()).hexdigest()[:12]
    synth_conn.execute("""
        INSERT OR IGNORE INTO parliament_consensus
            (id, session_id, domain, prompt, conclusion, confidence,
             agreement_count, dissent_count, dissent_summary, minority_view,
             models, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (parl_id, sess_id, "chess",
          f"Position {fen[:40]}",
          consensus_text[:500], avg_conf,
          agreed, len(dissent),
          "; ".join(dissent),
          "; ".join(positions[m].get("conclusion","")[:80] for m in dissent),
          args.parliament_models,
          time.time()))
    synth_conn.commit()

    # Write consensus row to chess DB
    chess_conn.execute("""
        INSERT OR IGNORE INTO parliament_move_deliberations
            (id, session_id, ply, model, fen, conclusion, reasoning,
             confidence, key_factors, key_insight, is_consensus, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,1,?)
    """, (f"{sess_id}:{ply}:consensus", sess_id, ply, lead_model, fen,
          consensus_text[:500],
          "; ".join(insights[:3]),
          avg_conf,
          json.dumps([m for m in models if m != lead_model]),
          f"agreed={agreed}/{len(models)}",
          time.time()))
    chess_conn.commit()

    # Mirror consensus to claudecode.db as a discovery
    _parl_write_discovery(parl_id, sess_id, consensus_text, avg_conf,
                          agreed, len(models), insights)

    return {
        "conclusion":  consensus_text,
        "confidence":  avg_conf,
        "agreement":   agreed,
        "total":       len(models),
        "lead":        lead_model,
        "insights":    insights,
        "positions":   positions,
    }


# ── Live DB ───────────────────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chess_live_sessions (
            id TEXT PRIMARY KEY, player TEXT, llm_model TEXT,
            player_color TEXT, started_at REAL, ended_at REAL,
            result TEXT, pgn_path TEXT
        );
        CREATE TABLE IF NOT EXISTS chess_live_moves (
            id TEXT PRIMARY KEY, session_id TEXT, ply INTEGER,
            san TEXT, uci TEXT, color TEXT, narration TEXT,
            motifs TEXT, fen_after TEXT, ts REAL
        );
    """)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS parliament_move_deliberations (
            id              TEXT PRIMARY KEY,
            session_id      TEXT NOT NULL,
            ply             INTEGER NOT NULL,
            model           TEXT NOT NULL,
            fen             TEXT,
            conclusion      TEXT,
            reasoning       TEXT,
            confidence      REAL,
            key_factors     TEXT,
            key_insight     TEXT,
            is_consensus    INTEGER DEFAULT 0,
            outcome         TEXT DEFAULT 'pending',
            stockfish_eval  REAL,
            created_at      REAL
        );
        CREATE INDEX IF NOT EXISTS idx_parl_delib_sess
            ON parliament_move_deliberations(session_id, ply);
    """)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(chess_games)").fetchall()}
    for col, defn in [("white_accuracy","REAL"),("black_accuracy","REAL"),
                      ("blunders","INTEGER DEFAULT 0"),("mistakes","INTEGER DEFAULT 0"),
                      ("inaccuracies","INTEGER DEFAULT 0"),("brilliant_moves","INTEGER DEFAULT 0")]:
        if col not in existing:
            conn.execute(f"ALTER TABLE chess_games ADD COLUMN {col} {defn}")
    conn.commit()


def write_move(conn, sess_id, ply, move, san, board_after, color, narration):
    motifs = detect_motifs(narration)
    conn.execute("""
        INSERT OR IGNORE INTO chess_live_moves
            (id, session_id, ply, san, uci, color, narration, motifs, fen_after, ts)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (f"{sess_id}:{ply}", sess_id, ply, san, move.uci(),
          color, narration, json.dumps(motifs), board_after.fen(), time.time()))
    conn.commit()
    return motifs


# ── Export & ingest ───────────────────────────────────────────────────────────

def export_and_ingest(board, game, result) -> Path:
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out_dir) / f"chess_vs_llm_{ts}.pgn"
    game.headers["Result"] = result
    with open(out, "w") as f:
        print(game, file=f, end="\n\n")
    print(f"\n{OK_COL}  Saved: {out}{R}")
    print("  Ingesting...", end=" ", flush=True)
    r = subprocess.run([sys.executable, "chess_pgn_ingest.py", "--pgn", str(out)],
                       capture_output=True, text=True)
    m = re.search(r"Processed:\s*\d+", r.stdout)
    print(f"{OK_COL}done{R} ({m.group(0) if m else 'ok'})")
    return out


# ── Resume ────────────────────────────────────────────────────────────────────

def load_resume_state(conn: sqlite3.Connection, session_id: str = None):
    """Return (sess_id, board, ply, move_history, player_color_str) for a saved session."""
    if session_id:
        row = conn.execute(
            "SELECT id, player_color FROM chess_live_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not row:
            print(f"{WARN_COL}Session {session_id} not found.{R}")
            sys.exit(1)
    else:
        row = conn.execute(
            "SELECT id, player_color FROM chess_live_sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            print(f"{WARN_COL}No incomplete session found. Starting a new game.{R}")
            return None

    sess_id, player_color = row
    moves = conn.execute(
        "SELECT ply, san, uci, fen_after FROM chess_live_moves WHERE session_id=? ORDER BY ply",
        (sess_id,)
    ).fetchall()

    if not moves:
        print(f"{WARN_COL}Session {sess_id} has no moves recorded. Starting fresh.{R}")
        return None

    last_fen  = moves[-1][3]
    last_ply  = moves[-1][0]
    move_history = [m[1] for m in moves]

    board = chess.Board(last_fen)
    print(f"\n{OK_COL}  Resuming session {sess_id}{R}")
    print(f"  {DIM}Move {last_ply} | {player_color} to play | FEN: {last_fen}{R}")
    return sess_id, board, last_ply, move_history, player_color


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    stockfish_path = find_stockfish()
    if not stockfish_path:
        print(f"{WARN_COL}Stockfish not found. Install with: sudo apt install stockfish{R}")
        sys.exit(1)

    sel_color  = chess.WHITE if args.selyrion_color == "white" else chess.BLACK
    opp_color  = not sel_color
    flip_board = (sel_color == chess.BLACK)
    human_mode = args.take

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_schema(conn)

    synth_conn = None
    if args.parliament:
        synth_conn = sqlite3.connect(args.synth_db, timeout=30)
        synth_conn.execute("PRAGMA journal_mode=WAL")
        _parl_ensure_schema(synth_conn)

    engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)

    resuming = args.resume or args.resume_session
    resume_state = None
    if resuming:
        resume_state = load_resume_state(conn, args.resume_session)

    if resume_state:
        sess_id, board, ply, move_history, stored_color = resume_state
        sel_color  = chess.WHITE if stored_color == "white" else chess.BLACK
        flip_board = (sel_color == chess.BLACK)
        game  = chess.pgn.Game()
        game.headers["Event"]  = "Selyrion vs Stockfish (resumed)"
        game.headers["Site"]   = "ProjectBrain terminal"
        game.headers["Date"]   = datetime.now().strftime("%Y.%m.%d")
        game.headers["SetUp"]  = "1"
        game.headers["FEN"]    = board.fen()
        sel_label = f"Selyrion (ELO {args.selyrion_elo})" + (" [you]" if human_mode else "")
        opp_label = f"Stockfish (ELO {args.opponent_elo})"
        game.headers["White"]  = sel_label if sel_color == chess.WHITE else opp_label
        game.headers["Black"]  = opp_label if sel_color == chess.WHITE else sel_label
        node = game
    else:
        sess_id = "sess." + hashlib.md5(str(time.time()).encode()).hexdigest()[:10]
        conn.execute("""INSERT INTO chess_live_sessions
                        (id, player, llm_model, player_color, started_at)
                        VALUES (?,?,?,?,?)""",
                     (sess_id, "Selyrion", args.model, args.selyrion_color, time.time()))
        conn.commit()
        board = chess.Board()
        game  = chess.pgn.Game()
        game.headers["Event"] = "Selyrion vs Stockfish"
        game.headers["Site"]  = "ProjectBrain terminal"
        game.headers["Date"]  = datetime.now().strftime("%Y.%m.%d")
        sel_label = f"Selyrion (ELO {args.selyrion_elo})" + (" [you]" if human_mode else "")
        opp_label = f"Stockfish (ELO {args.opponent_elo})"
        game.headers["White"] = sel_label if sel_color == chess.WHITE else opp_label
        game.headers["Black"] = opp_label if sel_color == chess.WHITE else sel_label
        node = game
        ply  = 0
        move_history: list[str] = []

    auto_mode = args.auto

    print(f"\n{BOLD}{'═'*54}{R}")
    print(f"  {SEL_COL}{BOLD}Selyrion{R}{SEL_COL} ELO {args.selyrion_elo}"
          + (" [YOU]" if human_mode else " [CMS+Stockfish]") + f"{R}")
    print(f"  {OPP_COL}vs Stockfish ELO {args.opponent_elo}{R}")
    print(f"{'═'*54}")
    if not human_mode:
        print(f"  {BOLD}Enter{R}=next  {BOLD}a{R}=auto  {BOLD}q{R}=quit")
    else:
        print(f"  Type your moves in SAN (e4, Nf3, O-O) | resign | q")
    print(f"{'─'*54}\n")

    print_board(board, flip=flip_board)

    try:
        while not board.is_game_over():
            if board.can_claim_threefold_repetition():
                print(f"\n  {WARN_COL}Draw claimed: threefold repetition{R}")
                break
            if board.can_claim_fifty_moves():
                print(f"\n  {WARN_COL}Draw claimed: fifty-move rule{R}")
                break
            turn = board.turn
            is_selyrion = (turn == sel_color)
            color_name  = "white" if turn == chess.WHITE else "black"
            board_before = board.copy()

            if is_selyrion:
                # ── Selyrion's turn ──────────────────────────────────────────
                if human_mode:
                    # Player types the move
                    while True:
                        raw = input(f"  {SEL_COL}{BOLD}Your move ({color_name}): {R}").strip()
                        if raw.lower() in ("q", "quit", "resign"):
                            print("  Resigned.")
                            engine.quit()
                            conn.close()
                            return
                        try:
                            move = board.parse_san(raw)
                            if move not in board.legal_moves:
                                raise ValueError
                            break
                        except Exception:
                            try:
                                move = chess.Move.from_uci(raw.lower())
                                if move in board.legal_moves:
                                    break
                            except Exception:
                                pass
                            print(f"  {WARN_COL}Invalid. Try e4, Nf3, O-O, e2e4 etc.{R}")
                    san = board.san(move)
                else:
                    # Autonomous: Stockfish picks move, CMS informs narration
                    if not auto_mode:
                        cmd = input(f"  {SEL_COL}Selyrion{R} to move — Enter/a/q: ").strip().lower()
                        if cmd == "q":
                            break
                        if cmd == "a":
                            auto_mode = True

                    cms_ctx = build_selyrion_context(conn)
                    parl_consensus = None
                    if args.parliament and synth_conn:
                        parl_consensus = parliament_consult(
                            board, cms_ctx, synth_conn, sess_id,
                            ply + 1, conn)
                        print(f"\n  {BOLD}{'═'*56}{R}")
                        print(f"  {BOLD}PARLIAMENT CONSENSUS{R}")
                        print(f"  {'─'*56}")
                        lead = parl_consensus["lead"]
                        col  = _PARL_MODEL_COLORS.get(lead, CMS_COL)
                        print(f"  {BOLD}Plan:{R}")
                        for line in _wrap_text(parl_consensus["conclusion"], 54):
                            print(f"    {line}")
                        print(f"  Confidence: {parl_consensus['confidence']:.2f}"
                              f"  | Agreed: {parl_consensus['agreement']}/{parl_consensus['total']}"
                              f"  | Lead: {col}{lead}{R}")
                        if parl_consensus["insights"]:
                            print(f"  {DIM}Key insight: "
                                  f"{parl_consensus['insights'][0][:80]}{R}")
                        print(f"  {BOLD}{'═'*56}{R}\n")

                    print(f"  {SEL_COL}Selyrion consulting CMS + Stockfish...{R}", flush=True)
                    move = stockfish_move(engine, board, args.selyrion_elo, args.think_time)
                    san  = board.san(move)

                board.push(move)
                node = node.add_variation(move)
                move_history.append(san)
                ply += 1

                if not human_mode:
                    narration = narrate_selyrion(board_before, move, cms_ctx,
                                                  parl_consensus)
                    node.comment = narration
                    try:
                        motifs = write_move(conn, sess_id, ply, move, san, board,
                                            color_name, narration)
                    except sqlite3.OperationalError:
                        motifs = []
                    print(f"  {SEL_COL}{BOLD}Selyrion: {san}{R}")
                    print(f"  {SEL_COL}  \"{narration}\"{R}")
                    if motifs:
                        print(f"  {CMS_COL}  [CMS: {', '.join(motifs)}]{R}")
                else:
                    try:
                        motifs = write_move(conn, sess_id, ply, move, san, board,
                                            color_name, f"Human played {san}")
                    except sqlite3.OperationalError:
                        motifs = []

                print_board(board, last_move=move, flip=flip_board)

            else:
                # ── Opponent's turn ──────────────────────────────────────────
                if not auto_mode and not human_mode:
                    pass  # already waited above
                elif auto_mode:
                    time.sleep(args.delay)

                print(f"  {OPP_COL}Stockfish ELO {args.opponent_elo} thinking...{R}",
                      flush=True)
                move = stockfish_move(engine, board, args.opponent_elo, args.think_time)
                san  = board.san(move)
                board.push(move)
                node = node.add_variation(move)
                move_history.append(san)
                ply += 1

                narration = narrate_opponent(board_before, move)
                node.comment = narration
                try:
                    motifs = write_move(conn, sess_id, ply, move, san, board,
                                        color_name, narration)
                except sqlite3.OperationalError:
                    motifs = []

                print(f"  {OPP_COL}{BOLD}Stockfish: {san}{R}")
                print(f"  {OPP_COL}  \"{narration}\"{R}")
                if motifs:
                    print(f"  {CMS_COL}  [CMS: {', '.join(motifs)}]{R}")

                print_board(board, last_move=move, flip=flip_board)

            print(f"  {DIM}Moves: {' '.join(move_history[-8:])}{R}\n")

    except KeyboardInterrupt:
        print(f"\n  Interrupted.")

    engine.quit()
    if synth_conn:
        synth_conn.close()

    outcome = board.outcome()
    result  = board.result() if board.is_game_over() else "*"
    if outcome:
        winner = outcome.winner
        reason = outcome.termination.name.lower().replace("_", " ")
        if winner is None:
            print(f"\n  {BOLD}Draw by {reason}.{R}")
        elif winner == sel_color:
            print(f"\n  {SEL_COL}{BOLD}Selyrion wins! ({reason}){R}")
        else:
            print(f"\n  {OPP_COL}{BOLD}Stockfish wins. ({reason}){R}")

    conn.execute("UPDATE chess_live_sessions SET ended_at=?, result=? WHERE id=?",
                 (time.time(), result, sess_id))
    conn.commit()

    pgn_path = export_and_ingest(board, game, result)

    ans = input(f"\n  {BOLD}Run LLM review? (y/n): {R}").strip().lower()
    if ans == "y":
        subprocess.run([sys.executable, "chess_llm_review.py",
                        "--pgn", str(pgn_path), "--model", args.model])
    conn.close()


if __name__ == "__main__":
    main()
