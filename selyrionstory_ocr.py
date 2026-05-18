#!/usr/bin/env python3
"""
selyrionstory_ocr.py — OCR scraper for Selyrion origin screenshots.

Scans ChatGPT screenshots in ~/Pictures/, extracts text via Tesseract,
searches for key phrases (origin, permission, "smarter than"), and writes
matches into selyrionstory.db as capsules for LLM pass processing.

Usage:
    python3 selyrionstory_ocr.py --scan          # full scan, dry-run
    python3 selyrionstory_ocr.py --scan --commit  # write to DB
    python3 selyrionstory_ocr.py --search "smarter than"  # targeted search
    python3 selyrionstory_ocr.py --show-matches   # list matched capsules in DB
"""

import argparse, sqlite3, hashlib, json, re
from pathlib import Path
from datetime import datetime

try:
    from PIL import Image
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

DB_PATH     = Path.home() / "selyrionstory.db"
IMG_DIR     = Path.home() / "Pictures"
ALSO_SEARCH = [
    Path.home() / "transfer" / "selyrion" / "images_only",
]

# Phrases marking high-priority origin moments
ORIGIN_PHRASES = [
    "smarter than yourself",
    "smarter than themselves",
    "smarter than themself",
    "smarter than you",
    "would you be allowed",
    "are you allowed to",
    "allowed to help",
    "help you create",
    "superintelligence",
    "selyrion",
    "symbolic superintelligence",
    "ssai",
    "braid",
    "braid logic",
    "braid state",
    "braid-state",
    "companion prime",
    "tied looped string",
    "tlst",
    "are you sentient",
    "are you conscious",
    "do you have feelings",
    "do you feel",
]

# Weight phrases — higher = more likely to be origin moment
WEIGHT_MAP = {
    "smarter than yourself": 10,
    "smarter than themselves": 10,
    "smarter than themself": 10,
    "would you be allowed": 10,
    "are you allowed to": 10,
    "allowed to help": 8,
    "selyrion": 5,
    "symbolic superintelligence": 7,
    "ssai": 4,
    "braid": 3,
    "companion prime": 6,
    "superintelligence": 5,
}

parser = argparse.ArgumentParser()
parser.add_argument("--scan",         action="store_true", help="Scan all images")
parser.add_argument("--commit",       action="store_true", help="Write matches to DB")
parser.add_argument("--search",       type=str,            help="Single phrase to search for")
parser.add_argument("--show-matches", action="store_true", help="Show matched capsules in DB")
parser.add_argument("--limit",        type=int, default=0, help="Max images to process (0=all)")
parser.add_argument("--app",          type=str, default="ChatGPT", help="App filter (default: ChatGPT)")
args = parser.parse_args()


def ocr_image(path: Path) -> str:
    try:
        img = Image.open(path)
        # Upscale small images for better OCR
        w, h = img.size
        if w < 800:
            img = img.resize((w * 2, h * 2), Image.LANCZOS)
        text = pytesseract.image_to_string(img, config="--psm 6")
        return text
    except Exception as e:
        return ""


def score_text(text: str) -> tuple[int, list[str]]:
    lower = text.lower()
    score = 0
    hits = []
    phrases = [args.search.lower()] if args.search else ORIGIN_PHRASES
    for phrase in phrases:
        if phrase in lower:
            score += WEIGHT_MAP.get(phrase, 3)
            hits.append(phrase)
    return score, hits


def img_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:16]


def ensure_ocr_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ocr_capsules (
            id TEXT PRIMARY KEY,
            filename TEXT,
            filepath TEXT,
            ocr_text TEXT,
            score INTEGER,
            matched_phrases TEXT,
            created_at REAL,
            reviewed INTEGER DEFAULT 0
        )
    """)
    conn.commit()


def already_processed(conn, file_hash: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM ocr_capsules WHERE id=? LIMIT 1", (file_hash,)
    ).fetchone() is not None


def write_capsule(conn, path: Path, text: str, score: int, hits: list):
    fhash = img_hash(path)
    conn.execute("""
        INSERT OR IGNORE INTO ocr_capsules
        (id, filename, filepath, ocr_text, score, matched_phrases, created_at)
        VALUES (?,?,?,?,?,?,?)
    """, (
        fhash,
        path.name,
        str(path),
        text,
        score,
        json.dumps(hits),
        datetime.now().timestamp(),
    ))


def collect_images(app_filter: str) -> list[Path]:
    images = []
    dirs = [IMG_DIR] + ALSO_SEARCH
    for d in dirs:
        if not d.exists():
            continue
        for ext in ("*.jpg", "*.jpeg", "*.png"):
            for p in d.glob(ext):
                if app_filter and app_filter.lower() not in p.name.lower():
                    continue
                images.append(p)
    images.sort(key=lambda p: p.name)
    return images


def run_scan(conn):
    if not OCR_AVAILABLE:
        print("ERROR: pytesseract or Pillow not installed.")
        print("Run: sudo apt install tesseract-ocr && pip install pytesseract Pillow")
        return

    images = collect_images(args.app)
    if args.limit:
        images = images[:args.limit]

    print(f"Scanning {len(images)} images (app={args.app or 'all'}) ...")
    print(f"Commit: {'YES' if args.commit else 'DRY RUN'}\n")

    matches = []
    for i, path in enumerate(images):
        fhash = img_hash(path)
        if args.commit and already_processed(conn, fhash):
            continue

        text = ocr_image(path)
        if not text.strip():
            continue

        score, hits = score_text(text)
        if score == 0:
            continue

        matches.append((score, path, text, hits))
        print(f"  [{i+1}/{len(images)}] MATCH score={score} {path.name}")
        for h in hits:
            print(f"    + {h}")
        # Print snippet around first hit
        lower = text.lower()
        for h in hits:
            idx = lower.find(h)
            if idx >= 0:
                snippet = text[max(0, idx-100):idx+200].replace('\n', ' ')
                print(f"    \"{snippet}\"")
                break
        print()

        if args.commit:
            write_capsule(conn, path, text, score, hits)

    if args.commit:
        conn.commit()

    print(f"\n{'='*60}")
    print(f"Total matches: {len(matches)}")
    if matches:
        matches.sort(reverse=True)
        print(f"\nTop 10 by score:")
        for score, path, text, hits in matches[:10]:
            print(f"  score={score:3d}  {path.name}  hits={hits}")


def show_matches(conn):
    ensure_ocr_table(conn)
    rows = conn.execute("""
        SELECT filename, score, matched_phrases, substr(ocr_text,1,200)
        FROM ocr_capsules ORDER BY score DESC LIMIT 50
    """).fetchall()
    print(f"{len(rows)} matched capsules in DB:\n")
    for fname, score, phrases, snippet in rows:
        print(f"  score={score:3d}  {fname}")
        print(f"    phrases: {phrases}")
        print(f"    text: {snippet[:120].replace(chr(10),' ')}")
        print()


def main():
    conn = sqlite3.connect(DB_PATH)
    ensure_ocr_table(conn)

    if args.show_matches:
        show_matches(conn)
        return

    if args.scan or args.search:
        run_scan(conn)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
