#!/usr/bin/env python3
"""
openalex_dl.py — Download OpenAlex Works snapshot from S3 via manifest.

Uses the published manifest file (one request) instead of S3 listing.
Fully resumable — skips size-verified files.

Usage:
    python3 openalex_dl.py [--entity works] [--workers 6]
"""
import os
import sys
import json
import time
import threading
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

S3_BASE    = "https://openalex.s3.amazonaws.com"
ENTITY     = "works"
WORKERS    = 6

for arg in sys.argv[1:]:
    if arg.startswith("--entity="):
        ENTITY = arg.split("=", 1)[1]
    elif arg == "--entity":
        ENTITY = sys.argv[sys.argv.index(arg) + 1]
    elif arg.startswith("--workers="):
        WORKERS = int(arg.split("=", 1)[1])
    elif arg.startswith("--out="):
        pass   # handled below via env

DUMP_DIR   = Path(os.environ.get("OPENALEX_DUMP", f"/mnt/openalex/{ENTITY}"))
# Allow --out positional override
for arg in sys.argv[1:]:
    if not arg.startswith("--"):
        DUMP_DIR = Path(arg)

PROGRESS_F = DUMP_DIR.parent / "download_progress.json"
CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB

_progress_lock = threading.Lock()
_print_lock    = threading.Lock()


def _log(msg: str):
    with _print_lock:
        print(msg, flush=True)


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "openalex-dl/2.0 (research download)"})
    return s


def is_network_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(x in msg for x in ["name resolution", "connection reset", "connection aborted",
                                   "remote end closed", "timeout", "timed out", "network"])


def wait_for_network(label: str = ""):
    delay = 30
    while True:
        try:
            requests.get("https://8.8.8.8", timeout=5)
            return
        except Exception:
            pass
        try:
            import socket
            socket.setdefaulttimeout(5)
            socket.gethostbyname("openalex.s3.amazonaws.com")
            return
        except Exception:
            pass
        _log(f"  [network down{(' — ' + label) if label else ''}] waiting {delay}s...")
        time.sleep(delay)
        delay = min(delay * 2, 300)


def fetch_manifest() -> list[tuple[str, int]]:
    session = _make_session()
    url = f"{S3_BASE}/data/{ENTITY}/manifest"
    attempt = 0
    while True:
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            data = r.json()
            out = []
            for entry in data["entries"]:
                s3_url = entry["url"]
                key = s3_url.replace("s3://openalex/", "")
                size = entry["meta"]["content_length"]
                out.append((key, size))
            return out
        except Exception as e:
            attempt += 1
            if is_network_error(e):
                wait_for_network("manifest")
            else:
                wait = min(30 * attempt, 300)
                _log(f"  manifest retry {attempt} after {wait}s: {e}")
                time.sleep(wait)


def download_file(key: str, expected_size: int, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size == expected_size:
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    url     = f"{S3_BASE}/{key}"
    fname   = dest.name
    session = _make_session()   # each thread owns its session

    attempt = 0
    while True:
        attempt += 1
        try:
            with session.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(dest, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                        f.write(chunk)
            if dest.stat().st_size == expected_size:
                return True
            _log(f"    SIZE MISMATCH {fname} (attempt {attempt}) — retrying")
            dest.unlink(missing_ok=True)
            time.sleep(10)
        except Exception as e:
            _log(f"    ERROR {fname} (attempt {attempt}): {e}")
            dest.unlink(missing_ok=True)
            if is_network_error(e):
                wait_for_network(fname)
            else:
                time.sleep(min(30 * attempt, 300))


def load_progress() -> dict:
    if PROGRESS_F.exists():
        return json.loads(PROGRESS_F.read_text())
    return {"downloaded": [], "failed": []}


def save_progress(p: dict):
    PROGRESS_F.write_text(json.dumps(p, indent=2))


def main():
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    progress  = load_progress()
    done_keys = set(progress.get("downloaded", []))

    print(f"OpenAlex Download — entity: {ENTITY}  workers: {WORKERS}", flush=True)
    print(f"Dump dir  : {DUMP_DIR}", flush=True)
    print(f"Already done: {len(done_keys)} files\n", flush=True)

    print("Fetching manifest...", flush=True)
    all_files = fetch_manifest()
    total     = len(all_files)
    remaining = [(k, s) for k, s in all_files if k not in done_keys]
    print(f"Manifest: {total} files total, {len(remaining)} remaining\n", flush=True)

    files_done  = 0
    bytes_done  = 0
    counter_lock = threading.Lock()

    def _worker(args):
        key, size = args
        parts = key.split("/")
        date  = parts[-2].replace("updated_date=", "")
        fname = f"{date}__{parts[-1]}"
        dest  = DUMP_DIR / fname
        _log(f"  ↓ {fname}  ({size / 1e9:.2f} GB)")
        ok = download_file(key, size, dest)
        return key, size, ok

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_worker, item): item for item in remaining}
        for fut in as_completed(futures):
            key, size, ok = fut.result()
            with counter_lock:
                if ok:
                    progress["downloaded"].append(key)
                    files_done += 1
                    bytes_done += size
                else:
                    progress["failed"].append(key)
                with _progress_lock:
                    save_progress(progress)
                total_done = len(done_keys) + files_done
                pct = int(100 * total_done / total)
                status = "✓" if ok else "✗"
                _log(f"    {status}  {total_done}/{total} ({pct}%)  "
                     f"{bytes_done/1e9:.1f} GB this session")

    print(f"\nFinished. {files_done} files downloaded, {bytes_done/1e9:.1f} GB total.", flush=True)
    if progress.get("failed"):
        print(f"Failed: {len(progress['failed'])} files — re-run to retry.", flush=True)


if __name__ == "__main__":
    main()
