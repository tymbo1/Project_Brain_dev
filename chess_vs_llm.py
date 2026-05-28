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
parser.add_argument("--vs-human",       action="store_true", help="Human plays opponent side against Selyrion")
parser.add_argument("--no-db",          action="store_true", help="Skip all DB writes (pure play mode)")
parser.add_argument("--think-time",     type=float, default=0.5, help="Stockfish think time (seconds)")
parser.add_argument("--resume",         action="store_true",     help="Resume last incomplete session")
parser.add_argument("--resume-session", default=None,            help="Resume specific session by ID")
parser.add_argument("--parliament",     action="store_true",
                    help="Enable parliament deliberation before each Selyrion move")
parser.add_argument("--no-parliament",  action="store_true",
                    help="Explicitly disable parliament (default — same as omitting --parliament)")
parser.add_argument("--parliament-models",
                    default="qwen2.5:7b,qwen3:4b",
                    help="Models for parliament deliberation (phi4-mini benched: 63% failure rate; gemma3:4b benched: VRAM swap timeout; qwen2.5:14b benched: 9GB exceeds 8GB VRAM)")
parser.add_argument("--synth-db",       default=str(Path.home() / "selyrion_synth.db"),
                    help="synth.db path for parliament proposals")
parser.add_argument("--selyrion-picks", action="store_true",
                    help="Selyrion LLM picks from Stockfish top-N candidates (not just narrates)")
parser.add_argument("--symbolic-pick",  action="store_true",
                    help="Pure symbolic move selection: CMS lookahead, no LLM for decision")
parser.add_argument("--top-n",          type=int, default=3,
                    help="How many Stockfish candidates to present to Selyrion (default 3)")
parser.add_argument("--stream",         action="store_true", default=True,
                    help="Stream LLM narration to terminal as it generates (default on)")
parser.add_argument("--scos",           action="store_true",
                    help="Use SCOS tool bridge for move selection (memory_search + cms_write loop)")
parser.add_argument("--llm-articulate", action="store_true",
                    help="One LLM call to articulate the reasoning packet after symbolic pick")
parser.add_argument("--parliament-audit", type=int, default=0, metavar="N",
                    help="Parliament deliberation every N moves only (0=disabled, training/audit mode)")
parser.add_argument("--no-langeng",     action="store_true",
                    help="Disable LangEng narration (use LLM fallback only; faster for harvesting runs)")
parser.add_argument("--force-moves",    default="",
                    help="Comma-separated UCI moves to play before handing off (e.g. e2e4,e7e5,g1f3)")
parser.add_argument("--start-fen",      default="",
                    help="Start game from this FEN position instead of the initial position")
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


# ── LangEng narration (optional, replaces LLM narration) ─────────────────────
_langeng_ae = None
_langeng_bridge = None

def _init_langeng():
    global _langeng_ae, _langeng_bridge
    if _langeng_ae is not None:
        return True
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path.home() / "projectbrain_dev" / "inference"))
        _sys.path.insert(0, str(Path.home() / "Le_P2"))
        from activation_engine import ActivationEngine
        from langeng_bridge import chains_to_prose
        _langeng_ae = ActivationEngine(db_path=str(Path.home() / "resonance_v11.db"))
        _langeng_bridge = chains_to_prose
        return True
    except Exception as e:
        return False


def _chess_query_terms(board: chess.Board, move: chess.Move, motifs: list) -> list[str]:
    """Build CMS query terms from a chess position and move."""
    terms = []
    piece = board.piece_at(move.from_square)
    if piece:
        terms.append(chess.piece_name(piece.piece_type))
    if board.is_capture(move):
        terms.append("capture")
    board2 = board.copy(); board2.push(move)
    if board2.is_check():
        terms.append("check")
    if board2.is_checkmate():
        terms.append("checkmate")
    # Motifs from CMS
    terms.extend(motifs[:3])
    # Positional concepts based on piece
    if piece and piece.piece_type == chess.PAWN:
        terms.append("pawn structure")
    if piece and piece.piece_type in (chess.ROOK, chess.QUEEN):
        terms.append("open file")
    return list(dict.fromkeys(terms))  # dedup


def narrate_langeng(board_before: chess.Board, move: chess.Move,
                    motifs: list, opening: str, is_selyrion: bool) -> str | None:
    """Narrate a chess move using LangEng symbolic prose. Returns None on failure."""
    if args.no_langeng:
        return None
    if not _init_langeng():
        return None
    try:
        terms = _chess_query_terms(board_before, move, motifs)
        if not terms:
            return None
        san = board_before.san(move)
        # Query CMS for each term, collect chains
        all_chains = []
        for term in terms[:3]:
            result = _langeng_ae.infer(term, max_chains=6,
                                       domain_override={"chess", "general"})
            chains = result.get("chains", [])
            all_chains.extend(chains[:4])
        if not all_chains:
            return None
        prose = _langeng_bridge(terms[0], all_chains, intent="expand")
        if not prose or len(prose) < 20:
            return None
        # Wrap with move context
        prefix = f"{'Selyrion' if is_selyrion else 'The opponent'} played {san}. "
        if opening and opening != "unknown_opening":
            prefix += f"In the {opening.replace('_', ' ')}: "
        return prefix + prose
    except Exception:
        return None




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


def _llm_call(prompt: str, prefix: str = "", stream_timeout: int = 30) -> str:
    """
    Call LLM with timeout escalation.
    Streams to terminal if args.stream; falls back to non-stream if Ollama hangs.
    Prevents indefinite game freeze on slow/stuck model response.
    """
    import threading

    def _non_stream_fallback() -> str:
        try:
            r = requests.post(OLLAMA_URL, json={
                "model": args.model, "prompt": prompt,
                "stream": False, "options": {"temperature": 0.6}
            }, timeout=30)
            return r.json().get("response", "").strip()[:400]
        except Exception as e:
            return f"(narration unavailable: {e})"

    if not args.stream:
        return _non_stream_fallback()

    # Streaming path with timeout watchdog
    result_holder: dict = {"text": "", "done": False, "cancelled": False}

    def _stream_worker():
        try:
            r = requests.post(OLLAMA_URL, json={
                "model": args.model, "prompt": prompt,
                "stream": True, "options": {"temperature": 0.6}
            }, timeout=stream_timeout, stream=True)
            chunks = []
            if prefix:
                print(prefix, end="", flush=True)
            for line in r.iter_lines():
                if result_holder["cancelled"]:
                    break
                if not line:
                    continue
                try:
                    data  = json.loads(line)
                    chunk = data.get("response", "")
                    chunks.append(chunk)
                    print(chunk, end="", flush=True)
                    if data.get("done"):
                        break
                except json.JSONDecodeError:
                    pass
            result_holder["text"] = "".join(chunks).strip()[:400]
        except Exception:
            pass
        result_holder["done"] = True

    thread = threading.Thread(target=_stream_worker, daemon=True)
    thread.start()
    thread.join(timeout=stream_timeout + 5)

    if result_holder["done"] and result_holder["text"]:
        print()  # newline after streaming
        return result_holder["text"]

    # Stream stalled — escalate to non-streaming
    result_holder["cancelled"] = True
    print(f"\n  [stream timeout — falling back to non-stream]", flush=True)
    return _non_stream_fallback()


_OPENING_BOOK = {
    ("e2e4", "e7e5", "g1f3", "g8f6"):            "petrov_defense",
    ("e2e4", "e7e5", "g1f3", "b8c6"):            "open_game",
    ("e2e4", "c7c5"):                            "sicilian_defense",
    ("e2e4", "e7e6"):                            "french_defense",
    ("e2e4", "c7c6"):                            "caro_kann",
    ("d2d4", "d7d5", "c2c4"):                    "queens_gambit",
    ("d2d4", "g8f6", "c2c4", "e7e6"):            "nimzo_or_queens_indian",
    ("d2d4", "g8f6", "c2c4", "g7g6"):            "kings_indian_defense",
    ("d2d4", "g8f6", "c2c4", "c7c5"):            "benoni",
    ("d2d4", "g8f6", "g1f3"):                    "queens_indian_setup",
    ("c2c4",):                                   "english_opening",
    ("g1f3",):                                   "reti_opening",
    ("d2d4", "d7d5"):                            "closed_game",
    ("d2d4",):                                   "indian_defense",
    ("e2e4", "e7e5"):                            "open_game_nf3",
    ("f2f4",):                                   "bird_opening",
}

def classify_opening(moves_uci: list) -> str:
    best = "unknown_opening"
    for length in range(min(6, len(moves_uci)), 0, -1):
        key = tuple(moves_uci[:length])
        if key in _OPENING_BOOK:
            return _OPENING_BOOK[key]
    return best


_PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
                 chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def positional_summary(board: chess.Board) -> dict:
    """Quick positional facts: material balance, king safety, mobility."""
    def mat(color):
        return sum(_PIECE_VALUES.get(pt, 0)
                   for pt in chess.PIECE_TYPES
                   for _ in board.pieces(pt, color))

    us   = board.turn
    them = not board.turn
    our_mat  = mat(us)
    opp_mat  = mat(them)
    balance  = our_mat - opp_mat  # positive = we're ahead

    def king_zone_pressure(color):
        sq = board.king(color)
        if sq is None:
            return "unknown"
        enemy = not color
        n_attackers = len(board.attackers(enemy, sq))
        adjacent = chess.SquareSet(chess.BB_KING_ATTACKS[sq])
        attacked_adj = sum(1 for s in adjacent if board.is_attacked_by(enemy, s))
        if n_attackers >= 1 or attacked_adj >= 3:
            return "exposed"
        elif attacked_adj == 0:
            return "safe"
        return "moderate"

    return {
        "material_balance": balance,
        "our_material": our_mat,
        "their_material": opp_mat,
        "our_king": king_zone_pressure(us),
        "their_king": king_zone_pressure(them),
        "our_mobility": board.legal_moves.count(),
        "castled_us":   board.has_castling_rights(us),
    }


