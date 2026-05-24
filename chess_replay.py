#!/usr/bin/env python3
"""
chess_replay.py — Experiential replay of ingested games with parliament deliberation.

Replays historical games position-by-position. At turning points (eval shift > threshold)
and every N plies, runs full parliament deliberation with Stockfish as epistemic anchor.

Parliament members:
  LLMs              — strategic reasoning, symbolic abstraction
  Stockfish         — tactical reality anchor (not a language participant)
  CMS               — persistent causal context (injected, not a speaker)

Stores per-position reasoning provenance in parliament_move_deliberations.
Compares parliament proposals to historical moves and engine best.
Writes validated insights to synth.db for HITL merge to CMS.
Mirrors summaries to claudecode.db as discoveries.

Usage:
  python3 chess_replay.py --games 50                    # replay 50 games from DB
  python3 chess_replay.py --game-id game.abc123          # replay specific game
  python3 chess_replay.py --pgn myfile.pgn              # replay from PGN file
  python3 chess_replay.py --games 100 --batch            # batch mode (no pausing)
  python3 chess_replay.py --games 0 --stats             # show replay stats
  python3 chess_replay.py --resume                       # resume interrupted batch
"""

import sys, re, json, sqlite3, hashlib, time, argparse, shutil, subprocess
from pathlib import Path
from collections import defaultdict
from datetime import datetime

try:
    import chess, chess.pgn, chess.engine
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "python-chess", "-q", "--break-system-packages"])
    import chess, chess.pgn, chess.engine

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

from adaptive_policy import AdaptivePolicy, DebatePolicy

# ── CLI ───────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("--games",         type=int, default=10,
                    help="Number of games to replay (0=stats only)")
parser.add_argument("--game-id",       default=None, help="Replay a specific game by ID")
parser.add_argument("--pgn",           default=None, help="Replay from PGN file instead of DB")
parser.add_argument("--db",            default=str(Path.home() / "resonance_v11.db"))
parser.add_argument("--synth-db",      default=str(Path.home() / "selyrion_synth.db"))
parser.add_argument("--batch",         action="store_true", help="No pausing between games")
parser.add_argument("--resume",        action="store_true",
                    help="Skip games already replayed (has parliament_move_deliberations)")
parser.add_argument("--models",        default="qwen2.5:14b,llama3.1:8b,gemma3:4b,phi4-mini",
                    help="LLM parliament models")
parser.add_argument("--stockfish-depth", type=int, default=15,
                    help="Stockfish search depth for parliamentary analysis")
parser.add_argument("--think-time",    type=float, default=0.3,
                    help="Stockfish think time per position")
parser.add_argument("--deliberate-every", type=int, default=6,
                    help="Run parliament every N plies (in addition to turning points)")
parser.add_argument("--eval-threshold", type=float, default=0.5,
                    help="Centipawn shift to trigger turning-point parliament (in pawns)")
parser.add_argument("--no-parliament", action="store_true",
                    help="Skip LLM parliament, only do Stockfish eval + turning point detection")
parser.add_argument("--stats",         action="store_true")
parser.add_argument("--dry-run",       action="store_true")
parser.add_argument("--verbose",       action="store_true")
parser.add_argument("--claude-review", action="store_true",
                    help="Call Claude after consensus to adjudicate reasoning quality")
parser.add_argument("--think-budget",  type=float, default=45.0,
                    help="Max seconds per model deliberation before timeout (default 45)")
args = parser.parse_args()

OLLAMA_URL    = "http://localhost:11434/api/generate"
MODELS        = [m.strip() for m in args.models.split(",") if m.strip()]
SUPERMODEL_DB = str(Path.home() / "supermodel.db")

# ── CMS position memory ───────────────────────────────────────────────────────
# Parliament consensus is stored as 'position' anchors in the CMS.
# FEN → anchor. Maturity grows only on re-encounter with conf ≥ 0.80.
# Maturity ≥ 2.0 (seen 10+ times) = RECALL; skip full deliberation.
# No unconditional increments — guarded against motif inflation.

POSITION_RECALL_THRESHOLD = 2.0   # maturity required to skip deliberation
POSITION_MATURITY_STEP    = 0.1   # per confirmed re-encounter (conf ≥ 0.80)
POSITION_MIN_CONF         = 0.80  # minimum consensus confidence to write

def _fen_canonical(fen: str) -> str:
    """Short stable key for a FEN (strip move clocks, hash)."""
    parts = fen.split()
    core = " ".join(parts[:4])  # piece placement, active color, castling, en passant
    return "pos." + hashlib.md5(core.encode()).hexdigest()[:12]

def cms_position_recall(conn: sqlite3.Connection, fen: str) -> dict | None:
    """Query CMS for a known position anchor. Returns consensus dict or None."""
    canon = _fen_canonical(fen)
    row = conn.execute(
        "SELECT display_name, sources, maturity FROM anchors WHERE canonical=?",
        (canon,)
    ).fetchone()
    if row and row["maturity"] >= POSITION_RECALL_THRESHOLD:
        try:
            data = json.loads(row["sources"] or "{}")
            return {"conclusion": row["display_name"],
                    "confidence": data.get("confidence", 0.80),
                    "seen": int(row["maturity"] / POSITION_MATURITY_STEP),
                    "maturity": row["maturity"]}
        except Exception:
            return None
    return None

