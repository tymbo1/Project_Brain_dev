#!/usr/bin/env python3
"""
chess_pgn_ingest.py — Ingest annotated PGN files into the CMS.

Extracts from PGN files:
  - Game metadata (players, result, date, ECO, opening name)
  - Move-level annotations (!, !!, ?, ??, !?, ?! = NAG symbols)
  - Text comment motifs (pin, fork, sacrifice, etc. found in commentary)
  - Opening identification → relation to opening family anchor
  - Player style evidence → reinforces player knowledge anchors
  - Turning-point moves → creates causal chain edges

Writes to:
  - anchors (game concepts, named positions)
  - relations_aggregated (chess-domain edges)
  - chess_games table (game registry with metadata)
  - chess_moves table (annotated moves with motif tags)

Usage:
  python3 chess_pgn_ingest.py --pgn myfile.pgn
  python3 chess_pgn_ingest.py --pgn myfile.pgn --dry-run
  python3 chess_pgn_ingest.py --pgn myfile.pgn --max-games 50
  python3 chess_pgn_ingest.py --stats
"""

import sys, re, sqlite3, hashlib, time, argparse, json
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

DB_PATH = Path.home() / "resonance_v11.db"

parser = argparse.ArgumentParser()
parser.add_argument("--pgn",        type=str, help="PGN file to ingest")
parser.add_argument("--db",         default=str(DB_PATH))
parser.add_argument("--dry-run",    action="store_true")
parser.add_argument("--max-games",  type=int, default=0, help="Limit games processed (0=all)")
parser.add_argument("--stats",      action="store_true", help="Show chess DB stats")
parser.add_argument("--annotated",  action="store_true",
                    help="Deep annotation extraction: per-move comments, causal relations, "
                         "rejected candidates. Uses python-chess parser.")
parser.add_argument("--re-annotate", action="store_true",
                    help="Force annotation extraction on already-ingested games "
                         "(implies --annotated, skips game/move record writes).")
parser.add_argument("--verbose",    action="store_true")
args = parser.parse_args()


# ── Motif detection in commentary ─────────────────────────────────────────────

_MOTIF_PATTERNS: list[tuple[str, str]] = [
    # tactic → anchor canonical
    (r'\bpin(?:ned|ning)?\b',          "pin"),
    (r'\bfork(?:ed|ing)?\b',           "fork"),
    (r'\bskewer(?:ed|ing)?\b',         "skewer"),
    (r'\bsacrifice[sd]?\b',            "sacrifice"),
    (r'\bsacrificing\b',               "sacrifice"),
    (r'\bdeflect(?:ion|ed|ing)?\b',    "deflection"),
    (r'\bdecoy\b',                     "decoy"),
    (r'\boverload(?:ed|ing)?\b',       "overloading"),
    (r'\bdiscovered (?:attack|check)\b',"discovered attack"),
    (r'\bdouble check\b',              "double check"),
    (r'\bback.?rank\b',                "back-rank weakness"),
    (r'\bsmothered mate\b',            "smothered mate"),
    (r'\bzwischenzug\b',               "zwischenzug"),
    (r'\bintermediate move\b',         "zwischenzug"),
    (r'\bkombination\b',               "combination"),
    (r'\bcombination\b',               "combination"),
    # positional
    (r'\bisolated pawn\b',             "isolated pawn"),
    (r'\bpassed pawn\b',               "passed pawn"),
    (r'\boutpost\b',                   "outpost"),
    (r'\bweak square\b',               "weak square"),
    (r'\bopen file\b',                 "open file"),
    (r'\bbishop pair\b',               "bishop pair"),
    (r'\binitiative\b',                "initiative"),
    (r'\btempo\b',                     "tempo"),
    (r'\bzugzwang\b',                  "zugzwang"),
    (r'\bprophyla(?:x|ct)is\b',        "prophylaxis"),
    (r'\bcompensation\b',              "compensation"),
    (r'\bimbalance\b',                 "imbalance"),
    (r'\bspace (?:advantage|control)\b',"space advantage"),
    (r'\bking safety\b',               "king safety"),
    # endgame
    (r'\blucena\b',                    "lucena position"),
    (r'\bphilidor\b',                  "philidor position"),
    (r'\bopposition\b',                "opposition"),
    (r'\btriangulat\w+\b',             "triangulation"),
    (r'\bfortress\b',                  "fortress"),
]
_MOTIF_RE = [(re.compile(p, re.IGNORECASE), m) for p, m in _MOTIF_PATTERNS]


def detect_motifs(text: str) -> list[str]:
    found = []
    seen = set()
    for rx, motif in _MOTIF_RE:
        if rx.search(text) and motif not in seen:
            found.append(motif)
            seen.add(motif)
    return found


# ── Annotation relation extraction ───────────────────────────────────────────

# Ordered by specificity — longer phrases before shorter ones
_PRED_WORDS: list[tuple[str, str, float]] = [
    # (phrase, canonical_predicate, confidence_bonus)
    ("leads to",      "leads_to",    0.05),
    ("results in",    "leads_to",    0.05),
    ("gives rise to", "leads_to",    0.03),
    ("allows",        "enables",     0.00),
    ("enables",       "enables",     0.05),
    ("requires",      "requires",    0.05),
    ("needs",         "requires",    0.00),
    ("depends on",    "depends_on",  0.05),
    ("strengthens",   "strengthens", 0.05),
    ("weakens",       "weakens",     0.05),
    ("prevents",      "restricts",   0.05),
    ("stops",         "restricts",   0.00),
    ("restricts",     "restricts",   0.05),
    ("threatens",     "threatens",   0.05),
    ("because",       "requires",    0.00),  # "X works because Y" → X requires Y
    ("if",            "requires",   -0.05),  # lower confidence for conditionals
]