def tactical_scan(board: chess.Board) -> dict:
    """Detect immediate tactical themes: checks, winning captures, hanging pieces."""
    us   = board.turn
    them = not board.turn

    alerts           = []
    checks_available = []
    winning_captures = []
    hanging_pieces   = []

    if board.is_check():
        alerts.append("YOU ARE IN CHECK — must resolve immediately")

    for move in board.legal_moves:
        san = board.san(move)
        board.push(move)
        is_check = board.is_check()
        is_mate  = board.is_checkmate()
        board.pop()
        if is_mate:
            checks_available.insert(0, f"{san} (CHECKMATE)")
            alerts.insert(0, f"CHECKMATE AVAILABLE: {san}")
        elif is_check:
            checks_available.append(san)

    for move in board.legal_moves:
        if board.is_capture(move):
            victim   = board.piece_at(move.to_square)
            attacker = board.piece_at(move.from_square)
            if victim and attacker:
                gain = _PIECE_VALUES.get(victim.piece_type, 0) - _PIECE_VALUES.get(attacker.piece_type, 0)
                if gain > 0:
                    winning_captures.append((board.san(move), gain))

    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if piece and piece.color == them:
            if board.attackers(us, sq) and not board.attackers(them, sq):
                hanging_pieces.append((chess.square_name(sq), chess.piece_name(piece.piece_type)))

    if hanging_pieces:
        for sq_name, pt_name in hanging_pieces[:2]:
            alerts.append(f"Opponent {pt_name} on {sq_name} is UNDEFENDED (free capture)")
    if winning_captures:
        for san, gain in winning_captures[:2]:
            alerts.append(f"Winning exchange: {san} gains +{gain} material")
    if checks_available and not any("CHECKMATE" in a for a in alerts):
        alerts.append(f"Check moves available: {', '.join(checks_available[:3])}")

    # Derive tactical theme labels from detected features
    motifs = []
    if any("CHECKMATE" in a for a in alerts):
        motifs.append("checkmate")
    if winning_captures:
        # If a capture creates a fork-like situation (gain from knight/bishop attacking multiple)
        if len(winning_captures) >= 2:
            motifs.append("fork")
        else:
            motifs.append("trapped piece")
    if hanging_pieces:
        motifs.append("trapped piece")
    if checks_available and not any("CHECKMATE" in a for a in alerts):
        motifs.append("initiative")
    if board.is_check():
        motifs.append("back-rank weakness") if board.fullmove_number > 15 else None
    # Deduplicate
    motifs = list(dict.fromkeys(motifs))

    return {
        "alerts":           alerts,
        "checks_available": checks_available[:4],
        "winning_captures": [s for s, _ in winning_captures[:3]],
        "hanging_pieces":   hanging_pieces[:3],
        "motifs":           motifs,
    }


def stockfish_top_n(engine: chess.engine.SimpleEngine, board: chess.Board,
                    elo: int, n: int, think_time: float) -> list[tuple[chess.Move, str, int]]:
    """Return top-N Stockfish moves as (move, san, centipawn_score)."""
    try:
        engine.configure({"UCI_LimitStrength": True, "UCI_Elo": elo})
    except Exception:
        pass
    try:
        analysis = engine.analyse(board, chess.engine.Limit(time=think_time),
                                  multipv=n)
        results = []
        for info in analysis[:n]:
            move = info["pv"][0] if info.get("pv") else None
            if move and move in board.legal_moves:
                san   = board.san(move)
                score = info.get("score", chess.engine.PovScore(
                    chess.engine.Cp(0), board.turn))
                cp = score.relative.score(mate_score=10000) or 0
                results.append((move, san, cp))
        return results if results else [(engine.play(board, chess.engine.Limit(time=think_time)).move,
                                        board.san(engine.play(board, chess.engine.Limit(time=0.1)).move), 0)]
    except Exception:
        move = engine.play(board, chess.engine.Limit(time=think_time)).move
        return [(move, board.san(move), 0)]


def _fetch_cms_pattern_score(
    opening: str,
    active_themes: list | None,
) -> float:
    """Query CMS ONCE per turn — opening + theme patterns.

    Returns a combined pattern score (0.0–1.0) to be reused across all
    leaf nodes in the lookahead tree. Eliminates per-leaf SQLite queries.
    """
    try:
        from tools.memory_search import memory_search

        def _q(query: str, limit: int = 8) -> float:
            r = memory_search({"query": query, "domain": "chess", "limit": limit})
            if r.get("status") != "success" or not r.get("relations"):
                return 0.0
            rels = r["relations"]
            return sum(rel["confidence"] * min(rel["seen"], 100) / 100.0
                       for rel in rels) / max(len(rels), 1)

        scores: list[tuple[float, float]] = []

        if opening and opening != "unknown_opening":
            s = _q(f"{opening.replace('_', ' ')} leads to", limit=10)
            if s > 0:
                scores.append((1.5, s))

        if active_themes:
            s = _q(f"{active_themes[0].split(':')[0].strip()} anticipates", limit=8)
            if s > 0:
                scores.append((1.2, s))

        # Always include a generic fallback so score is non-zero even pre-opening
        s = _q("chess tactics", limit=8)
        scores.append((1.0, s))

        if not scores:
            return 0.0
        total_w = sum(w for w, _ in scores)
        return sum(w * s for w, s in scores) / total_w

    except Exception:
        return 0.0


def _cms_leaf_score(board: chess.Board, precomputed_pattern: float) -> float:
    """Score a leaf node using precomputed CMS pattern + live material delta.

    Called thousands of times per turn — must be O(1). All CMS queries
    already happened in _fetch_cms_pattern_score(); this just combines
    the cached pattern score with position-specific material facts.
    """
    if board.is_checkmate():
        return 100.0
    if board.is_stalemate() or board.is_insufficient_material():
        return -5.0

    us   = not board.turn
    them = board.turn
    piece_map = board.piece_map()
    our_mat   = sum(_PIECE_VALUES.get(p.piece_type, 0)
                    for p in piece_map.values() if p.color == us)
    their_mat = sum(_PIECE_VALUES.get(p.piece_type, 0)
                    for p in piece_map.values() if p.color == them)
    mat_delta = our_mat - their_mat
    return mat_delta * 0.15 + precomputed_pattern * 2.0


# Keep the old name as a compatibility shim for any callers that pass board only
def _cms_position_score(
    board: chess.Board,
    opening: str = "unknown_opening",
    active_themes: list | None = None,
    precomputed_pattern: float | None = None,
) -> float:
    if precomputed_pattern is not None:
        return _cms_leaf_score(board, precomputed_pattern)
    # Legacy: fetch on-demand (slow — only reached by old call paths)
    pat = _fetch_cms_pattern_score(opening, active_themes)
    return _cms_leaf_score(board, pat)