def cms_position_write(conn: sqlite3.Connection, fen: str,
                       conclusion: str, confidence: float, key_insight: str = ""):
    """Write or update a position anchor in the CMS. Only for conf ≥ 0.80."""
    if confidence < POSITION_MIN_CONF:
        return
    canon = _fen_canonical(fen)
    existing = conn.execute(
        "SELECT maturity FROM anchors WHERE canonical=?", (canon,)
    ).fetchone()
    meta = json.dumps({"confidence": confidence, "fen": fen, "insight": key_insight[:200]})
    if existing:
        # Re-encounter: small maturity increment, update consensus if better
        conn.execute("""
            UPDATE anchors SET maturity = maturity + ?,
                display_name = CASE WHEN ? > maturity THEN ? ELSE display_name END,
                sources = ?
            WHERE canonical = ?
        """, (POSITION_MATURITY_STEP, confidence, conclusion[:300], meta, canon))
    else:
        anchor_id = "anc." + hashlib.md5(canon.encode()).hexdigest()[:10]
        conn.execute("""
            INSERT OR IGNORE INTO anchors
                (id, canonical, display_name, anchor_type, maturity,
                 sources, domain_tags, visible, state)
            VALUES (?,?,?,?,?,?,?,1,'active')
        """, (anchor_id, canon, conclusion[:300], "position",
              POSITION_MATURITY_STEP, meta, "chess,position"))
    conn.commit()

def _load_position_cache(conn: sqlite3.Connection, *_):
    """Count known position anchors for display — CMS is the real store."""
    count = conn.execute(
        "SELECT COUNT(*) FROM anchors WHERE anchor_type='position' "
        "AND domain_tags LIKE '%chess%'"
    ).fetchone()[0]
    return count

# ── ANSI ──────────────────────────────────────────────────────────────────────
R    = "\033[0m";  BOLD = "\033[1m";  DIM = "\033[2m"
OK   = "\033[32m"; WARN = "\033[33m"; ERR = "\033[31m"
CMS  = "\033[35m"; LLM  = "\033[36m"; SEL = "\033[38;5;141m"
SF   = "\033[38;5;214m"  # Stockfish orange

MODEL_COLORS = {
    "llama3.1:8b":  "\033[36m",
    "llama3:8b":    "\033[36m",
    "gemma3:4b":    "\033[38;5;141m",
    "phi4-mini":    "\033[33m",
    "qwen3:4b":     "\033[38;5;214m",
    "qwen2.5:14b":  "\033[38;5;208m",
}
MODEL_ROLES = {
    "qwen2.5:14b":  "lead analyst — give the definitive assessment. Name the best move, explain WHY in concrete chess terms (material, king safety, pawn structure, piece activity). Be authoritative and specific.",
    "llama3.1:8b":  "synthesis arbitrator — compare the engine line vs historical move, name EXACTLY which you prefer and WHY in concrete chess terms. No vague language.",
    "llama3:8b":    "synthesis arbitrator — compare the engine line vs historical move, name EXACTLY which you prefer and WHY in concrete chess terms. No vague language.",
    "gemma3:4b":    "symbolic resonance — attend to meaning, metaphor, conceptual depth",
    "phi4-mini":    "analytical discipline — precise reasoning, structured logic",
    "qwen3:4b":   "broad knowledge — history, theory, comparative evidence",
}
def load_psychometric_weights() -> dict:
    """Load per-model engine_agreement rates from supermodel.db for consensus weighting."""
    weights = {m: 1.0 for m in MODELS}
    if not Path(SUPERMODEL_DB).exists():
        return weights
    try:
        conn = sqlite3.connect(SUPERMODEL_DB)
        rows = conn.execute("""
            SELECT model, total_positions, engine_agreements
            FROM model_psychometrics WHERE domain='chess'
        """).fetchall()
        conn.close()
        for model, total, eng in rows:
            if total and total > 10:
                weights[model] = (eng or 0) / total
    except Exception:
        pass
    return weights