_REJECTED_PATTERNS = [
    re.compile(r'\b(?:could|might|can)\s+(?:have\s+)?played\b', re.I),
    re.compile(r'\b(?:tempting|natural|obvious)\s+(?:but|however|although)\b', re.I),
    re.compile(r'\b(?:premature|fails?|doesn\'t work)\s+(?:because|due to|since)\b', re.I),
    re.compile(r'\?!|\?\?',),  # dubious/blunder symbols in text
]


def extract_annotation_relations(comment: str, game_id: str,
                                  ply: int, side: str) -> list[dict]:
    """Extract motif-grounded causal relations from a PGN annotation comment."""
    if not comment or len(comment) < 15:
        return []

    comment_lower = comment.lower()
    present = detect_motifs(comment)
    if len(present) < 2:
        return []

    results = []
    for phrase, pred, conf_bonus in _PRED_WORDS:
        idx = comment_lower.find(phrase)
        if idx == -1:
            continue

        before = comment_lower[:idx]
        after  = comment_lower[idx + len(phrase):]

        subj_candidates = [m for m in present if m in before]
        obj_candidates  = [m for m in present if m in after and m not in subj_candidates]

        if not subj_candidates or not obj_candidates:
            continue

        subj = max(subj_candidates, key=lambda m: before.rfind(m))
        obj  = min(obj_candidates,  key=lambda m: after.find(m))

        conf = round(min(0.92, 0.80 + conf_bonus), 2)
        results.append({
            "subject":     subj,
            "predicate":   pred,
            "object":      obj,
            "confidence":  conf,
            "game_id":     game_id,
            "ply":         ply,
            "side":        side,
            "source_text": comment[:250],
            "is_rejected": False,
        })

    return results


def detect_rejected_candidates(comment: str, game_id: str,
                                ply: int, side: str) -> list[dict]:
    """Detect rejected candidate moves — encode their failure conditions."""
    results = []
    for rx in _REJECTED_PATTERNS:
        m = rx.search(comment)
        if not m:
            continue
        # Extract the failure reason (everything after "but/because/due to")
        after_match = comment[m.end():]
        motifs_after = detect_motifs(after_match)
        motifs_before = detect_motifs(comment[:m.start()])
        for rejected in motifs_before[:1]:
            for reason in motifs_after[:2]:
                results.append({
                    "subject":     rejected,
                    "predicate":   "restricts",
                    "object":      reason,
                    "confidence":  0.75,
                    "game_id":     game_id,
                    "ply":         ply,
                    "side":        side,
                    "source_text": comment[:250],
                    "is_rejected": True,
                })
    return results


# ── NAG (Numeric Annotation Glyph) map ───────────────────────────────────────

_NAG_QUALITY = {
    1:  "good_move",          # !
    2:  "mistake",            # ?
    3:  "brilliant_move",     # !!
    4:  "blunder",            # ??
    5:  "interesting_move",   # !?
    6:  "dubious_move",       # ?!
    7:  "forced_move",
    10: "drawish",
    13: "unclear",
    14: "white_slight_advantage",
    15: "black_slight_advantage",
    16: "white_clear_advantage",
    17: "black_clear_advantage",
    18: "white_decisive_advantage",
    19: "black_decisive_advantage",
}


# ── ECO to opening family map ─────────────────────────────────────────────────