def selyrion_lookahead(board: chess.Board,
                       candidates: list[tuple[chess.Move, str, int]],
                       engine: chess.engine.SimpleEngine,
                       depth: int = 6,
                       tactic_info: dict = None,
                       opening: str = "unknown_opening",
                       active_themes: list | None = None,
                       ) -> list[tuple[chess.Move, str, int, float, float]]:
    """Evaluate each candidate by playing out a variation tree depth moves deep.

    Performance architecture:
      - CMS pattern score fetched ONCE before the loop (not per-leaf).
      - Each candidate evaluated in a parallel thread with its own Stockfish instance.
      - Leaf scoring is O(1): precomputed_pattern + live material delta.

    Returns (move, san, engine_cp, lookahead_score, variance).
    """
    import math as _math
    import chess.polyglot as _poly

    think = 0.05

    # Transposition table: zobrist_hash → (score, depth_remaining)
    # Caps at 200k entries to bound memory (~30MB at ~150 bytes/entry)
    _tt: dict[int, tuple[float, int]] = {}
    _TT_MAX = 200_000

    # ── Adaptive branching ────────────────────────────────────────────────────
    if tactic_info:
        if tactic_info.get("checks_available") or tactic_info.get("winning_captures"):
            base_branching = 4
        elif tactic_info.get("hanging_pieces"):
            base_branching = 3
        else:
            base_branching = 2
    else:
        base_branching = 2

    # ── CMS pattern fetched ONCE, reused across all variations ───────────────
    precomputed_pattern = _fetch_cms_pattern_score(opening, active_themes)

    # ── PV-line evaluation: 1 engine call per candidate, not branching^depth ─
    # For each candidate move, ask Stockfish for `pv_lines` principal variations
    # at `depth` half-moves. Walk each PV to its terminal position and score it
    # with _cms_leaf_score (O(1) — no further engine calls).
    # Total engine calls = len(candidates) × 1, regardless of depth.
    results = []
    pv_lines = base_branching  # number of PV lines per candidate (same as old branching)

    for move, san, engine_cp in candidates:
        b = board.copy()
        b.push(move)

        try:
            analyses = engine.analyse(
                b,
                chess.engine.Limit(depth=depth),
                multipv=pv_lines,
            )
        except Exception:
            results.append((move, san, engine_cp, 0.0, 1.0, []))
            continue

        leaf_scores: list[float] = []
        variations: list[dict] = []

        for analysis in analyses:
            pv = analysis.get("pv", [])
            b2 = b.copy()
            pv_san_list: list[str] = []
            captures = 0
            checks = 0
            immediate_captures = 0  # ply ≤ 2 of PV (causally close to candidate move)
            immediate_checks = 0
            for i, pv_move in enumerate(pv):
                if pv_move not in b2.legal_moves:
                    break
                is_cap = b2.is_capture(pv_move)
                if is_cap:
                    captures += 1
                    if i < 2:
                        immediate_captures += 1
                mv_san = b2.san(pv_move)
                pv_san_list.append(mv_san)
                if "+" in mv_san or "#" in mv_san:
                    checks += 1
                    if i < 2:
                        immediate_checks += 1
                b2.push(pv_move)

            # Transposition table: avoid re-scoring identical leaf positions
            _zh = _poly.zobrist_hash(b2)
            if _zh in _tt:
                score = _tt[_zh]
            else:
                score = _cms_leaf_score(b2, precomputed_pattern)
                if len(_tt) < _TT_MAX:
                    _tt[_zh] = score
            is_opp_final = (len(pv) % 2 == 1)
            adj = -score if is_opp_final else score

            pv_joined = " ".join(pv_san_list)

            # Immediate themes: causally attributable to the candidate move (ply ≤ 2)
            immediate_themes: list[str] = []
            if b2.is_checkmate() or (pv_san_list and "#" in pv_san_list[-1] and len(pv_san_list) <= 2):
                immediate_themes.append("checkmate")
            if immediate_captures >= 2 and immediate_checks >= 1:
                immediate_themes.append("fork")
            elif immediate_captures == 1 and immediate_checks >= 1:
                immediate_themes.append("discovered attack")
            elif immediate_captures >= 2:
                immediate_themes.append("initiative")

            # Line themes: correlations visible deeper in the PV, not direct causality
            line_themes: list[str] = []
            if b2.is_checkmate() and "checkmate" not in immediate_themes:
                line_themes.append("checkmate")
            if "=Q" in pv_joined or "=R" in pv_joined:
                line_themes.append("pawn promotion")
            if captures >= 2 and checks >= 1 and "fork" not in immediate_themes:
                line_themes.append("fork")
            elif captures >= 2 and "initiative" not in immediate_themes and "fork" not in immediate_themes:
                line_themes.append("initiative")
            elif captures == 1 and checks >= 1 and "discovered attack" not in immediate_themes:
                line_themes.append("discovered attack")
            if checks >= 2 and "checkmate" not in immediate_themes and "initiative" not in (immediate_themes + line_themes):
                line_themes.append("initiative")
            if "O-O-O" in pv_joined:
                line_themes.append("queenside attack")
            elif "O-O" in pv_joined:
                line_themes.append("kingside attack")
            line_themes = [t for t in line_themes if t not in immediate_themes]

            pv_themes = list(dict.fromkeys(immediate_themes + line_themes))
            outcome_weight = sum(_THEME_WEIGHTS.get(t, 1) for t in pv_themes)
            stability_tag = "stable" if len(leaf_scores) == 0 or abs(adj - (leaf_scores[-1] if leaf_scores else 0)) < 0.5 else "volatile"

            variations.append({
                "pv_san":           pv_san_list,
                "leaf_score":       round(adj, 3),
                "themes":           pv_themes,
                "immediate_themes": immediate_themes,
                "line_themes":      line_themes,
                "outcome_weight":   outcome_weight,
                "captures":         captures,
                "checks":           checks,
                "stability":        stability_tag,
            })
            leaf_scores.append(adj)

        if not leaf_scores:
            results.append((move, san, engine_cp, 0.0, 1.0, []))
            continue

        mean = sum(leaf_scores) / len(leaf_scores)
        variance = (_math.sqrt(
            sum((s - mean) ** 2 for s in leaf_scores) / len(leaf_scores)
        ) if len(leaf_scores) > 1 else 0.0)
        stability_bonus = max(0.0, 1.0 - variance * 0.4)
        adjusted = mean * (0.7 + 0.3 * stability_bonus)
        results.append((move, san, engine_cp, adjusted, variance, variations))

    # ── Trajectory compression: write best variation themes back to CMS ──────
    # This is how Selyrion learns from lookahead — not just uses it live.
    try:
        if results and opening and opening != "unknown_opening":
            best = max(results, key=lambda r: r[3])
            best_vars = best[5]
            if best_vars:
                top_var = max(best_vars, key=lambda v: v.get("outcome_weight", 0))
                themes = top_var.get("immediate_themes", []) + top_var.get("line_themes", [])
                cms = _get_cms_conn()
                if cms and themes:
                    import hashlib as _hl
                    for theme in themes[:3]:
                        subj = opening.replace("_", " ")
                        obj  = theme
                        rid  = "r." + _hl.md5(f"{subj}anticipates{obj}chess_traj".encode()).hexdigest()[:12]
                        sid  = "a." + _hl.md5(subj.encode()).hexdigest()[:12]
                        oid  = "a." + _hl.md5(obj.encode()).hexdigest()[:12]
                        cms.execute("INSERT OR IGNORE INTO anchors (id, canonical, maturity, domain_tags) VALUES (?,?,1.0,'chess')", (sid, subj))
                        cms.execute("INSERT OR IGNORE INTO anchors (id, canonical, maturity, domain_tags) VALUES (?,?,1.0,'chess')", (oid, obj))
                        cms.execute("""
                            INSERT INTO relations (id, subject_id, object_id, predicate, confidence, seen_count, domain_tags, source_dataset)
                            VALUES (?,?,?,'anticipates',?,1,'chess','lookahead_traj')
                            ON CONFLICT(id) DO UPDATE SET seen_count=seen_count+1, confidence=MIN(0.95,confidence+0.02)
                        """, (rid, sid, oid, min(0.95, 0.65 + best[3] * 0.1)))
                    cms.commit()
    except Exception:
        pass

    return results


def selyrion_pick_move(board: chess.Board, candidates: list[tuple[chess.Move, str, int]],
                       cms_ctx: str, parl_consensus: dict = None) -> tuple[chess.Move, str]:
    """Selyrion LLM picks the best move from Stockfish candidates using CMS + parliament context."""
    color_name = "white" if board.turn == chess.WHITE else "black"
    n = len(candidates)
    cand_lines = "\n".join(
        f"  {i+1}. {san}  (engine score: {cp:+d} cp)"
        for i, (_, san, cp) in enumerate(candidates)
    )

    # D: Parliament proposed moves — show Selyrion what parliament voted for
    parl_block = ""
    parl_bias_idx = None
    if parl_consensus:
        proposed = parl_consensus.get("proposed_moves", [])
        if proposed and proposed[0] < len(candidates):
            parl_bias_idx = proposed[0]
            parl_san = candidates[parl_bias_idx][1]
            vote_count = len([v for v in proposed if v == parl_bias_idx])
            parl_block += (
                f"\nParliament move recommendation: {parl_san} "
                f"(voted by {vote_count}/{parl_consensus.get('total', '?')} models)"
            )
        if parl_consensus.get("conclusion"):
            parl_block += f"\nParliament strategic plan: {parl_consensus['conclusion'][:200]}"
        if parl_consensus.get("tactic_alerts"):
            parl_block += f"\nTactical alerts: {'; '.join(parl_consensus['tactic_alerts'][:2])}"

    prompt = f"""You are Selyrion, a chess reasoning system with a symbolic knowledge graph (CMS).

Position (FEN): {board.fen()}
You are playing {color_name}.

CMS knowledge:
{cms_ctx}{parl_block}

Stockfish has identified these candidate moves (with engine evaluation):
{cand_lines}

Parliament has analyzed this position and made a specific recommendation above.
You may follow parliament's recommendation, or override it if your CMS knowledge strongly suggests otherwise.
If parliament flagged a tactical alert (check, hanging piece, etc.), it must be addressed — do not ignore it.

Reply with ONLY the move number (1-{n}) and one sentence explaining why.
Example: "2. I choose Nf3 because it develops toward the center outpost."
"""
    raw = _llm_call(prompt, prefix=f"  {SEL_COL}Selyrion choosing... {R}")
    m = re.search(rf'\b([1-{n}])\b', raw)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(candidates):
            if parl_bias_idx is not None and idx != parl_bias_idx:
                print(f"  {WARN_COL}[Selyrion overrode parliament: "
                      f"{candidates[parl_bias_idx][1]} → {candidates[idx][1]}]{R}")
            return candidates[idx][0], candidates[idx][1]
    # Fallback: parliament bias if available, else top Stockfish move
    if parl_bias_idx is not None:
        return candidates[parl_bias_idx][0], candidates[parl_bias_idx][1]
    return candidates[0][0], candidates[0][1]


# ── SCOS tool-bridge move selection ─────────────────────────────────────────

_scos_bridge = None

def _get_scos_bridge():
    global _scos_bridge
    if _scos_bridge is None:
        try:
            from scos_bridge import SelyrionBridge
            _scos_bridge = SelyrionBridge(model="qwen2.5:14b")
        except Exception as e:
            print(f"  {WARN_COL}SCOS bridge unavailable: {e}{R}")
    return _scos_bridge