def write_contradiction(conn: sqlite3.Connection, sess_id: str, ply: int,
                        model_a: str, data_a: dict,
                        model_b: str, data_b: dict):
    """Log engine-agreement contradiction between two models to supermodel.db."""
    if not Path(SUPERMODEL_DB).exists():
        return
    try:
        sm = sqlite3.connect(SUPERMODEL_DB)
        topic = "engine agreement"
        if data_a.get("proposed_move") and data_b.get("proposed_move"):
            topic = f"move choice: {data_a['proposed_move']} vs {data_b['proposed_move']}"
        sm.execute("""
            INSERT INTO contradiction_ledger
                (session_id, ply, domain, model_a, claim_a, conf_a,
                 model_b, claim_b, conf_b, topic, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (sess_id, ply, "chess",
              model_a, str(data_a.get("conclusion", ""))[:300],
              float(data_a.get("confidence", 0.5)),
              model_b, str(data_b.get("conclusion", ""))[:300],
              float(data_b.get("confidence", 0.5)),
              topic, time.time()))
        sm.commit()
        sm.close()
    except Exception:
        pass


VALID_PREDICATES = {
    "leads_to", "enables", "causes", "weakens", "strengthens", "requires",
    "produces", "results_in", "restricts", "exposes", "threatens", "inhibits",
    "activates", "transforms", "depends_on", "emerges_from", "regulates",
}

_CLAUDECODE_DB = str(Path.home() / "claudecode.db")


# ── Stockfish helpers ─────────────────────────────────────────────────────────

def find_stockfish() -> str:
    p = shutil.which("stockfish")
    if p:
        return p
    for c in ["/usr/games/stockfish", "/usr/local/bin/stockfish"]:
        if Path(c).exists():
            return c
    return None


def stockfish_report(engine: chess.engine.SimpleEngine,
                     board: chess.Board,
                     depth: int = 15,
                     think_time: float = 0.3) -> dict:
    """Get structured tactical report from Stockfish for the current position."""
    info = engine.analyse(board, chess.engine.Limit(depth=depth, time=think_time),
                          multipv=3)

    if not info:
        return {"eval": 0.0, "pv": [], "eval_str": "0.00", "report": "No analysis"}

    best = info[0]
    score = best["score"].white()

    if score.is_mate():
        eval_f = 10.0 if score.mate() > 0 else -10.0
        eval_str = f"M{score.mate()}"
    else:
        eval_f = score.cp / 100.0
        eval_str = f"{eval_f:+.2f}"

    # Build PV string (first 6 moves)
    pv_moves = []
    pv_board = board.copy()
    for mv in best.get("pv", [])[:6]:
        try:
            pv_moves.append(pv_board.san(mv))
            pv_board.push(mv)
        except Exception:
            break

    # Multi-PV: candidate moves with evals
    candidates = []
    for line in info[:3]:
        sc = line["score"].white()
        if sc.is_mate():
            ceval = f"M{sc.mate()}"
        else:
            ceval = f"{sc.cp/100:+.2f}"
        cv = line.get("pv", [])
        if cv:
            try:
                cmove = board.san(cv[0])
                candidates.append(f"{cmove} ({ceval})")
            except Exception:
                pass

    color_name = "White" if board.turn == chess.WHITE else "Black"
    adv = "advantage" if eval_f > 0 else "disadvantage"
    if abs(eval_f) < 0.3:
        adv = "roughly equal"

    report = (
        f"STOCKFISH TACTICAL ANALYSIS (depth={depth}):\n"
        f"  Evaluation: {eval_str} ({color_name} {adv})\n"
        f"  Best continuation: {' '.join(pv_moves)}\n"
        f"  Top candidates: {', '.join(candidates)}\n"
        f"  FEN: {board.fen()}"
    )

    return {
        "eval":      eval_f,
        "eval_str":  eval_str,
        "pv":        pv_moves,
        "candidates": candidates,
        "report":    report,
    }


# ── CMS context ───────────────────────────────────────────────────────────────

def build_cms_context(conn: sqlite3.Connection, white: str, black: str,
                      opening: str) -> str:
    parts = []

    rows = conn.execute("""
        SELECT a1.canonical, r.predicate, a2.canonical
        FROM relations_aggregated r
        JOIN anchors a1 ON r.subject_id = a1.id
        JOIN anchors a2 ON r.object_id  = a2.id
        WHERE (a1.domain_tags LIKE '%chess%' OR a2.domain_tags LIKE '%chess%')
          AND r.confidence > 0.72 AND r.seen_count > 2
        ORDER BY r.seen_count DESC, r.confidence DESC LIMIT 10
    """).fetchall()
    if rows:
        parts.append("Chess CMS relations: " +
                     "; ".join(f"{s} {p} {o}" for s, p, o in rows))

    refl = conn.execute("""
        SELECT strategic_identity, lesson FROM chess_reflections
        ORDER BY reflected_at DESC LIMIT 3
    """).fetchall()
    if refl:
        parts.append("Recent lessons: " +
                     " | ".join(f"{r[0]}: {r[1]}" for r in refl if r[1]))

    if opening:
        parts.append(f"Opening: {opening}")

    return "\n".join(parts) if parts else "Chess CMS context sparse."


# ── Parliament LLM call ───────────────────────────────────────────────────────

def _llm(model: str, prompt: str, temperature: float = 0.5) -> str:
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": model, "prompt": prompt,
            "stream": False, "options": {"temperature": temperature},
        }, timeout=args.think_budget + 5)
        return r.json().get("response", "").strip()
    except Exception as e:
        return f'{{"error": "{e}"}}'


def _parse_json(raw: str, keys: list) -> dict | None:
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


def _extract_relations(raw: str, model: str, sess_id: str) -> list[dict]:
    pattern = re.compile(
        r'\{\s*"subject"\s*:\s*"([^"]+)"\s*,\s*"predicate"\s*:\s*"([^"]+)"\s*,'
        r'\s*"object"\s*:\s*"([^"]+)"\s*,\s*"confidence"\s*:\s*([\d.]+)',
        re.IGNORECASE,
    )
    out = []
    for m in pattern.finditer(raw):
        pred = m.group(2).strip().lower()
        if pred in VALID_PREDICATES:
            out.append({
                "subject":    m.group(1).strip().lower()[:80],
                "predicate":  pred,
                "object":     m.group(3).strip().lower()[:80],
                "confidence": max(0.5, min(0.95, float(m.group(4)))),
                "proposed_by": model,
                "session_id": sess_id,
            })
    return out


# ── synth.db helpers ──────────────────────────────────────────────────────────

def ensure_synth_schema(sconn: sqlite3.Connection):
    sconn.executescript("""
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
        CREATE TABLE IF NOT EXISTS replay_sessions (
            id          TEXT PRIMARY KEY,
            game_id     TEXT NOT NULL,
            white       TEXT,
            black       TEXT,
            opening     TEXT,
            ply_count   INTEGER,
            positions_deliberated INTEGER DEFAULT 0,
            parliament_agreements INTEGER DEFAULT 0,
            parliament_disagreements INTEGER DEFAULT 0,
            engine_alignment REAL,
            created_at  REAL
        );
    """)
    sconn.commit()


def write_synth_relation(sconn: sqlite3.Connection, rel: dict):
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
          "chess",
          time.time()))


# ── Parliament deliberation ───────────────────────────────────────────────────

def claude_adjudicate(sess_id: str, ply: int, fen: str,
                      sf_report: dict, positions: dict,
                      consensus: dict, conn: sqlite3.Connection) -> dict | None:
    """
    Call Claude CLI to adjudicate reasoning quality after parliament consensus.
    Returns structured quality assessment and stores in parliament_claude_reviews.
    Auto-triggered on curriculum_flag / weak_consensus positions.
    """
    sf_eval = sf_report.get("eval_str", "?")
    sf_pv   = " ".join(sf_report.get("pv", [])[:4])

    model_summaries = []
    for m, p in positions.items():
        model_summaries.append(
            f"  [{m}] move={p.get('proposed_move','?')} conf={p.get('confidence',0):.2f} "
            f"engine={'agree' if p.get('agrees_with_engine') else 'disagree'}\n"
            f"    reasoning: {str(p.get('reasoning',''))[:200]}"
        )

    prompt = f"""You are a senior chess analyst reviewing a parliament of LLMs that analyzed a chess position.

Position (FEN): {fen}
Stockfish evaluation: {sf_eval}  Best line: {sf_pv}
Parliament consensus move: {consensus.get('conclusion','?')[:100]}
Consensus confidence: {consensus.get('confidence',0):.2f}
Engine agreement: {consensus.get('engine_agreements',0)}/{consensus.get('total',0)} models

Individual model assessments:
{chr(10).join(model_summaries)}

Your task: Adjudicate the QUALITY OF REASONING, not just correctness.
Evaluate each model's argument on chess merit: specificity, tactical accuracy, strategic insight.
Flag hallucinations (confident wrong claims), vague generalities, or genuinely insightful observations.

Respond in JSON only:
{{
  "verdict": "good|poor|mixed",
  "quality_score": 0.0-1.0,
  "model_scores": {{{", ".join(f'"{m}": 0.0' for m in positions)}}},
  "good_reasoning": "what was best about the parliament's reasoning (1-2 sentences)",
  "bad_reasoning": "what was flawed or vague (1-2 sentences)",
  "key_insight": "the single most valuable chess observation made, or null if none",
  "training_value": "high|medium|low"
}}"""

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=60
        )
        raw = result.stdout.strip()
        # Extract JSON from response
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group())
    except Exception:
        return None

    review_id = f"{sess_id}:{ply}:claude"
    conn.execute("""
        INSERT OR REPLACE INTO parliament_claude_reviews
            (id, session_id, ply, fen, consensus_move, stockfish_eval,
             verdict, quality_score, model_scores, good_reasoning,
             bad_reasoning, key_insight, training_value, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (review_id, sess_id, ply, fen,
          consensus.get("conclusion","")[:300],
          sf_report.get("eval", 0.0),
          data.get("verdict","mixed"),
          float(data.get("quality_score", 0.5)),
          json.dumps(data.get("model_scores", {})),
          str(data.get("good_reasoning",""))[:500],
          str(data.get("bad_reasoning",""))[:500],
          str(data.get("key_insight",""))[:300],
          data.get("training_value","medium"),
          time.time()))
    conn.commit()

    vc = OK if data.get("verdict") == "good" else (ERR if data.get("verdict") == "poor" else WARN)
    print(f"\n  {BOLD}Claude review:{R} {vc}{data.get('verdict','?')}{R} "
          f"quality={data.get('quality_score',0):.2f} "
          f"training={data.get('training_value','?')}")
    if data.get("key_insight"):
        print(f"  {DIM}Insight: {data['key_insight'][:100]}{R}")

    return data


def parliament_position(board: chess.Board, sf_report: dict,
                        cms_ctx: str, historical_move: str,
                        sess_id: str, ply: int,
                        conn: sqlite3.Connection,
                        sconn: sqlite3.Connection,
                        policy: DebatePolicy = None) -> dict:
    """Run parliament deliberation on a position with Stockfish as anchor."""
    if policy is None:
        policy = DebatePolicy()
    fen = board.fen()

    # Position recall — CMS anchor lookup; skip deliberation for mature positions
    cached = cms_position_recall(conn, fen)
    if cached and not policy.curriculum_flag and not args.claude_review:
        print(f"\n  {SF}{BOLD}Stockfish:{R} {SF}{sf_report['eval_str']}{R}  "
              f"PV: {' '.join(sf_report['pv'][:4])}")
        print(f"  {DIM}Historical: {historical_move or '(game end)'}{R}")
        print(f"  {OK}[RECALL]{R} seen={cached['seen']}x  conf={cached['confidence']:.2f}  "
              f"\"{cached['conclusion'][:70]}\"")
        return {"conclusion": cached["conclusion"], "confidence": cached["confidence"],
                "engine_agreements": 0, "hist_agreements": 0,
                "total": len(MODELS), "lead": "recall", "positions": {}, "recalled": True}
    color_name = "white" if board.turn == chess.WHITE else "black"
    sf_text = sf_report.get("report", "")

    zone_tag = f"  {WARN}[{policy.zone_label}]{R}" if policy.zone_label != "normal" else ""
    print(f"\n  {SF}{BOLD}Stockfish:{R} {SF}{sf_report['eval_str']}{R}  "
          f"PV: {' '.join(sf_report['pv'][:4])}{zone_tag}")
    if policy.zone_label != "normal":
        print(f"  {DIM}Policy: depth={policy.stockfish_depth} "
              f"threshold={policy.consensus_threshold:.0%} "
              f"reason: {policy.reason}{R}")
    print(f"  {DIM}Historical: {historical_move or '(game end)'}{R}")
    print(f"  {'─'*56}")

    positions = {}
    for model in MODELS:
        col = MODEL_COLORS.get(model, LLM)
        print(f"  {col}{model:20}{R}", end=" ", flush=True)
        role = MODEL_ROLES.get(model, "independent reasoner")

        llama_instruction = ""
        if model in ("llama3:8b", "llama3.1:8b"):
            llama_instruction = f"""
REQUIRED: Name a specific move in proposed_move. State exactly ONE concrete reason
(e.g. "opens the f-file for the rook", "fixes the backward pawn on d6", "forces the king
to a worse square"). Generic phrases like "complex strategic landscape" are forbidden.
Engine says: {" ".join(sf_report.get("pv", [])[:2])}. Historical was: {historical_move}.
Pick one. Justify it in chess terms."""

        prompt = f"""You are a chess parliament member analyzing a historical game position.
Your role: {role}
{llama_instruction}
Position (FEN): {fen}
It is {color_name}'s turn.

{sf_text}

Chess knowledge substrate (CMS):
{cms_ctx}

Historical game continuation: {historical_move or '(end of game)'}

Deliberate on this position. Reason independently.
Do you agree with Stockfish's preferred line? Why or why not?
What strategic themes does this position express?
Extract any causal insights as proposed relations.

Respond in JSON only:
{{
  "conclusion": "NAME your recommended move first, then give ONE concrete chess reason (e.g. 'I recommend Kf5 because it centralises the king and controls the e4 square'). No generic phrases.",
  "proposed_move": "the move you'd recommend in this position (SAN notation)",
  "agrees_with_engine": true or false,
  "agrees_with_historical": true or false,
  "reasoning": "your reasoning (2-3 sentences)",
  "confidence": 0.0-1.0,
  "key_insight": "one sentence — most important thing about this position",
  "proposed_relations": [
    {{"subject": "concept", "predicate": "leads_to", "object": "concept", "confidence": 0.75}}
  ]
}}"""

        raw = _llm(model, prompt, temperature=0.5)
        data = _parse_json(raw, ["conclusion", "confidence"])
        if not data:
            data = {"conclusion": raw[:150] or "(no response)",
                    "proposed_move": "", "agrees_with_engine": None,
                    "agrees_with_historical": None, "reasoning": "",
                    "confidence": 0.50, "key_insight": ""}
            print(f"{WARN}partial{R}", end="")
        else:
            conf = float(data.get("confidence", 0.5))
            eng_agree = data.get("agrees_with_engine")
            hist_agree = data.get("agrees_with_historical")
            tag = f"{OK}✓eng{R}" if eng_agree else f"{WARN}✗eng{R}"
            tag2 = f"{OK}✓hist{R}" if hist_agree else f"{WARN}✗hist{R}"
            print(f"{OK}ok{R}  conf={conf:.2f}  {tag} {tag2}", end="")

        print(f"  \"{str(data.get('conclusion',''))[:50]}\"")
        positions[model] = data

        # Write per-model deliberation to chess DB
        row_id = f"{sess_id}:{ply}:{model}"
        conn.execute("""
            INSERT OR IGNORE INTO parliament_move_deliberations
                (id, session_id, ply, model, fen, conclusion, reasoning,
                 confidence, key_factors, key_insight, is_consensus,
                 stockfish_eval, agrees_with_engine, agrees_with_historical,
                 created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)
        """, (row_id, sess_id, ply, model, fen,
              str(data.get("conclusion", ""))[:500],
              str(data.get("reasoning", ""))[:1000],
              float(data.get("confidence", 0.5)),
              str(data.get("proposed_move", ""))[:50],
              str(data.get("key_insight", ""))[:300],
              sf_report.get("eval", 0.0),
              1 if data.get("agrees_with_engine") else 0,
              1 if data.get("agrees_with_historical") else 0,
              time.time()))

        for rel in _extract_relations(raw, model, sess_id):
            write_synth_relation(sconn, rel)

    sconn.commit()
    conn.commit()

    # Weighted consensus: policy weights (terrain-derived) × raw confidence
    # Fall back to psychometric weights if no terrain rules loaded yet
    terrain_weights = policy.model_weights
    if not terrain_weights:
        terrain_weights = load_psychometric_weights()

    all_confs = [float(p.get("confidence", 0.5)) for p in positions.values()]
    avg_conf  = sum(all_confs) / len(all_confs) if all_confs else 0.5

    def weighted_score(m):
        raw = float(positions[m].get("confidence", 0.5))
        return raw * terrain_weights.get(m, 1.0)

    # Apply consensus threshold — require minimum agreement fraction
    models_agreeing_with_lead = 0
    lead = max(positions, key=weighted_score)
    lead_eng = positions[lead].get("agrees_with_engine")
    for m, p in positions.items():
        if m != lead and p.get("agrees_with_engine") == lead_eng:
            models_agreeing_with_lead += 1
    agreement_frac = (models_agreeing_with_lead + 1) / len(positions) if positions else 0

    # If we're in a strict zone and consensus is weak, flag it
    consensus_weak = agreement_frac < policy.consensus_threshold
    consensus_text = positions[lead].get("conclusion", "")
    engine_agreements = sum(1 for p in positions.values()
                            if p.get("agrees_with_engine") is True)
    hist_agreements = sum(1 for p in positions.values()
                          if p.get("agrees_with_historical") is True)

    # Contradiction detection — log inter-model engine disagreements
    model_list = list(positions.keys())
    for i, m_a in enumerate(model_list):
        for m_b in model_list[i+1:]:
            agree_a = positions[m_a].get("agrees_with_engine")
            agree_b = positions[m_b].get("agrees_with_engine")
            if agree_a is not None and agree_b is not None and agree_a != agree_b:
                write_contradiction(conn, sess_id, ply,
                                    m_a, positions[m_a],
                                    m_b, positions[m_b])

    # Write Stockfish as a special parliament entry
    sf_row_id = f"{sess_id}:{ply}:stockfish"
    conn.execute("""
        INSERT OR IGNORE INTO parliament_move_deliberations
            (id, session_id, ply, model, fen, conclusion, reasoning,
             confidence, key_factors, key_insight, is_consensus,
             stockfish_eval, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?)
    """, (sf_row_id, sess_id, ply, "stockfish", fen,
          " ".join(sf_report.get("pv", [])[:4]),
          sf_report.get("report", "")[:1000],
          0.99,
          json.dumps(sf_report.get("candidates", [])),
          f"Engine eval: {sf_report['eval_str']}",
          sf_report.get("eval", 0.0),
          time.time()))

    # Write consensus row
    cons_id = f"{sess_id}:{ply}:consensus"
    weak_flag = " [WEAK_CONSENSUS]" if consensus_weak else ""
    curr_flag = " [CURRICULUM]" if policy.curriculum_flag else ""
    conn.execute("""
        INSERT OR IGNORE INTO parliament_move_deliberations
            (id, session_id, ply, model, fen, conclusion, reasoning,
             confidence, key_factors, key_insight, is_consensus,
             stockfish_eval, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)
    """, (cons_id, sess_id, ply, lead, fen,
          consensus_text[:500],
          f"engine_agree={engine_agreements}/{len(MODELS)} "
          f"hist_agree={hist_agreements}/{len(MODELS)}"
          f" zone={policy.zone_label}{weak_flag}{curr_flag}",
          avg_conf,
          json.dumps(MODELS),
          f"lead={lead} threshold={policy.consensus_threshold:.0%}",
          sf_report.get("eval", 0.0),
          time.time()))
    conn.commit()

    print(f"\n  {BOLD}Consensus:{R} conf={avg_conf:.2f}  "
          f"engine={engine_agreements}/{len(MODELS)}  "
          f"historical={hist_agreements}/{len(MODELS)}")
    print(f"  \"{consensus_text[:80]}\"")

    result = {
        "conclusion":          consensus_text,
        "confidence":          avg_conf,
        "engine_agreements":   engine_agreements,
        "hist_agreements":     hist_agreements,
        "total":               len(MODELS),
        "lead":                lead,
        "positions":           positions,
    }

    # Write position memory to CMS (conf-gated, maturity-bounded — no inflation)
    best_insight = max(positions.values(),
                       key=lambda p: float(p.get("confidence", 0)),
                       default={}).get("key_insight", "")
    cms_position_write(conn, fen, consensus_text, avg_conf, best_insight)

    # Claude post-consensus review — runs on curriculum/weak_consensus or --claude-review flag
    if args.claude_review or policy.curriculum_flag or consensus_weak:
        claude_adjudicate(sess_id, ply, fen, sf_report, positions, result, conn)

    return result


# ── Game replay ───────────────────────────────────────────────────────────────

def replay_game(game_row: dict, engine: chess.engine.SimpleEngine,
                conn: sqlite3.Connection, sconn: sqlite3.Connection) -> dict:
    """Replay a single game with parliament deliberation at key positions."""

    gid   = game_row["id"]
    white = game_row.get("white", "?")
    black = game_row.get("black", "?")
    opening = game_row.get("opening", "")
    pgn_text = game_row.get("pgn_text", "")

    # Reconstruct move list from chess_moves table or embedded PGN
    moves_rows = conn.execute("""
        SELECT ply, san FROM chess_moves WHERE game_id=? ORDER BY ply
    """, (gid,)).fetchall()

    if not moves_rows:
        return {"skipped": True, "reason": "no moves stored"}

    sess_id = "replay." + hashlib.md5(f"{gid}{time.time()}".encode()).hexdigest()[:10]
    cms_ctx = build_cms_context(conn, white, black, opening)
    policy_engine = AdaptivePolicy(SUPERMODEL_DB)
    cache_size = _load_position_cache(conn)
    if cache_size:
        print(f"  {DIM}Position recall: {cache_size} cached positions{R}")

    print(f"\n  {BOLD}{'═'*58}{R}")
    print(f"  Replaying: {SEL}{white}{R} vs {LLM}{black}{R}  {DIM}{opening}{R}")
    print(f"  {DIM}{gid}{R}")

    board = chess.Board()
    prev_eval = 0.0
    positions_deliberated = 0
    parliament_agreements = 0
    parliament_disagreements = 0
    engine_evals = []

    for ply, (_, san) in enumerate(moves_rows):
        try:
            move = board.parse_san(san)
        except Exception:
            break

        historical_san = san
        next_san = moves_rows[ply + 1][1] if ply + 1 < len(moves_rows) else None

        should_deliberate = (
            not args.no_parliament and
            (ply % args.deliberate_every == 0 or
             (len(engine_evals) >= 1 and
              abs(engine_evals[-1] - prev_eval) >= args.eval_threshold))
        )

        if should_deliberate:
            # Get adaptive policy for this position (uses last known eval)
            last_eval = engine_evals[-1] if engine_evals else None
            dp = policy_engine.for_position(ply, last_eval, opening)

            sf = stockfish_report(engine, board, dp.stockfish_depth, args.think_time)
            engine_evals.append(sf["eval"])
            prev_eval = sf["eval"]

            # Re-evaluate policy now that we have the actual eval
            dp = policy_engine.for_position(ply, sf["eval"], opening)

            print(f"\n  {DIM}Ply {ply} | {historical_san}{R}")
            result = parliament_position(
                board, sf, cms_ctx, historical_san,
                sess_id, ply, conn, sconn, dp,
            )
            positions_deliberated += 1

            if result["engine_agreements"] >= len(MODELS) // 2:
                parliament_agreements += 1
            else:
                parliament_disagreements += 1

            # Write discovery to claudecode.db
            body = (f"Replay [{white} vs {black}] ply={ply} "
                    f"eval={sf['eval_str']} parliament: {result['conclusion'][:120]}")
            disc_id = hashlib.md5(f"{sess_id}{ply}".encode()).hexdigest()[:12]
            try:
                cc = sqlite3.connect(_CLAUDECODE_DB)
                cc.execute("""
                    INSERT OR IGNORE INTO discoveries
                        (id, session_id, body, tags, importance, created_at)
                    VALUES (?,?,?,?,?,?)
                """, (disc_id, sess_id, body[:1000], "chess,replay,parliament",
                      2, time.time()))
                cc.commit()
                cc.close()
            except Exception:
                pass

        else:
            # Still track engine eval at every 3rd ply even without deliberation
            if ply % 3 == 0:
                info = engine.analyse(board, chess.engine.Limit(depth=10))
                if info:
                    sc = info["score"].white()
                    ev = sc.cp / 100.0 if not sc.is_mate() else (10.0 if sc.mate() > 0 else -10.0)
                    engine_evals.append(ev)
                    prev_eval = ev

        if not args.dry_run:
            board.push(move)

    # Write replay session record
    avg_align = (parliament_agreements / positions_deliberated
                 if positions_deliberated else 0.0)
    sconn.execute("""
        INSERT OR IGNORE INTO replay_sessions
            (id, game_id, white, black, opening, ply_count,
             positions_deliberated, parliament_agreements,
             parliament_disagreements, engine_alignment, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (sess_id, gid, white, black, opening, len(moves_rows),
          positions_deliberated, parliament_agreements,
          parliament_disagreements, avg_align, time.time()))
    sconn.commit()

    print(f"\n  {OK}Done{R}: {positions_deliberated} positions deliberated  "
          f"engine align={avg_align:.0%}")

    return {
        "id": sess_id, "game_id": gid,
        "positions_deliberated": positions_deliberated,
        "parliament_agreements": parliament_agreements,
        "parliament_disagreements": parliament_disagreements,
        "engine_alignment": avg_align,
    }


# ── Load games ────────────────────────────────────────────────────────────────

def load_games_from_db(conn: sqlite3.Connection, limit: int,
                       game_id: str = None,
                       resume: bool = False) -> list[dict]:
    if game_id:
        row = conn.execute(
            "SELECT id, white, black, opening, ply_count FROM chess_games WHERE id=?",
            (game_id,)
        ).fetchone()
        return [dict(zip(["id","white","black","opening","ply_count"], row))] if row else []

    already_replayed = set()
    if resume:
        rows = conn.execute("""
            SELECT DISTINCT session_id FROM parliament_move_deliberations
            WHERE is_consensus=1
        """).fetchall()
        # session_id is "replay.xxx" — need to map back to game_id via replay_sessions in synth
        # Simpler: check games that have deliberations stored
        gids = conn.execute("""
            SELECT DISTINCT game_id FROM parliament_move_deliberations
            WHERE model='consensus'
        """).fetchall()
        already_replayed = {r[0] for r in gids}

    rows = conn.execute("""
        SELECT id, white, black, opening, ply_count
        FROM chess_games
        WHERE ply_count > 20
        ORDER BY RANDOM()
        LIMIT ?
    """, (limit + len(already_replayed),)).fetchall()

    games = []
    for row in rows:
        d = dict(zip(["id","white","black","opening","ply_count"], row))
        if d["id"] not in already_replayed:
            games.append(d)
        if len(games) >= limit:
            break
    return games


def load_games_from_pgn(pgn_path: str) -> list[dict]:
    import io
    import chess.pgn as cpgn
    games = []
    text = Path(pgn_path).read_text(errors="replace")
    with io.StringIO(text) as f:
        while True:
            g = cpgn.read_game(f)
            if g is None:
                break
            tags = dict(g.headers)
            # Collect moves
            moves = []
            node = g
            ply = 0
            while node.variations:
                node = node.variations[0]
                try:
                    san = node.parent.board().san(node.move)
                    moves.append((ply, san))
                    ply += 1
                except Exception:
                    break

            games.append({
                "id":       "pgn." + hashlib.md5(
                    f"{tags.get('White')}{tags.get('Black')}{tags.get('Date')}".encode()
                ).hexdigest()[:12],
                "white":   tags.get("White", "?"),
                "black":   tags.get("Black", "?"),
                "opening": tags.get("Opening", ""),
                "ply_count": len(moves),
                "_moves":  moves,
            })
    return games


# ── Stats ─────────────────────────────────────────────────────────────────────

def cmd_stats(conn: sqlite3.Connection, sconn: sqlite3.Connection):
    total = conn.execute("SELECT COUNT(*) FROM parliament_move_deliberations").fetchone()[0]
    consensus = conn.execute(
        "SELECT COUNT(*) FROM parliament_move_deliberations WHERE is_consensus=1"
    ).fetchone()[0]
    eng_agree = conn.execute("""
        SELECT AVG(CAST(json_extract(key_factors,'$[1]') AS REAL))
        FROM parliament_move_deliberations WHERE model != 'consensus' AND model != 'stockfish'
    """).fetchone()[0]

    synth = sconn.execute("SELECT COUNT(*) FROM synth_relations WHERE domain='chess'").fetchone()[0]
    replayed = sconn.execute("SELECT COUNT(*) FROM replay_sessions").fetchone()[0]

    print(f"\n  Replay Stats")
    print(f"  {'─'*40}")
    print(f"  Games replayed:          {replayed:,}")
    print(f"  Parliament positions:     {total:,}")
    print(f"  Consensus records:        {consensus:,}")
    print(f"  Chess synth relations:    {synth:,}")

    top_models = conn.execute("""
        SELECT model, COUNT(*) c FROM parliament_move_deliberations
        WHERE model NOT IN ('consensus','stockfish')
        GROUP BY model ORDER BY c DESC
    """).fetchall()
    if top_models:
        print(f"\n  Model participation:")
        for m, c in top_models:
            print(f"    {m:20} {c:,} deliberations")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    sf_path = find_stockfish()
    if not sf_path:
        print(f"{ERR}Stockfish not found. Install: sudo apt install stockfish{R}")
        sys.exit(1)

    conn  = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS parliament_move_deliberations (
            id          TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            game_id     TEXT,
            ply         INTEGER,
            model       TEXT NOT NULL,
            fen         TEXT,
            conclusion  TEXT,
            reasoning   TEXT,
            confidence  REAL DEFAULT 0.70,
            key_factors TEXT,
            key_insight TEXT,
            is_consensus INTEGER DEFAULT 0,
            outcome     TEXT DEFAULT 'pending',
            stockfish_eval REAL,
            agrees_with_engine INTEGER DEFAULT 0,
            agrees_with_historical INTEGER DEFAULT 0,
            created_at  REAL
        );
        CREATE INDEX IF NOT EXISTS idx_pmd_session ON parliament_move_deliberations(session_id);
        CREATE INDEX IF NOT EXISTS idx_pmd_game    ON parliament_move_deliberations(game_id);

        CREATE TABLE IF NOT EXISTS parliament_claude_reviews (
            id             TEXT PRIMARY KEY,
            session_id     TEXT NOT NULL,
            ply            INTEGER,
            fen            TEXT,
            consensus_move TEXT,
            stockfish_eval REAL,
            verdict        TEXT,
            quality_score  REAL,
            model_scores   TEXT,
            good_reasoning TEXT,
            bad_reasoning  TEXT,
            key_insight    TEXT,
            training_value TEXT,
            created_at     REAL
        );
        CREATE INDEX IF NOT EXISTS idx_pcr_session ON parliament_claude_reviews(session_id);
    """)
    conn.commit()
    sconn = sqlite3.connect(args.synth_db)
    ensure_synth_schema(sconn)

    if args.stats:
        cmd_stats(conn, sconn)
        conn.close(); sconn.close()
        return

    engine = chess.engine.SimpleEngine.popen_uci(sf_path)

    if args.pgn:
        games = load_games_from_pgn(args.pgn)
        if args.games:
            games = games[:args.games]
        # For PGN games, attach moves directly
        for g in games:
            # Patch conn.execute to use _moves from PGN
            g["_from_pgn"] = True
    elif args.game_id:
        games = load_games_from_db(conn, 1, game_id=args.game_id)
    else:
        games = load_games_from_db(conn, args.games, resume=args.resume)

    if not games:
        print(f"{WARN}No games to replay.{R}")
        engine.quit(); conn.close(); sconn.close()
        return

    print(f"\n  {BOLD}Selyrion Experiential Replay{R}")
    print(f"  {DIM}{len(games)} games  |  models: {', '.join(MODELS)}{R}")
    print(f"  {DIM}parliament every {args.deliberate_every} plies + turning points "
          f"(threshold ±{args.eval_threshold}){R}")
    if args.no_parliament:
        print(f"  {WARN}Parliament disabled — Stockfish eval only{R}")

    processed = errors = 0
    for i, game in enumerate(games):
        print(f"\n  [{i+1}/{len(games)}]", end="")

        # For PGN-sourced games, monkey-patch chess_moves query
        if game.get("_from_pgn"):
            original_execute = conn.execute
            pgn_moves = game.get("_moves", [])
            def _mock_execute(sql, params=()):
                if "chess_moves" in sql and "game_id" in sql:
                    class _FakeResult:
                        def fetchall(self_):
                            return pgn_moves
                    return _FakeResult()
                return original_execute(sql, params)
            conn.execute = _mock_execute

        try:
            result = replay_game(game, engine, conn, sconn)
            if not result.get("skipped"):
                processed += 1
        except KeyboardInterrupt:
            print(f"\n  Interrupted.")
            break
        except Exception as e:
            errors += 1
            if args.verbose:
                import traceback; traceback.print_exc()
            else:
                print(f"  {ERR}Error: {e}{R}")
        finally:
            if game.get("_from_pgn"):
                conn.execute = original_execute

        if not args.batch and i < len(games) - 1:
            cmd = input(f"\n  Continue? Enter/q: ").strip().lower()
            if cmd == "q":
                break

    engine.quit()
    print(f"\n  {BOLD}Replay complete.{R}  Processed: {processed}  Errors: {errors}")
    print(f"  Synth relations pending: ", end="")
    print(sconn.execute(
        "SELECT COUNT(*) FROM synth_relations WHERE review_status='pending'"
    ).fetchone()[0])
    print(f"  Run: python3 selyrion_parliament.py --review")

    conn.close(); sconn.close()


if __name__ == "__main__":
    main()