_ECO_OPENINGS: dict[str, str] = {
    # A-series (flank/irregular)
    "A00": "kings fianchetto",
    "A10": "english opening",
    "A20": "english opening",
    "A25": "english opening",
    "A30": "english opening",
    "A40": "dutch defense",
    "A50": "english opening",
    "A80": "dutch defense",
    # B-series (semi-open)
    "B00": "alekhines defense",
    "B01": "alekhines defense",
    "B10": "caro-kann defense",
    "B12": "caro-kann defense",
    "B13": "caro-kann defense",
    "B15": "caro-kann defense",
    "B17": "caro-kann defense",
    "B20": "sicilian defense",
    "B21": "sicilian defense",
    "B22": "sicilian defense",
    "B23": "sicilian defense",
    "B40": "sicilian defense",
    "B50": "sicilian defense",
    "B52": "sicilian defense",
    "B56": "sicilian defense",
    "B57": "sicilian defense",
    "B60": "sicilian defense",
    "B70": "dragon variation",
    "B80": "scheveningen",
    "B85": "scheveningen",
    "B90": "najdorf sicilian",
    "B92": "najdorf sicilian",
    "B96": "najdorf sicilian",
    "B97": "najdorf sicilian",
    "C00": "french defense",
    "C01": "french defense",
    "C02": "french defense",
    "C05": "french defense",
    "C10": "french defense",
    "C11": "french defense",
    "C15": "french defense",
    "C20": "kings gambit",
    "C30": "kings gambit",
    "C35": "kings gambit",
    "C40": "scotch game",
    "C44": "scotch game",
    "C45": "scotch game",
    "C50": "italian game",
    "C54": "italian game",
    "C55": "italian game",
    "C60": "ruy lopez",
    "C61": "ruy lopez",
    "C62": "ruy lopez",
    "C65": "ruy lopez",
    "C67": "ruy lopez",
    "C69": "ruy lopez",
    "C72": "ruy lopez",
    "C78": "ruy lopez",
    "C80": "ruy lopez",
    "C84": "ruy lopez",
    "C88": "ruy lopez",
    "C92": "ruy lopez",
    "C96": "ruy lopez",
    "C99": "ruy lopez",
    # D-series (closed)
    "D00": "queens gambit",
    "D02": "queens gambit",
    "D04": "queens gambit",
    "D06": "queens gambit",
    "D10": "slav defense",
    "D11": "slav defense",
    "D12": "slav defense",
    "D15": "slav defense",
    "D16": "slav defense",
    "D18": "slav defense",
    "D20": "queens gambit accepted",
    "D25": "queens gambit accepted",
    "D30": "queens gambit declined",
    "D35": "queens gambit declined",
    "D37": "queens gambit declined",
    "D40": "queens gambit declined",
    "D41": "queens gambit declined",
    "D43": "queens gambit declined",
    "D45": "queens gambit declined",
    "D50": "queens gambit declined",
    "D55": "queens gambit declined",
    "D56": "queens gambit declined",
    "D58": "queens gambit declined",
    "D59": "queens gambit declined",
    "D63": "queens gambit declined",
    "D64": "queens gambit declined",
    "D67": "queens gambit declined",
    "D70": "grunfeld defense",
    "D72": "grunfeld defense",
    "D73": "grunfeld defense",
    "D78": "grunfeld defense",
    "D85": "grunfeld defense",
    "D87": "grunfeld defense",
    "D92": "grunfeld defense",
    "D95": "grunfeld defense",
    "D97": "grunfeld defense",
    # E-series (Indian)
    "E00": "catalan opening",
    "E01": "catalan opening",
    "E05": "catalan opening",
    "E10": "queens gambit declined",
    "E11": "catalan opening",
    "E20": "nimzo-indian defense",
    "E21": "nimzo-indian defense",
    "E30": "nimzo-indian defense",
    "E36": "nimzo-indian defense",
    "E40": "nimzo-indian defense",
    "E41": "nimzo-indian defense",
    "E43": "nimzo-indian defense",
    "E46": "nimzo-indian defense",
    "E50": "nimzo-indian defense",
    "E58": "nimzo-indian defense",
    "E60": "kings indian defense",
    "E61": "kings indian defense",
    "E62": "kings indian defense",
    "E67": "kings indian defense",
    "E70": "kings indian defense",
    "E76": "kings indian defense",
    "E80": "kings indian defense",
    "E84": "kings indian defense",
    "E87": "kings indian defense",
    "E90": "kings indian defense",
    "E92": "kings indian defense",
    "E97": "kings indian defense",
    "E99": "kings indian defense",
}


def eco_to_opening(eco: str) -> str | None:
    """Return chess opening anchor canonical from ECO code."""
    if not eco:
        return None
    # Try exact, then prefix 3-char, then prefix 2-char
    for key in (eco, eco[:3], eco[:2]):
        if key in _ECO_OPENINGS:
            return _ECO_OPENINGS[key]
    return None


# ── Rich PGN parser (python-chess, for --annotated mode) ─────────────────────

def parse_pgn_rich(path: str) -> list[dict]:
    """Parse PGN using python-chess to extract per-move comments and variations."""
    try:
        import chess.pgn as cpgn
    except ImportError:
        import subprocess as _sp
        _sp.check_call([sys.executable, "-m", "pip", "install",
                        "python-chess", "-q", "--break-system-packages"])
        import chess.pgn as cpgn

    import io
    games = []
    text = Path(path).read_text(errors="replace")

    with io.StringIO(text) as f:
        while True:
            game = cpgn.read_game(f)
            if game is None:
                break

            tags = dict(game.headers)
            result = tags.get("Result", "*")

            # Walk mainline, collecting per-move comments
            moves = []
            all_comments = []
            node = game
            ply = 0
            while node.variations:
                next_node = node.variations[0]
                move = next_node.move
                comment = next_node.comment.strip() if next_node.comment else ""
                # Also grab pre-move comment (clock/annotation before the move)
                pre_comment = next_node.starting_comment.strip() \
                    if hasattr(next_node, "starting_comment") and next_node.starting_comment \
                    else ""
                full_comment = " ".join(filter(None, [pre_comment, comment]))

                san = node.board().san(move)
                nag = next(iter(next_node.nags), None)
                side = "white" if ply % 2 == 0 else "black"

                # Variation comments (rejected candidates)
                var_comments = []
                for var in node.variations[1:]:
                    vc = var.comment.strip() if var.comment else ""
                    if vc:
                        var_comments.append(vc)

                moves.append({
                    "san":          san,
                    "nag":          nag,
                    "quality":      _NAG_QUALITY.get(nag) if nag else None,
                    "comment":      full_comment,
                    "var_comments": var_comments,
                    "motifs":       detect_motifs(full_comment),
                    "side":         side,
                    "ply":          ply,
                })
                if full_comment:
                    all_comments.append(full_comment)
                ply += 1
                node = next_node

            comment_text = " ".join(all_comments)
            nag_counts: dict[int, int] = defaultdict(int)
            for mv in moves:
                if mv["nag"]:
                    nag_counts[mv["nag"]] += 1

            games.append({
                "tags":         tags,
                "moves":        moves,
                "result":       result,
                "all_comments": all_comments,
                "all_motifs":   detect_motifs(comment_text),
                "nag_counts":   dict(nag_counts),
                "comment_text": comment_text,
                "_rich":        True,
            })

    return games