def selyrion_scos_pick(board: chess.Board,
                       candidates: list[tuple[chess.Move, str, int]],
                       tactic_info: dict = None,
                       parl_consensus: dict = None,
                       engine: chess.engine.SimpleEngine = None,
                       opening: str = "unknown_opening",
                       active_themes: list | None = None,
                       ) -> tuple[chess.Move, str, list]:
    """Selyrion picks a move using CMS lookahead tree + single LLM decision.

    The CMS IS the multi-hop symbolic reasoner:
      - _cms_position_score() queries: opening→leads_to, theme→anticipates, material fallback
      - selyrion_lookahead() walks a 6-deep variation tree, scoring each leaf via CMS
    The lookahead scores are pre-computed symbolic reasoning. The LLM gets one call
    to pick from ranked candidates — not to simulate reasoning that already happened.
    """
    n = len(candidates)

    # ── CMS symbolic lookahead (opening-aware, theme-aware) ──────────────────
    lookahead_scores = {}
    scored_sorted: list = []
    if engine is not None:
        try:
            print(f"  {DIM}Selyrion CMS lookahead (depth 6, opening={opening})...{R}", flush=True)
            scored = selyrion_lookahead(board, candidates, engine, depth=6,
                                        tactic_info=tactic_info,
                                        opening=opening,
                                        active_themes=active_themes)
            lookahead_scores = {san: (score, var) for _, san, _, score, var, *_ in scored}
            scored_sorted = sorted(scored, key=lambda x: x[3], reverse=True)
            parts = []
            for _, san, _, score, var, *_ in scored_sorted:
                stability = "stable" if var < 0.2 else ("volatile" if var > 0.5 else "mid")
                parts.append(f"{san} {score:+.2f} ({stability})")
            print(f"  {DIM}CMS scores: " + " | ".join(parts) + f"{R}", flush=True)
        except Exception as e:
            print(f"  {DIM}Lookahead skipped: {e}{R}")

    # ── Motif coherence filter ────────────────────────────────────────────────
    raw_motifs = tactic_info.get("motifs", []) if tactic_info else []
    active_motifs, observed_motifs = motif_strategy_match(raw_motifs, board, tactic_info)

    # ── Build candidate display with lookahead scores ─────────────────────────
    cand_lines = "\n".join(
        f"  {i+1}. {san}  (engine: {cp:+d} cp"
        + (f"  CMS-lookahead: {lookahead_scores[san][0]:+.2f}"
           f" {'[stable]' if lookahead_scores[san][1] < 0.2 else '[volatile]' if lookahead_scores[san][1] > 0.5 else '[mid]'}"
           if san in lookahead_scores else "")
        + ")"
        for i, (_, san, cp) in enumerate(candidates)
    )

    tactic_block = ""
    if tactic_info and tactic_info.get("alerts"):
        all_alerts = tactic_info["alerts"]
        active_alerts = [a for a in all_alerts
                         if any(m.lower() in a.lower() for m in active_motifs)] or all_alerts[:1]
        tactic_block = "TACTICAL ALERTS:\n" + \
                       "\n".join(f"  ⚠ {a}" for a in active_alerts) + "\n"

    opening_block = (
        f"Opening: {opening.replace('_', ' ')}\n"
        if opening != "unknown_opening" else ""
    )

    color_name = "white" if board.turn == chess.WHITE else "black"
    prompt = f"""You are Selyrion, a chess engine with symbolic CMS memory.

Position: {board.fen()}
Playing as: {color_name}
{opening_block}{tactic_block}
Candidates (engine centipawns + 6-move CMS-lookahead score):
{cand_lines}

The CMS-lookahead score reflects opening-pattern and tactical-chain knowledge evaluated
6 moves deep. Prefer high lookahead + stable variance. Override engine cp only if lookahead
strongly disagrees or a tactic demands it.

Reply with ONLY: <number 1-{n}>. <one-sentence reason>.
Example: "2. Nf3 develops toward the opening's known fork setup."
"""
    raw = _llm_call(prompt, prefix=f"  {SEL_COL}Selyrion deciding... {R}")
    m = re.search(rf'\b([1-{n}])\b', raw)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < n:
            reason = raw.split(".", 1)[-1].strip()[:120] if "." in raw else ""
            if reason:
                print(f"  {SEL_COL}→ {candidates[idx][1]}: {reason}{R}")
            if observed_motifs:
                print(f"  {DIM}[motifs observed but not strategy-coherent: "
                      f"{', '.join(observed_motifs)}]{R}")
            return candidates[idx][0], candidates[idx][1], active_motifs

    # Fallback: top CMS-lookahead candidate if available, else engine top
    if scored_sorted:
        top = scored_sorted[0]
        return top[0], top[1], active_motifs
    return candidates[0][0], candidates[0][1], active_motifs


# ── Motif observation layer ───────────────────────────────────────────────────
# Motifs are observations. They only enter SCOS cognition if coherent with
# the current positional strategy — not just because they were detected.

_MOTIF_STRATEGY_MAP: dict[str, list[str]] = {
    # motif → strategies it is coherent with
    "fork":              ["win material", "tactical pressure", "initiative"],
    "pin":               ["win material", "tactical pressure", "restrict mobility"],
    "skewer":            ["win material", "tactical pressure"],
    "sacrifice":         ["kingside attack", "initiative", "compensation"],
    "discovered attack": ["tactical pressure", "win material", "initiative"],
    "back-rank weakness":["checkmate", "tactical pressure", "win material"],
    "passed pawn":       ["endgame conversion", "promotion", "space advantage"],
    "pawn promotion":    ["endgame conversion", "promotion"],
    "zugzwang":          ["endgame conversion", "restrict mobility"],
    "checkmate":         ["checkmate", "kingside attack", "tactical pressure"],
    "deflection":        ["win material", "tactical pressure"],
    "kingside attack":   ["kingside attack", "initiative", "sacrifice"],
    "queenside attack":  ["queenside attack", "space advantage", "initiative"],
    "open file":         ["rook activity", "space advantage", "initiative"],
    "isolated pawn":     ["endgame conversion", "restrict mobility", "space advantage"],
    "outpost":           ["space advantage", "restrict mobility", "endgame conversion"],
    "prophylaxis":       ["restrict mobility", "consolidate", "endgame conversion"],
    "compensation":      ["initiative", "sacrifice", "compensation"],
    "trapped piece":     ["win material", "tactical pressure"],
    "x-ray attack":      ["win material", "tactical pressure"],
    "initiative":        ["initiative", "kingside attack", "tactical pressure"],
    "tempo":             ["initiative", "development advantage"],
}

def _positional_strategy(board: chess.Board, tactic_info: dict) -> list[str]:
    """Derive current strategic context from board state and tactic scan."""
    strategies = []
    move_count = board.fullmove_number

    if tactic_info:
        if tactic_info.get("checks_available"):
            strategies.append("checkmate")
            strategies.append("tactical pressure")
        if tactic_info.get("winning_captures"):
            strategies.append("win material")
            strategies.append("tactical pressure")
        if tactic_info.get("hanging_pieces"):
            strategies.append("win material")

    # Endgame: few pieces remain
    piece_count = len(board.piece_map())
    if piece_count <= 12:
        strategies.append("endgame conversion")
        strategies.append("promotion")

    # Material imbalance → consolidate or press
    white_mat = sum(_PIECE_VALUES.get(p.piece_type, 0)
                    for p in board.piece_map().values() if p.color == chess.WHITE)
    black_mat = sum(_PIECE_VALUES.get(p.piece_type, 0)
                    for p in board.piece_map().values() if p.color == chess.BLACK)
    delta = white_mat - black_mat if board.turn == chess.WHITE else black_mat - white_mat
    if delta >= 300:
        strategies.append("consolidate")
    elif delta <= -150:
        strategies.append("initiative")
        strategies.append("compensation")

    # King safety proxy: opponent king on edge = mating chance
    opp_color = not board.turn
    opp_king_sq = board.king(opp_color)
    if opp_king_sq is not None:
        rank, file = divmod(opp_king_sq, 8)
        if rank in (0, 7) or file in (0, 7):
            strategies.append("kingside attack")
            strategies.append("tactical pressure")

    # Opening/middlegame defaults
    if move_count <= 10:
        strategies.extend(["initiative", "development advantage"])
    elif move_count <= 25:
        strategies.extend(["initiative", "space advantage", "restrict mobility"])

    return list(dict.fromkeys(strategies))  # deduplicate, preserve order


# ── Symbolic pick architecture ────────────────────────────────────────────────
# Selyrion chooses moves deterministically via CMS-scored lookahead.
# No LLM is involved in the decision. The LLM role is narration only.

def _query_future_patterns(opening: str, active_themes: list | None) -> list[str]:
    """Pull known future patterns from CMS for the reasoning packet."""
    patterns = []
    try:
        from tools.memory_search import memory_search
        if opening and opening != "unknown_opening":
            r = memory_search({"query": f"{opening.replace('_', ' ')} leads to",
                               "domain": "chess", "limit": 4})
            if r.get("status") == "success":
                for rel in r.get("relations", [])[:2]:
                    patterns.append(
                        f"{rel.get('subject','')} → {rel.get('predicate','')} → {rel.get('object','')}")
        if active_themes:
            r = memory_search({"query": f"{active_themes[0].split(':')[0]} anticipates",
                               "domain": "chess", "limit": 4})
            if r.get("status") == "success":
                for rel in r.get("relations", [])[:2]:
                    patterns.append(
                        f"{rel.get('subject','')} → {rel.get('predicate','')} → {rel.get('object','')}")
    except Exception:
        pass
    return [p for p in patterns if p.strip(" →")][:4]


def selyrion_symbolic_pick(
    board: chess.Board,
    candidates: list[tuple[chess.Move, str, int]],
    engine: chess.engine.SimpleEngine,
    opening: str = "unknown_opening",
    active_themes: list | None = None,
    tactic_info: dict | None = None,
) -> tuple[chess.Move, str, dict]:
    """Pure symbolic move selection — no LLM.

    CMS pattern fetched once, lookahead runs in parallel threads.
    Returns move + structured reasoning packet for narration.
    """
    print(f"  {DIM}Fetching CMS patterns (once)...{R}", flush=True)
    scored = selyrion_lookahead(
        board, candidates, engine, depth=6,
        tactic_info=tactic_info,
        opening=opening,
        active_themes=active_themes,
    )

    # Blunder gate: reject candidates more than BLUNDER_MARGIN cp below best engine eval
    BLUNDER_MARGIN = 80
    best_engine_cp = max(row[2] for row in scored)
    safe_scored = [row for row in scored if row[2] >= best_engine_cp - BLUNDER_MARGIN]
    blocked = [row[1] for row in scored if row[2] < best_engine_cp - BLUNDER_MARGIN]
    if blocked:
        print(f"  {DIM}[blunder gate] blocked: {', '.join(blocked)} (>{BLUNDER_MARGIN}cp below best){R}", flush=True)
    if not safe_scored:
        safe_scored = [max(scored, key=lambda r: r[2])]

    def _combined(item: tuple) -> float:
        _, san, engine_cp, score, var, *_ = item
        stability = max(0.0, 1.0 - var * 0.4)
        engine_norm = max(-1.0, min(1.0, engine_cp / 3000.0))
        cms_norm = max(-1.0, min(1.0, score))
        # Engine dominates; CMS and stability are supporting signals
        return engine_norm * 0.55 + stability * 0.15 + cms_norm * 0.20

    scored_sorted = sorted(safe_scored, key=_combined, reverse=True)
    best = scored_sorted[0]
    best_move, best_san, best_cp, best_score, best_var, best_variations = best[0], best[1], best[2], best[3], best[4], best[5] if len(best) > 5 else []

    parts = []
    for row in scored_sorted:
        _, san, _, score, var, *_ = row
        stability = "stable" if var < 0.2 else ("volatile" if var > 0.5 else "mid")
        parts.append(f"{san} {score:+.2f} ({stability})")
    print(f"  {DIM}CMS scores: {' | '.join(parts)}{R}", flush=True)

    rejected = []
    for row in scored_sorted[1:]:
        _, san, _, score, var, *_ = row
        if best_score - score > 0.08:
            reason = "lower CMS lookahead"
        elif var > best_var + 0.25:
            reason = "unstable variation"
        else:
            reason = "marginally weaker"
        rejected.append({"move": san, "reason": reason, "score": round(score, 3)})

    future_patterns = _query_future_patterns(opening, active_themes)

    # All evaluated variations (all candidates, all PV lines)
    all_variations = []
    for row in scored_sorted:
        mv, san, cp, score, var, variations = row[0], row[1], row[2], row[3], row[4], row[5] if len(row) > 5 else []
        all_variations.append({
            "move": san,
            "engine_cp": cp,
            "lookahead_score": round(score, 3),
            "variance": round(var, 3),
            "is_chosen": (mv == best_move),
            "lines": variations,
        })

    # Aggregate themes from best move's variations — split by causality
    best_variation_themes = []
    best_immediate_variation_themes = []
    for v in best_variations:
        for t in v.get("themes", []):
            if t not in best_variation_themes:
                best_variation_themes.append(t)
        for t in v.get("immediate_themes", []):
            if t not in best_immediate_variation_themes:
                best_immediate_variation_themes.append(t)

    packet = {
        "chosen_move": best_san,
        "chosen_uci": best_move.uci(),
        "engine_cp": best_cp,
        "lookahead_score": round(best_score, 3),
        "lookahead_variance": round(best_var, 3),
        "opening": opening,
        "active_themes": list(active_themes or []),
        "variation_themes": best_variation_themes,
        "immediate_variation_themes": best_immediate_variation_themes,
        "future_patterns": future_patterns,
        "rejected_moves": rejected,
        "tactic_alerts": list((tactic_info or {}).get("alerts", [])),
        "all_variations": all_variations,
    }
    return best_move, best_san, packet


