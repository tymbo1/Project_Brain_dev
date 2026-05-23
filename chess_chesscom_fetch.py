#!/usr/bin/env python3
"""
chess_chesscom_fetch.py — Download PGNs from chess.com public API.

FAIR PLAY & TERMS OF SERVICE COMPLIANCE
----------------------------------------
This script uses the chess.com public API to download already-completed games
for offline knowledge ingestion only. This is explicitly permitted by chess.com.

The following are STRICTLY PROHIBITED under chess.com Fair Play Policy and must
never occur:
  - Querying the CMS, any chess engine, or any database DURING a live game
  - Receiving move suggestions or evaluations while a game is in progress
  - Any form of computer assistance to a player in a rated or unrated game

Selyrion's account (selyrion) must play all games without computer assistance.
Post-game analysis via the CMS is fine. Real-time assistance is not.
Violation can result in permanent account closure.
https://www.chess.com/legal/fair-play

Fetches game archives for listed players across a date range,
extracts PGNs, and writes a combined file ready for chess_pgn_ingest.py.

Usage:
  python3 chess_chesscom_fetch.py --out chess_chesscom.pgn
  python3 chess_chesscom_fetch.py --out chess_chesscom.pgn --months 6
"""

import sys, json, time, argparse, re
from pathlib import Path
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

parser = argparse.ArgumentParser()
parser.add_argument("--out",    default="chess_chesscom.pgn")
parser.add_argument("--months", type=int, default=3, help="How many months back to fetch")
parser.add_argument("--min-elo", type=int, default=2600, help="Min avg Elo filter (0 = no filter)")
parser.add_argument("--user",   action="append", default=[], metavar="USERNAME",
                    help="Extra chess.com username(s) to fetch (repeatable, no Elo filter applied)")
args = parser.parse_args()

HEADERS = {"User-Agent": "ProjectBrain/1.0 chess-research-noncommercial"}

# Top players with chess.com usernames
PLAYERS = [
    "MagnusCarlsen",
    "hikaru",
    "fabianocaruana",
    "alireza2003",
    "DanielNaroditsky",
    "GarryKasparov",
    "lachesisq",          # Leinier Dominguez
    "nihalsarin2002",
    "RaunakSadhwani",
    "penguingim1",        # Andrew Tang
]

# Selyrion's personal accounts — always fetched, no Elo filter
SELYRION_ACCOUNTS = ["selyrion", "Timobochester"]

# Extra users added via --user flag (personal accounts, no Elo filter)
EXTRA_USERS = SELYRION_ACCOUNTS + args.user


def get_archives(username: str) -> list[str]:
    try:
        r = requests.get(
            f"https://api.chess.com/pub/player/{username}/games/archives",
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        return r.json().get("archives", [])
    except Exception as e:
        print(f"  [!] {username} archives: {e}")
        return []


def fetch_month(url: str, min_elo: int = None) -> list[str]:
    """Fetch one month's games, return list of PGN strings."""
    if min_elo is None:
        min_elo = args.min_elo
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        pgns = []
        for g in data.get("games", []):
            pgn = g.get("pgn", "")
            if not pgn:
                continue
            if min_elo > 0:
                white_elo = g.get("white", {}).get("rating", 0)
                black_elo = g.get("black", {}).get("rating", 0)
                avg_elo = (white_elo + black_elo) / 2
                if avg_elo < min_elo:
                    continue
            # Inject accuracy tags if present (from premium review)
            accuracies = g.get("accuracies")
            if accuracies:
                wa = accuracies.get("white")
                ba = accuracies.get("black")
                inject = ""
                if wa is not None:
                    inject += f'[WhiteAccuracy "{wa:.1f}"]\n'
                if ba is not None:
                    inject += f'[BlackAccuracy "{ba:.1f}"]\n'
                if inject:
                    # Find end of header block (last line starting with '[')
                    lines = pgn.split('\n')
                    last_header = 0
                    for i, line in enumerate(lines):
                        if line.strip().startswith('['):
                            last_header = i
                    lines.insert(last_header + 1, inject.strip())
                    pgn = '\n'.join(lines)
            pgns.append(pgn)
        return pgns
    except Exception as e:
        print(f"  [!] fetch {url}: {e}")
        return []


def cutoff_url(months_back: int) -> str:
    """Return the URL prefix for the oldest month we want."""
    d = datetime.now() - relativedelta(months=months_back)
    return f"/{d.year}/{d.month:02d}"


def main():
    out_path = Path(args.out)
    cutoff = cutoff_url(args.months)

    all_pgns: list[str] = []
    seen_urls: set[str] = set()

    for username in PLAYERS:
        print(f"  {username}...", end=" ", flush=True)
        archives = get_archives(username)
        recent = [a for a in archives if cutoff <= a[-8:]]
        print(f"{len(recent)} recent months", flush=True)

        for arch_url in recent:
            if arch_url in seen_urls:
                continue
            seen_urls.add(arch_url)
            pgns = fetch_month(arch_url)
            all_pgns.extend(pgns)
            time.sleep(0.3)

    for username in EXTRA_USERS:
        print(f"  {username} [personal, no elo filter]...", end=" ", flush=True)
        archives = get_archives(username)
        recent = [a for a in archives if cutoff <= a[-8:]]
        print(f"{len(recent)} recent months", flush=True)

        for arch_url in recent:
            if arch_url in seen_urls:
                continue
            seen_urls.add(arch_url)
            pgns = fetch_month(arch_url, min_elo=0)
            all_pgns.extend(pgns)
            time.sleep(0.3)

    print(f"\n  Total PGNs collected: {len(all_pgns)}")

    # Write combined PGN file
    with open(out_path, "w") as f:
        for pgn in all_pgns:
            f.write(pgn.strip())
            f.write("\n\n")

    size = out_path.stat().st_size / 1024
    print(f"  Written: {out_path} ({size:.0f} KB)")
    print(f"\n  Ingest with:")
    print(f"  python3 chess_pgn_ingest.py --pgn {out_path}")


if __name__ == "__main__":
    main()