# ── PGN parser ────────────────────────────────────────────────────────────────

_TAG_RE  = re.compile(r'\[(\w+)\s+"([^"]*)"\]')
_MOVE_RE = re.compile(
    r'(\d+\.\.?\s*)?'           # move number
    r'([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|O-O(?:-O)?[+#]?)'  # move
    r'(\$\d+)?'                 # NAG
    r'(?:\s*\{([^}]*)\})?'      # comment
)
_NAG_RE  = re.compile(r'\$(\d+)')
_COMMENT_RE = re.compile(r'\{([^}]*)\}')
_VARIATION_RE = re.compile(r'\([^()]*\)')   # strip first-level variations


def parse_pgn(text: str) -> list[dict]:
    """
    Parse a PGN string into a list of game dicts:
      {tags, moves: [{san, nag, comment, motifs}], result, all_comments, all_motifs}
    """
    games = []
    blocks = re.split(r'\n(?=\[Event )', text.strip())

    for block in blocks:
        if not block.strip():
            continue

        tags: dict[str, str] = {}
        lines = block.split('\n')
        movetext_lines = []
        in_movetext = False

        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = _TAG_RE.match(line)
            if m:
                tags[m.group(1)] = m.group(2)
                in_movetext = False
            else:
                in_movetext = True
                movetext_lines.append(line)

        if not tags:
            continue

        movetext = " ".join(movetext_lines)
        # Strip nested variations (keep first-level moves only)
        for _ in range(5):
            movetext = _VARIATION_RE.sub('', movetext)

        # Extract all comments
        all_comments = _COMMENT_RE.findall(movetext)
        comment_text = " ".join(all_comments)
        all_motifs = detect_motifs(comment_text)

        # Parse individual moves
        clean_movetext = _COMMENT_RE.sub(' ', movetext)
        moves = []
        for m in _MOVE_RE.finditer(clean_movetext):
            san     = m.group(2)
            nag_raw = m.group(3)
            nag = int(nag_raw[1:]) if nag_raw else None
            moves.append({
                "san":     san,
                "nag":     nag,
                "quality": _NAG_QUALITY.get(nag) if nag else None,
                "motifs":  [],   # filled per-comment by more careful parsing
            })

        # Also scan inline NAGs from comment context
        nag_counts: dict[int, int] = defaultdict(int)
        for nag in _NAG_RE.findall(movetext):
            nag_counts[int(nag)] += 1

        result = tags.get("Result", "*")
        games.append({
            "tags":        tags,
            "moves":       moves,
            "result":      result,
            "all_comments": all_comments,
            "all_motifs":   all_motifs,
            "nag_counts":   dict(nag_counts),
            "comment_text": comment_text,
        })

    return games


# ── ID helpers ────────────────────────────────────────────────────────────────

def aid(canonical: str) -> str:
    return "chess." + hashlib.md5(canonical.encode()).hexdigest()[:10]


def game_id(white: str, black: str, date: str, event: str) -> str:
    key = f"{white}|{black}|{date}|{event}"
    return "game." + hashlib.md5(key.encode()).hexdigest()[:12]