def langeng_narrate_packet(packet: dict) -> str:
    """Deterministic structured narration from the reasoning packet.

    LangEng chains_to_prose is NOT used here — it pulls from general CMS capsules
    which are not chess-domain-filtered. Use --llm-articulate for quality prose.
    This function produces a precise, data-grounded one-liner from the packet.
    """
    move    = packet["chosen_move"]
    score   = packet["lookahead_score"]
    var     = packet["lookahead_variance"]
    opening = packet["opening"].replace("_", " ") if packet["opening"] != "unknown_opening" else None
    themes  = packet["active_themes"]
    patterns = packet["future_patterns"]
    rejected = packet["rejected_moves"]
    alerts   = packet["tactic_alerts"]
    stability = "stable" if var < 0.2 else ("volatile" if var > 0.5 else "dynamic")

    variation_themes = packet.get("variation_themes", [])
    all_motifs = list(dict.fromkeys(themes + variation_themes))  # deduplicated, ordered

    parts = [f"{move} — CMS: {score:+.2f} ({stability})"]
    if opening:
        parts.append(f"opening: {opening}")
    if alerts:
        parts.append(f"⚠ {alerts[0]}")
    if all_motifs:
        weights = [(_THEME_WEIGHTS.get(t, 1), t) for t in all_motifs]
        top = [t for _, t in sorted(weights, reverse=True)][:2]
        parts.append(f"motifs: {', '.join(top)}")
    if patterns:
        parts.append(f"projects: {patterns[0]}")
    if rejected:
        r = rejected[0]
        parts.append(f"over {r['move']} ({r['reason']})")
    return " | ".join(parts)


def llm_articulate_packet(packet: dict) -> str:
    """Single LLM call to render the reasoning packet as fluent prose."""
    move     = packet["chosen_move"]
    opening  = packet["opening"].replace("_", " ")
    all_themes = list(dict.fromkeys(
        (packet.get("active_themes") or []) + (packet.get("variation_themes") or [])
    ))
    themes   = ", ".join(all_themes) or "none"
    patterns = "; ".join(packet["future_patterns"]) or "none"
    rejected = "; ".join(f"{r['move']} ({r['reason']})" for r in packet["rejected_moves"]) or "none"
    alerts   = "; ".join(packet["tactic_alerts"]) or "none"

    # Include top variation line for context
    top_var_line = ""
    for cand in (packet.get("all_variations") or []):
        if cand.get("is_chosen") and cand.get("lines"):
            best_line = cand["lines"][0]
            pv_preview = " ".join(best_line.get("pv_san", [])[:4])
            if pv_preview:
                top_var_line = f"- Top PV line: {pv_preview} (themes: {', '.join(best_line.get('themes', [])) or 'none'})"
            break

    prompt = f"""Selyrion chose {move} in a chess game.

Reasoning packet:
- CMS lookahead score: {packet['lookahead_score']:+.2f} (variance: {packet['lookahead_variance']:.2f})
- Engine evaluation: {packet['engine_cp']:+d} cp
- Opening: {opening}
- Active tactical themes: {themes}
- CMS future patterns: {patterns}
{top_var_line}
- Rejected alternatives: {rejected}
- Tactical alerts: {alerts}

Write ONE sentence explaining why Selyrion chose this move.
Be specific to the data. Reference the opening or tactical theme if present.
Do not use chess clichés like "developing pieces" or "controlling the center" unless directly supported."""

    return _llm_call(prompt, prefix=f"  {SEL_COL}Articulating... {R}")


def motif_strategy_match(motifs: list[str], board: chess.Board,
                         tactic_info: dict) -> tuple[list[str], list[str]]:
    """Split motifs into active (coherent with strategy) and observed (metadata only).

    Returns (active_motifs, observed_motifs).
    Active motifs enter SCOS prompt. Observed are logged but don't affect cognition.
    """
    strategy = _positional_strategy(board, tactic_info)
    active, observed = [], []
    for motif in motifs:
        coherent_strategies = _MOTIF_STRATEGY_MAP.get(motif, [])
        if any(s in strategy for s in coherent_strategies):
            active.append(motif)
        else:
            observed.append(motif)
    return active, observed