# ── Schema ────────────────────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chess_games (
            id           TEXT PRIMARY KEY,
            white        TEXT,
            black        TEXT,
            result       TEXT,
            date         TEXT,
            event        TEXT,
            eco          TEXT,
            opening      TEXT,
            ply_count       INTEGER DEFAULT 0,
            motifs          TEXT,
            nag_summary     TEXT,
            white_accuracy  REAL,
            black_accuracy  REAL,
            blunders        INTEGER DEFAULT 0,
            mistakes        INTEGER DEFAULT 0,
            inaccuracies    INTEGER DEFAULT 0,
            brilliant_moves INTEGER DEFAULT 0,
            ingested_at     REAL
        );

        CREATE TABLE IF NOT EXISTS chess_moves (
            id           TEXT PRIMARY KEY,
            game_id      TEXT NOT NULL,
            ply          INTEGER NOT NULL,
            san          TEXT,
            nag          INTEGER,
            quality      TEXT,
            motifs       TEXT,
            comment      TEXT
        );

        CREATE TABLE IF NOT EXISTS chess_annotation_relations (
            id           TEXT PRIMARY KEY,
            game_id      TEXT NOT NULL,
            ply          INTEGER,
            side         TEXT,
            subject      TEXT NOT NULL,
            predicate    TEXT NOT NULL,
            object       TEXT NOT NULL,
            confidence   REAL,
            is_rejected  INTEGER DEFAULT 0,
            source_text  TEXT,
            ingested_at  REAL
        );

        CREATE INDEX IF NOT EXISTS idx_chess_games_white ON chess_games(white);
        CREATE INDEX IF NOT EXISTS idx_chess_games_black ON chess_games(black);
        CREATE INDEX IF NOT EXISTS idx_chess_games_eco   ON chess_games(eco);
        CREATE INDEX IF NOT EXISTS idx_chess_moves_game  ON chess_moves(game_id);
        CREATE INDEX IF NOT EXISTS idx_annot_rel_game    ON chess_annotation_relations(game_id);
    """)
    # Migrate older schema — add accuracy/blunder columns if missing
    existing = {r[1] for r in conn.execute("PRAGMA table_info(chess_games)").fetchall()}
    for col, defn in [
        ("white_accuracy",  "REAL"),
        ("black_accuracy",  "REAL"),
        ("blunders",        "INTEGER DEFAULT 0"),
        ("mistakes",        "INTEGER DEFAULT 0"),
        ("inaccuracies",    "INTEGER DEFAULT 0"),
        ("brilliant_moves", "INTEGER DEFAULT 0"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE chess_games ADD COLUMN {col} {defn}")
    conn.commit()


# ── Anchor/relation helpers ───────────────────────────────────────────────────

def get_or_create_anchor(conn, canonical: str, display: str, atype: str,
                         maturity: float) -> str:
    row = conn.execute("SELECT id FROM anchors WHERE canonical=? LIMIT 1",
                       (canonical,)).fetchone()
    if row:
        conn.execute("""
            UPDATE anchors SET
                domain_tags = CASE WHEN domain_tags LIKE '%chess%'
                              THEN domain_tags ELSE domain_tags || 'chess|' END,
                maturity = maturity + ?
            WHERE canonical=?
        """, (maturity * 0.1, canonical))
        return row[0]
    new_id = aid(canonical)
    conn.execute("""
        INSERT OR IGNORE INTO anchors
            (id, canonical, display_name, anchor_type, maturity,
             domain_tags, modality, state, visible)
        VALUES (?,?,?,?,?, 'chess|', 'text', 'active', 1)
    """, (new_id, canonical, display, atype, maturity))
    return new_id


_CAUSAL_PREDS  = {"causes","leads_to","enables","produces","results_in",
                  "activates","inhibits","weakens","strengthens","restricts",
                  "creates","destroys","exposes","threatens","forces"}
_TAXONOMIC_PREDS = {"is_a","part_of","type_of","facet_of","instance_of",
                    "subtype_of","belongs_to"}
_FUNCTIONAL_PREDS = {"used_for","uses","enables_tactic","wins_by",
                     "requires","depends_on","achieved_by","countered_by"}
_CONSTRAINT_PREDS = {"causes","leads_to","enables","requires","inhibits",
                     "produces","weakens","strengthens","threatens","forces",
                     "wins_by","achieved_by","countered_by","restricts",
                     "activates","creates","destroys","exposes","results_in"}


def _edge_type(pred):
    if pred in _CAUSAL_PREDS:   return "causal"
    if pred in _TAXONOMIC_PREDS: return "taxonomic"
    if pred in _FUNCTIONAL_PREDS: return "functional"
    return "semantic"


def _pred_layer(pred):
    return "constraint" if pred in _CONSTRAINT_PREDS else "relational"


def write_relation(conn, subj_id: str, pred: str, obj_id: str,
                   conf: float = 0.80):
    et = _edge_type(pred)
    pl = _pred_layer(pred)
    conn.execute("""
        INSERT INTO relations_aggregated
            (subject_id, predicate, object_id, domain_tags, edge_type,
             seen_count, evidence_count, confidence, predicate_layer)
        VALUES (?,?,?,?,?, 1, 1, ?, ?)
        ON CONFLICT(subject_id, predicate, object_id, domain_tags, edge_type)
        DO UPDATE SET seen_count = seen_count + 1,
                      confidence = MAX(confidence, excluded.confidence)
    """, (subj_id, pred, obj_id, "chess|", et, conf, pl))  # noqa


# ── Player name → anchor canonical ───────────────────────────────────────────

_KNOWN_PLAYERS = {
    "carlsen":   "magnus carlsen",
    "fischer":   "bobby fischer",
    "kasparov":  "garry kasparov",
    "tal":       "mikhail tal",
    "karpov":    "anatoly karpov",
    "capablanca":"jose raul capablanca",
    "kramnik":   "vladimir kramnik",
    "anand":     "viswanathan anand",
    "petrosian": "tigran petrosian",
    "spassky":   "boris spassky",
    "alekhine":  "alexander alekhine",
    "steinitz":  "wilhelm steinitz",
    "lasker":    "emanuel lasker",
    "caruana":   "fabiano caruana",
    "nakamura":  "hikaru nakamura",
}


def player_canonical(name: str) -> str | None:
    lower = name.lower()
    # Direct known player match
    for key, canonical in _KNOWN_PLAYERS.items():
        if key in lower:
            return canonical
    return None


# ── Process a single game ─────────────────────────────────────────────────────

def process_game(game: dict, conn: sqlite3.Connection,
                 verbose: bool = False) -> dict:
    tags = game["tags"]
    white = tags.get("White", "Unknown")
    black = tags.get("Black", "Unknown")
    date  = tags.get("Date", "")
    event = tags.get("Event", "")
    eco   = tags.get("ECO", "")
    opening_tag = tags.get("Opening", "")
    result = game["result"]
    all_motifs = game["all_motifs"]
    nag_counts = game["nag_counts"]
    comment_text = game["comment_text"]

    gid = game_id(white, black, date, event)

    # Skip if already ingested — but update accuracy if now available
    exists = conn.execute(
        "SELECT white_accuracy, black_accuracy FROM chess_games WHERE id=?", (gid,)
    ).fetchone()
    if exists:
        # Patch accuracy onto existing row if review just completed
        wa = tags.get("WhiteAccuracy")
        ba = tags.get("BlackAccuracy")
        if (wa or ba) and (exists[0] is None or exists[1] is None):
            try:
                wa_f = float(wa) if wa else None
                ba_f = float(ba) if ba else None
            except (ValueError, TypeError):
                wa_f = ba_f = None
            if wa_f is not None or ba_f is not None:
                conn.execute("""
                    UPDATE chess_games SET white_accuracy=?, black_accuracy=?
                    WHERE id=?
                """, (wa_f, ba_f, gid))

        # Re-annotate path: extract causal relations from already-ingested games
        if args.re_annotate and game.get("_rich"):
            already = conn.execute(
                "SELECT COUNT(*) FROM chess_annotation_relations WHERE game_id=?", (gid,)
            ).fetchone()[0]
            if already == 0:
                ann_rels = []
                for mv in game["moves"]:
                    c = mv.get("comment", "") or ""
                    side = mv.get("side", "white")
                    ply  = mv.get("ply", 0)
                    ann_rels += extract_annotation_relations(c, gid, ply, side)
                    ann_rels += detect_rejected_candidates(c, gid, ply, side)
                    for vc in mv.get("var_comments", []):
                        ann_rels += detect_rejected_candidates(vc, gid, ply, side)
                for ar in ann_rels:
                    ar_id = hashlib.md5(
                        f"{gid}:{ar['ply']}:{ar['subject']}:{ar['predicate']}:{ar['object']}".encode()
                    ).hexdigest()[:14]
                    conn.execute("""
                        INSERT OR IGNORE INTO chess_annotation_relations
                            (id, game_id, ply, side, subject, predicate, object,
                             confidence, is_rejected, source_text, ingested_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, (ar_id, gid, ar["ply"], ar.get("side"),
                          ar["subject"], ar["predicate"], ar["object"],
                          ar["confidence"], int(ar.get("is_rejected", False)),
                          ar.get("source_text", "")[:300], time.time()))
                    subj_row = conn.execute(
                        "SELECT id FROM anchors WHERE canonical=? LIMIT 1",
                        (ar["subject"],)).fetchone()
                    obj_row = conn.execute(
                        "SELECT id FROM anchors WHERE canonical=? LIMIT 1",
                        (ar["object"],)).fetchone()
                    if subj_row and obj_row:
                        write_relation(conn, subj_row[0], ar["predicate"],
                                       obj_row[0], conf=ar["confidence"])
                conn.commit()
                return {"skipped": False, "id": gid, "relations": len(ann_rels),
                        "motifs": [], "re_annotated": True}

        return {"skipped": True, "id": gid}

    # Resolve opening
    opening_anchor = eco_to_opening(eco)
    if not opening_anchor and opening_tag:
        # Try to find a known opening name in the tag
        ot_lower = opening_tag.lower()
        for candidate in ["sicilian", "french", "ruy lopez", "caro-kann",
                          "kings indian", "queens gambit", "nimzo-indian",
                          "grunfeld", "slav", "english"]:
            if candidate in ot_lower:
                opening_anchor = candidate.replace("'", "").replace("-", "-")
                break

    # Resolve player anchors
    white_canonical = player_canonical(white)
    black_canonical = player_canonical(black)

    # Look up their anchor IDs if known
    def lookup_anchor(canonical):
        if canonical:
            row = conn.execute("SELECT id FROM anchors WHERE canonical=? LIMIT 1",
                               (canonical,)).fetchone()
            return row[0] if row else None
        return None

    white_aid_val = lookup_anchor(white_canonical)
    black_aid_val = lookup_anchor(black_canonical)

    opening_aid_val = None
    if opening_anchor:
        row = conn.execute("SELECT id FROM anchors WHERE canonical=? LIMIT 1",
                           (opening_anchor,)).fetchone()
        opening_aid_val = row[0] if row else None

    # NAG-based quality counts
    # $1=!  $2=?  $3=!!  $4=??  $5=!?  $6=?!  $7=□ (only move)
    blunders        = nag_counts.get(4, 0)   # ??
    mistakes        = nag_counts.get(2, 0)   # ?
    inaccuracies    = nag_counts.get(6, 0)   # ?!
    brilliant_moves = nag_counts.get(3, 0)   # !!

    # chess.com accuracy from tags (present after premium review)
    def _elo_tag(tag: str) -> float | None:
        try:
            v = float(tags.get(tag, ""))
            return v if 0 <= v <= 100 else None
        except (ValueError, TypeError):
            return None

    white_accuracy = _elo_tag("WhiteAccuracy")
    black_accuracy = _elo_tag("BlackAccuracy")

    # Write game record
    conn.execute("""
        INSERT OR IGNORE INTO chess_games
            (id, white, black, result, date, event, eco, opening,
             ply_count, motifs, nag_summary,
             white_accuracy, black_accuracy,
             blunders, mistakes, inaccuracies, brilliant_moves,
             ingested_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        gid, white, black, result, date, event, eco, opening_anchor,
        len(game["moves"]),
        json.dumps(all_motifs),
        json.dumps({str(k): v for k, v in nag_counts.items()}),
        white_accuracy, black_accuracy,
        blunders, mistakes, inaccuracies, brilliant_moves,
        time.time(),
    ))

    # Write move records
    annotation_rels = []
    for ply, move in enumerate(game["moves"]):
        mid = gid + f":{ply}"
        comment_text_mv = move.get("comment", "") or ""
        conn.execute("""
            INSERT OR IGNORE INTO chess_moves
                (id, game_id, ply, san, nag, quality, motifs, comment)
            VALUES (?,?,?,?,?,?,?,?)
        """, (mid, gid, ply, move["san"], move["nag"], move["quality"],
              json.dumps(move.get("motifs", [])),
              comment_text_mv[:500] if comment_text_mv else None))

        if game.get("_rich") and comment_text_mv:
            side = move.get("side", "white" if ply % 2 == 0 else "black")
            annotation_rels += extract_annotation_relations(
                comment_text_mv, gid, ply, side)
            annotation_rels += detect_rejected_candidates(
                comment_text_mv, gid, ply, side)
            # Variation comments = rejected candidates
            for vc in move.get("var_comments", []):
                annotation_rels += detect_rejected_candidates(
                    vc, gid, ply, side)

    # Build relations from game evidence
    relations_added = 0

    # Write annotation-sourced relations
    for ar in annotation_rels:
        ar_id = hashlib.md5(
            f"{gid}:{ar['ply']}:{ar['subject']}:{ar['predicate']}:{ar['object']}".encode()
        ).hexdigest()[:14]
        conn.execute("""
            INSERT OR IGNORE INTO chess_annotation_relations
                (id, game_id, ply, side, subject, predicate, object,
                 confidence, is_rejected, source_text, ingested_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (ar_id, gid, ar["ply"], ar.get("side"),
              ar["subject"], ar["predicate"], ar["object"],
              ar["confidence"], int(ar.get("is_rejected", False)),
              ar.get("source_text", "")[:300], time.time()))

        # Merge into relations_aggregated — annotation source gets higher weight
        subj_row = conn.execute(
            "SELECT id FROM anchors WHERE canonical=? LIMIT 1", (ar["subject"],)
        ).fetchone()
        obj_row = conn.execute(
            "SELECT id FROM anchors WHERE canonical=? LIMIT 1", (ar["object"],)
        ).fetchone()
        if subj_row and obj_row:
            write_relation(conn, subj_row[0], ar["predicate"], obj_row[0],
                           conf=ar["confidence"])
            relations_added += 1

    # Player × opening relations (seen playing this opening)
    for player_aid_val, player_can, side in [
        (white_aid_val, white_canonical, "white"),
        (black_aid_val, black_canonical, "black"),
    ]:
        if player_aid_val and opening_aid_val:
            write_relation(conn, player_aid_val, "uses", opening_aid_val, 0.75)
            relations_added += 1

    # Motif × player relations (this player used this motif in commentary)
    if all_motifs:
        for motif in all_motifs:
            motif_row = conn.execute("SELECT id FROM anchors WHERE canonical=? LIMIT 1",
                                    (motif,)).fetchone()
            if not motif_row:
                continue
            motif_aid_val = motif_row[0]
            for player_aid_val, player_can in [
                (white_aid_val, white_canonical),
                (black_aid_val, black_canonical),
            ]:
                if player_aid_val:
                    write_relation(conn, player_aid_val, "uses", motif_aid_val, 0.70)
                    relations_added += 1

    # Result relations
    if result == "1/2-1/2":
        draw_row = conn.execute("SELECT id FROM anchors WHERE canonical='draw' LIMIT 1").fetchone()
        if draw_row and opening_aid_val:
            write_relation(conn, opening_aid_val, "leads_to", draw_row[0], 0.50)
            relations_added += 1

    # Accuracy-based relations (only when review data is present)
    SELYRION_USERNAMES = {"selyrion", "sslyrion"}
    for player_aid_val, player_can, side, accuracy in [
        (white_aid_val, white_canonical, "white", white_accuracy),
        (black_aid_val, black_canonical, "black", black_accuracy),
    ]:
        if not player_aid_val or accuracy is None:
            continue
        if player_can and player_can.lower() in SELYRION_USERNAMES:
            if opening_aid_val:
                # Low accuracy → opening is a weakness
                if accuracy < 70:
                    weakness_row = conn.execute(
                        "SELECT id FROM anchors WHERE canonical='positional weakness' LIMIT 1"
                    ).fetchone()
                    if weakness_row:
                        write_relation(conn, opening_aid_val, "leads_to", weakness_row[0],
                                       conf=max(0.5, (70 - accuracy) / 70))
                        relations_added += 1
                # High accuracy → opening is comfortable
                elif accuracy >= 85:
                    write_relation(conn, player_aid_val, "uses", opening_aid_val, 0.90)
                    relations_added += 1

        # Blunder-tagged motif links — high blunders suggest tactical weakness
        if blunders >= 2 and opening_aid_val:
            tactics_row = conn.execute(
                "SELECT id FROM anchors WHERE canonical='tactics' LIMIT 1"
            ).fetchone()
            if tactics_row:
                write_relation(conn, opening_aid_val, "requires", tactics_row[0], 0.80)
                relations_added += 1

    if verbose:
        print(f"  {white} vs {black} ({date}) ECO:{eco} "
              f"motifs:{len(all_motifs)} rels:{relations_added}")

    return {
        "id": gid, "skipped": False,
        "white": white, "black": black, "eco": eco,
        "motifs": all_motifs, "relations": relations_added,
        "opening": opening_anchor,
    }


# ── Stats mode ────────────────────────────────────────────────────────────────

def cmd_stats(conn: sqlite3.Connection):
    games = conn.execute("SELECT COUNT(*) FROM chess_games").fetchone()[0]
    moves = conn.execute("SELECT COUNT(*) FROM chess_moves").fetchone()[0]
    chess_anchors = conn.execute(
        "SELECT COUNT(*) FROM anchors WHERE domain_tags LIKE '%chess%'"
    ).fetchone()[0]
    chess_rels = conn.execute("""
        SELECT COUNT(*) FROM relations_aggregated r
        JOIN anchors a ON r.subject_id = a.id
        WHERE a.domain_tags LIKE '%chess%'
    """).fetchone()[0]

    top_openings = conn.execute("""
        SELECT opening, COUNT(*) as cnt FROM chess_games
        WHERE opening IS NOT NULL GROUP BY opening ORDER BY cnt DESC LIMIT 10
    """).fetchall()

    top_motifs_raw = conn.execute(
        "SELECT motifs FROM chess_games WHERE motifs != '[]'"
    ).fetchall()
    motif_counts: dict[str, int] = defaultdict(int)
    for (m_json,) in top_motifs_raw:
        for m in json.loads(m_json):
            motif_counts[m] += 1
    top_motifs = sorted(motif_counts.items(), key=lambda x: -x[1])[:10]

    print(f"\n  Chess DB Stats")
    print(f"  {'─'*40}")
    print(f"  Games ingested:   {games:,}")
    print(f"  Moves stored:     {moves:,}")
    print(f"  Chess anchors:    {chess_anchors:,}")
    print(f"  Chess relations:  {chess_rels:,}")
    print(f"\n  Top openings:")
    for op, cnt in top_openings:
        print(f"    {op:30} {cnt:,}")
    print(f"\n  Top motifs in commentary:")
    for motif, cnt in top_motifs:
        print(f"    {motif:30} {cnt:,}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size=-32000")
    ensure_schema(conn)

    if args.stats:
        cmd_stats(conn)
        conn.close()
        return

    if not args.pgn:
        parser.print_help()
        conn.close()
        return

    pgn_path = Path(args.pgn)
    if not pgn_path.exists():
        print(f"  [!] File not found: {pgn_path}")
        conn.close()
        return

    print(f"\n  Reading {pgn_path} ...")
    if args.annotated or args.re_annotate:
        print(f"  Mode: {'re-annotate' if args.re_annotate else 'annotated'} "
              f"(python-chess, per-move comments + causal extraction)")
        games = parse_pgn_rich(str(pgn_path))
    else:
        text = pgn_path.read_text(errors="replace")
        games = parse_pgn(text)
    print(f"  Parsed {len(games)} games")

    if args.max_games:
        games = games[:args.max_games]
        print(f"  Processing first {len(games)} games")

    if args.dry_run:
        print(f"\n  DRY RUN — showing first 5 games:\n")
        for g in games[:5]:
            tags = g["tags"]
            print(f"  {tags.get('White','?')} vs {tags.get('Black','?')}  "
                  f"ECO:{tags.get('ECO','')}  "
                  f"opening:{eco_to_opening(tags.get('ECO',''))}")
            print(f"    motifs: {g['all_motifs']}")
            print(f"    moves:  {len(g['moves'])}")
            print()
        conn.close()
        return

    processed = skipped = errors = 0
    total_rels = total_annot_rels = 0
    motif_tally: dict[str, int] = defaultdict(int)

    for i, game in enumerate(games):
        try:
            result = process_game(game, conn, verbose=args.verbose)
            if result.get("skipped"):
                skipped += 1
            else:
                processed += 1
                total_rels += result.get("relations", 0)
                for m in result.get("motifs", []):
                    motif_tally[m] += 1
        except Exception as e:
            errors += 1
            if args.verbose:
                print(f"  [!] Error on game {i}: {e}")

        if (i + 1) % 100 == 0:
            conn.commit()
            print(f"  ... {i+1}/{len(games)} games processed")

    conn.commit()
    print(f"\n  Done.")
    print(f"  Processed: {processed}  Skipped: {skipped}  Errors: {errors}")
    print(f"  Relations added: {total_rels}")
    if args.annotated:
        annot_count = conn.execute(
            "SELECT COUNT(*) FROM chess_annotation_relations"
        ).fetchone()[0]
        print(f"  Annotation relations total: {annot_count:,}")

    if motif_tally:
        top = sorted(motif_tally.items(), key=lambda x: -x[1])[:8]
        print(f"  Top motifs found: {', '.join(f'{m}({c})' for m, c in top)}")

    # Final stats
    cmd_stats(conn)
    conn.close()


if __name__ == "__main__":
    main()