_THEME_WEIGHTS = {
    "checkmate":          10,
    "back-rank weakness":  8,
    "pin":                 7,
    "skewer":              7,
    "trapped piece":       6,
    "fork":                5,
    "discovered attack":   5,
    "deflection":          5,
    "x-ray attack":        4,
    "sacrifice":           4,
    "passed pawn":         4,
    "pawn promotion":      4,
    "kingside attack":     3,
    "queenside attack":    3,
    "zugzwang":            3,
    "compensation":        3,
    "outpost":             3,
    "initiative":          3,
    "tempo":               3,
    "open file":           2,
    "isolated pawn":       1,
    "prophylaxis":         2,
}

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
    "qwen2.5:7b":  0.50,
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
    "llama3:8b":   "\033[36m",
    "gemma3:4b":   "\033[38;5;141m",
    "phi4-mini":   "\033[33m",
    "qwen3:4b":    "\033[38;5;214m",
    "qwen2.5:14b": "\033[38;5;208m",
    "qwen2.5:7b":  "\033[38;5;202m",
}
_PARL_MODEL_ROLES = {
    "llama3:8b":   "synthesis arbitrator — weigh all perspectives, seek integration",
    "gemma3:4b":   "symbolic resonance — attend to meaning, metaphor, conceptual depth",
    "phi4-mini":   "analytical discipline — precise reasoning, structured logic",
    "qwen3:4b":    "broad knowledge — history, theory, comparative evidence",
    "qwen2.5:14b": "lead analyst — definitive assessment, concrete chess terms",
    "qwen2.5:7b":  "lead analyst — definitive assessment, concrete chess terms",
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
                             cms_ctx: str,
                             candidates: list = None,
                             tactic_info: dict = None,
                             pos_info: dict = None,
                             recent: list[str] = None) -> str:
    role = _PARL_MODEL_ROLES.get(model, "independent reasoner")

    # A: Position-grounded block
    pos_block = ""
    if pos_info:
        bal = pos_info.get("material_balance", 0)
        bal_str = f"+{bal}" if bal > 0 else str(bal)
        pos_block = (
            f"\nPOSITIONAL FACTS:\n"
            f"  Material balance (our perspective): {bal_str} points\n"
            f"  Our king: {pos_info.get('our_king','?')}  |  "
            f"Their king: {pos_info.get('their_king','?')}\n"
            f"  Our legal moves: {pos_info.get('our_mobility','?')}\n"
        )

    # B: Tactical override block
    tactic_block = ""
    if tactic_info and tactic_info.get("alerts"):
        alert_lines = "\n".join(f"  ⚠ {a}" for a in tactic_info["alerts"])
        tactic_block = (
            f"\nTACTICAL ALERTS (treat as hard constraints — address these first):\n"
            f"{alert_lines}\n"
        )

    # D: Candidate moves block
    cand_block = ""
    cand_count = 0
    if candidates:
        cand_count = len(candidates)
        cand_lines = "\n".join(
            f"  {i+1}. {san}  ({cp:+d} cp)"
            for i, (_, san, cp) in enumerate(candidates)
        )
        cand_block = (
            f"\nSTOCKFISH TOP CANDIDATES (engine-evaluated, legal moves):\n"
            f"{cand_lines}\n"
            f"Pick one of these as your recommended move.\n"
        )

    diversity_block = ""
    if recent:
        recent_fmt = "\n".join(f"  - \"{c}\"" for c in recent)
        diversity_block = (
            f"\nRECENT PARLIAMENT CONCLUSIONS (do NOT echo — reason fresh):\n"
            f"{recent_fmt}\n"
            f"If this position calls for a different plan, say so explicitly.\n"
        )

    cand_move_field = ""
    if cand_count:
        cand_move_field = f'\n  "candidate_move": {1 if cand_count == 1 else "1-" + str(cand_count)} (integer — your recommended move number),'

    return f"""You are a member of Selyrion's chess parliament.
Your role: {role}

Position (FEN): {fen}
It is {color_name}'s turn.
{pos_block}{tactic_block}{cand_block}
Chess knowledge substrate (CMS):
{cms_ctx}
{diversity_block}
Reason independently. Address tactical alerts first (they are immediate constraints).
Then identify the best strategic plan for {color_name}.
Extract causal insights as proposed relations.

Respond in JSON only — no other text:
{{{cand_move_field}
  "conclusion": "recommended move + strategic plan (be specific, 1-2 sentences)",
  "reasoning": "your reasoning including why you chose this move (2-3 sentences)",
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
                       chess_conn: sqlite3.Connection,
                       candidates: list = None) -> dict:
    """Run round-1 parliament deliberation on current board position.

    Returns consensus dict with keys: conclusion, confidence, agreement, insights,
    proposed_moves (list of candidate indices proposed by models).
    """
    models = [m.strip() for m in args.parliament_models.split(",") if m.strip()]
    fen = board.fen()
    color_name = "white" if board.turn == chess.WHITE else "black"

    # B: Tactical pre-scan before any model fires
    tactic_info = tactical_scan(board)
    pos_info    = positional_summary(board)

    if tactic_info["alerts"]:
        print(f"\n  {WARN_COL}Tactical alerts: {'; '.join(tactic_info['alerts'][:2])}{R}")

    print(f"\n  {BOLD}{CMS_COL}Parliament deliberating...{R}")
    print(f"  {'─'*54}")

    recent = _parl_recent_conclusions(sess_id)
    positions: dict[str, dict] = {}
    proposed_move_votes: list[int] = []  # D: collect candidate_move indices

    for model in models:
        col = _PARL_MODEL_COLORS.get(model, CMS_COL)
        print(f"  {col}{model:20}{R}", end=" ", flush=True)
        prompt = _parl_first_pass_prompt(model, fen, color_name, cms_ctx,
                                          candidates, tactic_info, pos_info, recent)
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
            # D: Extract candidate_move vote
            cm = data.get("candidate_move")
            if cm is not None and candidates:
                try:
                    idx = int(float(cm)) - 1
                    if 0 <= idx < len(candidates):
                        proposed_move_votes.append(idx)
                except (ValueError, TypeError):
                    pass
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

    # D: Tally parliament's proposed candidate moves
    proposed_moves = []
    if proposed_move_votes and candidates:
        from collections import Counter
        vote_counts = Counter(proposed_move_votes)
        # Ranked by votes then confidence of lead model
        proposed_moves = [idx for idx, _ in vote_counts.most_common()]
        top_vote_idx   = proposed_moves[0]
        top_san        = candidates[top_vote_idx][1] if top_vote_idx < len(candidates) else "?"
        vote_str       = ", ".join(
            f"{candidates[i][1]}×{c}" for i, c in vote_counts.most_common()
            if i < len(candidates)
        )
        print(f"  {CMS_COL}Parliament move votes: {vote_str} → recommends {top_san}{R}")

    return {
        "conclusion":    consensus_text,
        "confidence":    avg_conf,
        "agreement":     agreed,
        "total":         len(models),
        "lead":          lead_model,
        "insights":      insights,
        "positions":     positions,
        "proposed_moves": proposed_moves,   # D: ordered list of candidate indices by votes
        "tactic_alerts":  tactic_info.get("alerts", []),
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


_cms_conn_live = None

def _get_cms_conn():
    global _cms_conn_live
    if _cms_conn_live is None:
        try:
            _cms_conn_live = sqlite3.connect(str(Path.home() / "resonance_v11.db"), timeout=5)
        except Exception:
            pass
    return _cms_conn_live


def live_annotate_to_cms(san: str, motifs: list, narration: str,
                          opening: str, ply: int):
    """Write live-game positional lessons to CMS chess domain after each Selyrion move."""
    cms = _get_cms_conn()
    if cms is None or not motifs:
        return
    try:
        # Ensure anchors + relations tables accessible
        def write_rel(subj, pred, obj, conf):
            # Get or create anchor IDs
            def get_or_create(name):
                row = cms.execute(
                    "SELECT id FROM anchors WHERE canonical=?", (name,)
                ).fetchone()
                if row:
                    return row[0]
                aid = "anc." + hashlib.md5(name.encode()).hexdigest()[:12]
                cms.execute(
                    "INSERT OR IGNORE INTO anchors (id, canonical, domain_tags, maturity) "
                    "VALUES (?,?,?,?)", (aid, name, "chess", 1.0))
                return aid

            sid = get_or_create(subj[:120])
            oid = get_or_create(obj[:120])
            rid = "rel." + hashlib.md5(f"{subj}{pred}{obj}".encode()).hexdigest()[:12]
            cms.execute("""
                INSERT INTO relations
                    (id, subject_id, object_id, predicate, confidence, seen_count,
                     domain_tags, source_dataset)
                VALUES (?,?,?,?,?,1,'chess','live_game')
                ON CONFLICT(id) DO UPDATE SET
                    seen_count=seen_count+1,
                    confidence=MIN(0.98, confidence+0.01)
            """, (rid, sid, oid, pred, 0.75))

        # Write one relation per active motif
        for motif in motifs:
            write_rel(f"move: {san}", "demonstrates", motif, 0.75)
            if opening and opening != "unknown_opening":
                write_rel(opening, "produces_motif", motif, 0.75)

        # Write narration-derived concept if strong enough
        if narration and len(narration) > 40:
            key_phrase = narration[:60].rstrip().rstrip('.,;')
            write_rel(f"move: {san}", "described_as", key_phrase[:80], 0.65)

        cms.commit()
    except Exception:
        pass  # never block the game


def _cms_gap_fill(san: str, packet: dict, opening: str):
    """After Selyrion's move, write any missing CMS relations discovered during lookahead.

    Checks existence before writing — never duplicates. Uses low confidence so
    these gap-fill entries are weak priors that strengthen only when seen again.
    Never blocks the game.
    """
    cms = _get_cms_conn()
    if cms is None:
        return
    try:
        # Gather themes: active (from position) + variation_themes (from PV walk)
        active = set(packet.get("active_themes") or [])
        variation_themes = set(packet.get("variation_themes") or [])
        immediate_themes = set(packet.get("immediate_variation_themes") or []) | active
        all_themes = active | variation_themes
        if not all_themes:
            return

        def _get_or_create(name: str) -> str:
            row = cms.execute("SELECT id FROM anchors WHERE canonical=?", (name,)).fetchone()
            if row:
                return row[0]
            aid = "anc." + hashlib.md5(name.encode()).hexdigest()[:12]
            cms.execute(
                "INSERT OR IGNORE INTO anchors (id, canonical, domain_tags, maturity) "
                "VALUES (?,?,?,?)", (aid, name, "chess", 1.0))
            return aid

        def _upsert_rel(subj: str, pred: str, obj: str, conf: float):
            sid = _get_or_create(subj[:120])
            oid = _get_or_create(obj[:120])
            rid = "rel." + hashlib.md5(f"{subj}{pred}{obj}".encode()).hexdigest()[:12]
            cms.execute("""
                INSERT INTO relations
                    (id, subject_id, object_id, predicate, confidence, seen_count,
                     domain_tags, source_dataset)
                VALUES (?,?,?,?,?,1,'chess','gap_fill')
                ON CONFLICT(id) DO UPDATE SET
                    seen_count = seen_count + 1,
                    confidence = MIN(0.95, confidence + 0.005)
            """, (rid, sid, oid, pred, conf))

        def _exists(subj: str, pred: str, obj: str) -> bool:
            rid = "rel." + hashlib.md5(f"{subj}{pred}{obj}".encode()).hexdigest()[:12]
            return bool(cms.execute("SELECT 1 FROM relations WHERE id=?", (rid,)).fetchone())

        for theme in all_themes:
            weight = _THEME_WEIGHTS.get(theme, 1)

            # opening → produces_motif → theme
            if opening and opening != "unknown_opening":
                if not _exists(opening, "produces_motif", theme):
                    _upsert_rel(opening, "produces_motif", theme, 0.60)

            # theme → anticipates → outcome
            if weight >= 8:
                outcome = "decisive advantage"
            elif weight >= 4:
                outcome = "tactical advantage"
            elif weight >= 2:
                outcome = "positional advantage"
            else:
                outcome = None
            if outcome and not _exists(theme, "anticipates", outcome):
                _upsert_rel(theme, "anticipates", outcome, 0.60)

            # move → demonstrates → theme (only causally immediate — ply ≤ 2 of PV)
            if theme in immediate_themes and weight >= 4:
                if not _exists(f"move: {san}", "demonstrates", theme):
                    _upsert_rel(f"move: {san}", "demonstrates", theme, 0.70)
            # move → leads_to_line_theme → theme (deeper PV correlation, not direct cause)
            elif theme not in immediate_themes and weight >= 3:
                if not _exists(f"move: {san}", "leads_to_line_theme", theme):
                    _upsert_rel(f"move: {san}", "leads_to_line_theme", theme, 0.55)

        cms.commit()
    except Exception:
        pass


def write_move(conn, sess_id, ply, move, san, board_after, color, narration,
               active_motifs=None, opening="unknown_opening"):
    # Use strategy-filtered motifs if provided (SCOS path), else detect from text
    motifs = active_motifs if active_motifs is not None else detect_motifs(narration)
    conn.execute("""
        INSERT OR IGNORE INTO chess_live_moves
            (id, session_id, ply, san, uci, color, narration, motifs, fen_after, ts)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (f"{sess_id}:{ply}", sess_id, ply, san, move.uci(),
          color, narration, json.dumps(motifs), board_after.fen(), time.time()))
    conn.commit()
    # Annotate Selyrion's moves into CMS chess domain
    if color in ("white", "black"):
        live_annotate_to_cms(san, motifs, narration, opening, ply)
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

    parl_active = args.parliament and not args.no_parliament
    synth_conn = None
    if parl_active:
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
        if not args.no_db:
            conn.execute("""INSERT INTO chess_live_sessions
                            (id, player, llm_model, player_color, started_at)
                            VALUES (?,?,?,?,?)""",
                         (sess_id, "Selyrion", args.model, args.selyrion_color, time.time()))
            conn.commit()
        # Write vs-human flag for live viewer
        _human_color_str = "black" if args.selyrion_color == "white" else "white"
        if args.vs_human:
            Path("/tmp/selyrion_vs_human.txt").write_text(_human_color_str)
        else:
            Path("/tmp/selyrion_vs_human.txt").unlink(missing_ok=True)

        # Live state file — used by viewer when --no-db (no DB session written)
        _STATE_FILE = Path("/tmp/selyrion_live_state.json")
        _live_history: list[str] = []
        # Write initial state immediately so viewer shows board before first move
        import json as _json_init
        _STATE_FILE.write_text(_json_init.dumps({
            "session_id": sess_id, "sel_color": args.selyrion_color,
            "ply": 0, "san": None, "color": None,
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1",
            "narration": "Selyrion is deliberating its first move...",
            "motifs": [], "history": [], "turn_color": "white",
            "vs_human": args.vs_human,
            "human_color": _human_color_str if args.vs_human else "black",
            "parliament": [], "cms_anchors": [],
        }))
        def _write_live_state(fen, san, narration, motifs, color, ply):
            import json as _json
            _live_history.append(san)
            turn = "white" if len(_live_history) % 2 == 0 else "black"
            state = {
                "session_id": sess_id,
                "sel_color": args.selyrion_color,
                "ply": ply, "san": san, "color": color,
                "fen": fen, "narration": narration,
                "motifs": motifs or [],
                "history": list(_live_history),
                "turn_color": turn,
                "vs_human": args.vs_human,
                "human_color": _human_color_str if args.vs_human else "black",
                "parliament": [], "cms_anchors": [],
            }
            _STATE_FILE.write_text(_json.dumps(state))
        board = chess.Board(args.start_fen) if args.start_fen else chess.Board()
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
        live_opening = "unknown_opening"

        # Play forced opening moves silently before handing off to main loop
        if args.force_moves:
            for uci in args.force_moves.split(","):
                uci = uci.strip()
                if not uci:
                    continue
                try:
                    mv = chess.Move.from_uci(uci)
                    if mv in board.legal_moves:
                        san = board.san(mv)
                        board.push(mv)
                        move_history.append(san)
                        ply += 1
                except Exception:
                    pass

    auto_mode = args.auto

    parl_active = args.parliament and not args.no_parliament
    if args.symbolic_pick:
        picks_label = "+symbolic-CMS"
    elif args.scos:
        picks_label = "+SCOS"
    elif args.selyrion_picks:
        picks_label = "+LLM picks"
    else:
        picks_label = "Stockfish"
    if args.llm_articulate:
        picks_label += "+articulate"
    audit_n = args.parliament_audit
    parl_label = (f"+Parliament-audit/{audit_n}" if parl_active and audit_n > 0
                  else ("+Parliament" if parl_active else "no parliament"))
    print(f"\n{BOLD}{'═'*60}{R}")
    print(f"  {SEL_COL}{BOLD}Selyrion{R}{SEL_COL} ELO {args.selyrion_elo}"
          + (" [YOU]" if human_mode else f" [{picks_label}] [{parl_label}]") + f"{R}")
    print(f"  {OPP_COL}vs Stockfish ELO {args.opponent_elo}{R}")
    print(f"{'═'*60}")
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

                    cms_ctx     = build_selyrion_context(conn)
                    tactic_info = tactical_scan(board) if (args.scos or args.symbolic_pick) else {}
                    parl_consensus  = None
                    reasoning_packet = None

                    # ── Parliament audit (optional, every N moves) ────────────
                    run_parliament = False
                    if parl_active and synth_conn:
                        if args.parliament_audit > 0:
                            run_parliament = (ply % args.parliament_audit == 0)
                        else:
                            run_parliament = True  # --parliament without --parliament-audit = every move

                    candidates = stockfish_top_n(engine, board, args.selyrion_elo,
                                                 args.top_n, args.think_time)
                    print(f"  {SEL_COL}Stockfish candidates:{R}")
                    for i, (_, s, cp) in enumerate(candidates):
                        print(f"    {i+1}. {s:8}  {cp:+d} cp")

                    if run_parliament:
                        parl_consensus = parliament_consult(
                            board, cms_ctx, synth_conn, sess_id,
                            ply + 1, conn, candidates)
                        print(f"\n  {BOLD}{'═'*56}{R}")
                        print(f"  {BOLD}PARLIAMENT AUDIT (ply {ply+1}){R}")
                        print(f"  {'─'*56}")
                        lead = parl_consensus["lead"]
                        col  = _PARL_MODEL_COLORS.get(lead, CMS_COL)
                        for line in _wrap_text(parl_consensus["conclusion"], 54):
                            print(f"    {line}")
                        print(f"  Confidence: {parl_consensus['confidence']:.2f}"
                              f"  | Lead: {col}{lead}{R}")
                        print(f"  {BOLD}{'═'*56}{R}\n")

                    # ── Move selection ────────────────────────────────────────
                    _cur_themes = (tactic_info.get("motifs") if tactic_info else None)

                    if args.symbolic_pick:
                        # Pure CMS symbolic decision — no LLM
                        move, san, reasoning_packet = selyrion_symbolic_pick(
                            board, candidates, engine,
                            opening=live_opening,
                            active_themes=_cur_themes,
                            tactic_info=tactic_info)

                    elif args.scos:
                        # CMS lookahead + single LLM pick
                        move, san, scos_motifs = selyrion_scos_pick(
                            board, candidates, tactic_info, parl_consensus,
                            engine=engine,
                            opening=live_opening,
                            active_themes=_cur_themes)
                        _cur_themes = scos_motifs or _cur_themes

                    elif args.selyrion_picks:
                        # LLM deliberates from Stockfish candidates
                        print(f"  {SEL_COL}Selyrion deliberating...{R}", flush=True)
                        move, san = selyrion_pick_move(board, candidates, cms_ctx,
                                                       parl_consensus)
                    else:
                        # Stockfish plays directly
                        print(f"  {SEL_COL}Selyrion (Stockfish)...{R}", flush=True)
                        move = stockfish_move(engine, board, args.selyrion_elo, args.think_time)
                        san  = board.san(move)

                board.push(move)
                node = node.add_variation(move)
                move_history.append(san)
                ply += 1

                # Classify opening at ply 10
                if ply == 10:
                    live_opening = classify_opening(
                        [m.uci() for m in board.move_stack])
                    if live_opening != "unknown_opening":
                        print(f"  {DIM}Opening classified: {live_opening}{R}")

                if not human_mode:
                    print(f"  {SEL_COL}{BOLD}Selyrion: {san}{R}")
                    # ── Narration ─────────────────────────────────────────────
                    if reasoning_packet:
                        if args.llm_articulate:
                            narration = llm_articulate_packet(reasoning_packet)
                        else:
                            narration = langeng_narrate_packet(reasoning_packet)
                        print(f"  {SEL_COL}{narration}{R}")
                    else:
                        print(f"  {SEL_COL}", end="", flush=True)
                        narration = (narrate_langeng(board_before, move, _cur_themes, live_opening, True)
                                     or narrate_selyrion(board_before, move, cms_ctx, parl_consensus))
                        print(R, end="")
                    node.comment = narration
                    motifs = []
                    if not args.no_db:
                        try:
                            motifs = write_move(conn, sess_id, ply, move, san, board,
                                                color_name, narration,
                                                active_motifs=_cur_themes,
                                                opening=live_opening)
                        except sqlite3.OperationalError:
                            motifs = []
                        if motifs:
                            print(f"  {CMS_COL}  [CMS: {', '.join(motifs)}]{R}")
                    _write_live_state(board.fen(), san, narration, motifs, color_name, ply)
                    # ── Continuous learning: fill CMS gaps from lookahead ──────
                    if reasoning_packet and args.symbolic_pick:
                        _cms_gap_fill(san, reasoning_packet, live_opening)
                else:
                    motifs = []
                    if not args.no_db:
                        try:
                            motifs = write_move(conn, sess_id, ply, move, san, board,
                                                color_name, f"Human played {san}")
                        except sqlite3.OperationalError:
                            motifs = []
                    _write_live_state(board.fen(), san, f"Human played {san}", motifs, color_name, ply)

                print_board(board, last_move=move, flip=flip_board)

            else:
                # ── Opponent's turn ──────────────────────────────────────────
                if args.vs_human:
                    # Human submits move via live viewer web UI
                    _move_file = Path("/tmp/selyrion_human_move.txt")
                    _move_file.unlink(missing_ok=True)
                    print(f"  {OPP_COL}{BOLD}Waiting for your move at http://100.76.170.8:7890 ...{R}", flush=True)
                    while True:
                        if _move_file.exists():
                            raw = _move_file.read_text().strip()
                            _move_file.unlink(missing_ok=True)
                            if raw.lower() in ("q", "quit", "resign"):
                                print("  You resigned.")
                                engine.quit()
                                conn.close()
                                return
                            try:
                                move = board.parse_san(raw)
                                if move in board.legal_moves:
                                    break
                            except Exception:
                                pass
                            try:
                                move = chess.Move.from_uci(raw.lower())
                                if move in board.legal_moves:
                                    break
                            except Exception:
                                pass
                            print(f"  {WARN_COL}Invalid move '{raw}' — try again via viewer{R}", flush=True)
                        time.sleep(0.5)
                    san = board.san(move)
                else:
                    if not auto_mode and not human_mode:
                        pass
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

                if args.vs_human:
                    print(f"  {OPP_COL}{BOLD}You: {san}{R}")
                else:
                    print(f"  {OPP_COL}{BOLD}Stockfish: {san}{R}")
                    print(f"  {OPP_COL}", end="", flush=True)
                    narration = (narrate_langeng(board_before, move, [], live_opening, False)
                                 or narrate_opponent(board_before, move))
                    print(R, end="")
                    node.comment = narration
                motifs = []
                if not args.no_db:
                    try:
                        motifs = write_move(conn, sess_id, ply, move, san, board,
                                            color_name, narration if not args.vs_human else f"Human played {san}")
                    except sqlite3.OperationalError:
                        motifs = []
                    if motifs:
                        print(f"  {CMS_COL}  [CMS: {', '.join(motifs)}]{R}")
                _write_live_state(board.fen(), san,
                                  narration if not args.vs_human else f"Selyrion played {san}",
                                  motifs, color_name, ply)

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

    # ── Self-reflection loop ──────────────────────────────────────────────────
    if parl_active:
        self_reflect(conn, sess_id, sel_color, result)

    pgn_path = export_and_ingest(board, game, result)

    if args.auto:
        ans = "n"
    else:
        try:
            ans = input(f"\n  {BOLD}Run LLM review? (y/n): {R}").strip().lower()
        except EOFError:
            ans = "n"
    if ans == "y":
        subprocess.run([sys.executable, "chess_llm_review.py",
                        "--pgn", str(pgn_path), "--model", args.model])
    conn.close()


def self_reflect(conn: sqlite3.Connection, sess_id: str,
                 sel_color: chess.Color, result: str):
    """Post-game self-reflection: re-evaluate each parliament move with Stockfish,
    identify failures and correct motifs, write lessons back to CMS + claudecode.db."""

    print(f"\n  {BOLD}{CMS_COL}{'═'*56}{R}")
    print(f"  {BOLD}{CMS_COL}Self-reflection loop running...{R}")
    print(f"  {CMS_COL}{'─'*56}{R}")

    # Gather all Selyrion plies that had parliament deliberation
    rows = conn.execute("""
        SELECT d.ply, d.fen, d.conclusion, d.confidence, d.key_factors, d.key_insight,
               m.san, m.uci, m.motifs
        FROM parliament_move_deliberations d
        LEFT JOIN chess_live_moves m ON m.session_id = d.session_id AND m.ply = d.ply
        WHERE d.session_id = ? AND d.is_consensus = 1
        ORDER BY d.ply
    """, (sess_id,)).fetchall()

    if not rows:
        print(f"  {DIM}No parliament deliberations found for this session.{R}")
        return

    # Spin up a fresh Stockfish instance for hindsight analysis
    sf_path = find_stockfish()
    if not sf_path:
        print(f"  {WARN_COL}Stockfish not found — skipping move re-evaluation.{R}")
        sf_engine = None
    else:
        sf_engine = chess.engine.SimpleEngine.popen_uci(sf_path)

    failures   = []  # (ply, san, parl_conclusion, cp_loss, sf_best)
    good_calls = []  # (ply, san, parl_conclusion)
    motif_errors: dict[str, int] = {}   # motif → times flagged wrongly
    motif_hits:   dict[str, int] = {}   # motif → times correctly flagged

    BLUNDER_THRESHOLD = 100   # cp loss considered a failure
    MISTAKE_THRESHOLD = 50

    for ply, fen, conclusion, conf, key_factors_json, key_insight, san, uci, motifs_json in rows:
        board_at = chess.Board(fen) if fen else None
        if board_at is None or uci is None:
            continue

        # Stockfish hindsight: what was the best move at this position?
        sf_best_san = None
        cp_loss = 0
        if sf_engine and board_at:
            try:
                # Score before the move
                info_before = sf_engine.analyse(board_at, chess.engine.Limit(depth=16))
                score_before = info_before.get("score")
                sf_best_move = info_before.get("pv", [None])[0]
                if sf_best_move:
                    sf_best_san = board_at.san(sf_best_move)

                # Score after actual move played
                actual_move = chess.Move.from_uci(uci)
                if actual_move in board_at.legal_moves:
                    board_after = board_at.copy()
                    board_after.push(actual_move)
                    info_after = sf_engine.analyse(board_after, chess.engine.Limit(depth=16))
                    score_after = info_after.get("score")

                    if score_before and score_after:
                        cp_before = score_before.relative.score(mate_score=10000) or 0
                        # After the move, it's opponent's turn — negate
                        cp_after = -(score_after.relative.score(mate_score=10000) or 0)
                        cp_loss = cp_before - cp_after
            except Exception:
                pass

        # Classify move quality
        actual_san = san or uci
        if cp_loss >= BLUNDER_THRESHOLD:
            failures.append((ply, actual_san, conclusion or "", cp_loss, sf_best_san))
        elif cp_loss >= MISTAKE_THRESHOLD:
            failures.append((ply, actual_san, conclusion or "", cp_loss, sf_best_san))
        else:
            good_calls.append((ply, actual_san, conclusion or ""))

        # Motif accuracy: were flagged motifs relevant?
        try:
            motifs = json.loads(motifs_json or "[]")
        except Exception:
            motifs = []
        for motif in motifs:
            if cp_loss >= MISTAKE_THRESHOLD:
                motif_errors[motif] = motif_errors.get(motif, 0) + 1
            else:
                motif_hits[motif]   = motif_hits.get(motif, 0) + 1

        # Write per-move Stockfish eval back to DB
        conn.execute("""
            UPDATE parliament_move_deliberations
            SET stockfish_eval = ?, outcome = ?
            WHERE session_id = ? AND ply = ? AND is_consensus = 1
        """, (float(-cp_loss), "blunder" if cp_loss >= BLUNDER_THRESHOLD
              else "mistake" if cp_loss >= MISTAKE_THRESHOLD else "ok",
              sess_id, ply))

    conn.commit()
    if sf_engine:
        sf_engine.quit()

    # ── Print reflection report ──────────────────────────────────────────────
    game_outcome = ("win" if (result == "1-0" and sel_color == chess.WHITE)
                    or (result == "0-1" and sel_color == chess.BLACK)
                    else "loss" if result != "*" else "incomplete")

    print(f"\n  Game outcome for Selyrion: {BOLD}{game_outcome}{R}")
    print(f"  Parliament plies reviewed: {len(rows)}")
    print(f"  Good decisions: {OK_COL}{len(good_calls)}{R}  |  "
          f"Errors: {WARN_COL}{len(failures)}{R}")

    if failures:
        print(f"\n  {BOLD}Failures (parliament led Selyrion wrong):{R}")
        for ply, san, conclusion, cp_loss, sf_best in failures:
            severity = f"{WARN_COL}mistake{R}" if cp_loss < BLUNDER_THRESHOLD else f"\033[31mblunder{R}"
            sf_note  = f" (SF best: {sf_best})" if sf_best and sf_best != san else ""
            print(f"    Ply {ply:2d} {san:6} {severity} −{cp_loss:.0f}cp{sf_note}")
            if conclusion:
                print(f"    {DIM}Parliament said: \"{conclusion[:80]}\"{R}")

    if motif_errors:
        print(f"\n  {BOLD}Motifs flagged during losing moves (possibly misleading):{R}")
        for motif, count in sorted(motif_errors.items(), key=lambda x: -x[1]):
            hit = motif_hits.get(motif, 0)
            print(f"    {motif:25}  wrong={count}  correct={hit}")

    # ── Write lessons to CMS ─────────────────────────────────────────────────
    cms_conn = sqlite3.connect(str(Path.home() / "resonance_v11.db"), timeout=10)
    cms_conn.execute("PRAGMA journal_mode=WAL")

    def cms_write_lesson(subj, pred, obj, conf=0.75):
        aid = lambda c: "a." + hashlib.md5(c.encode()).hexdigest()[:12]
        sid, oid = aid(subj), aid(obj)
        rid = "r." + hashlib.md5(f"{sid}{pred}{oid}chess_reflect".encode()).hexdigest()[:12]
        cms_conn.execute("""
            INSERT OR IGNORE INTO anchors (id, canonical, maturity)
            VALUES (?,?,1.0)
        """, (sid, subj))
        cms_conn.execute("""
            INSERT OR IGNORE INTO anchors (id, canonical, maturity)
            VALUES (?,?,1.0)
        """, (oid, obj))
        cms_conn.execute("""
            INSERT INTO relations (id, subject_id, object_id, predicate,
                confidence, seen_count, domain_tags, source_dataset)
            VALUES (?,?,?,?,?,1,?,?)
            ON CONFLICT(id) DO UPDATE SET
                seen_count = seen_count + 1,
                confidence = MAX(confidence, excluded.confidence)
        """, (rid, sid, oid, pred, conf, "chess", "self_reflect"))

    lessons_written = 0
    for ply, san, conclusion, cp_loss, sf_best in failures:
        if sf_best and sf_best != san:
            cms_write_lesson(f"selyrion played {san}", "leads_to",
                             f"cp loss {int(cp_loss//50)*50}+", conf=0.80)
            cms_write_lesson(f"parliament conclusion: {conclusion[:60]}",
                             "resulted_in", "positional error", conf=0.72)
            if sf_best:
                cms_write_lesson(f"stockfish preferred {sf_best}",
                                 "stronger_than", f"selyrion played {san}", conf=0.85)
            lessons_written += 1

    # Motif penalty lessons
    for motif, count in motif_errors.items():
        if count >= 2:
            cms_write_lesson(f"motif: {motif}", "misapplied_during", "parliament error", conf=0.70)

    # Good pattern reinforcement
    for ply, san, conclusion in good_calls[:3]:
        cms_write_lesson(f"parliament conclusion: {conclusion[:60]}",
                         "enabled", "accurate move selection", conf=0.75)

    cms_conn.commit()
    cms_conn.close()

    # ── Write session summary to claudecode.db ───────────────────────────────
    try:
        cc = sqlite3.connect(_CLAUDECODE_DB)
        summary = (f"Self-reflection: sess={sess_id} outcome={game_outcome} "
                   f"errors={len(failures)}/{len(rows)} plies reviewed. "
                   f"Worst blunder: {failures[0][1] if failures else 'none'} "
                   f"(−{failures[0][3]:.0f}cp). "
                   f"Motif errors: {list(motif_errors.keys())[:3]}. "
                   f"Lessons written to CMS: {lessons_written}.")
        body_id = "disc." + hashlib.md5(summary[:40].encode()).hexdigest()[:8]
        cc.execute("""
            INSERT OR IGNORE INTO discoveries
                (id, session_id, body, tags, importance, created_at)
            VALUES (?,?,?,?,?,?)
        """, (body_id, sess_id, summary[:1000], "chess,reflection,parliament", 3, time.time()))
        cc.commit()
        cc.close()
    except Exception:
        pass

    print(f"\n  {CMS_COL}{lessons_written} lessons written to CMS.{R}")
    print(f"  {BOLD}{CMS_COL}{'═'*56}{R}\n")


if __name__ == "__main__":
    main()
