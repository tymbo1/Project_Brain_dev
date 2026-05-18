#!/usr/bin/env python3
"""
ollama_guard.py — Ollama readiness probe and load guard.

Prevents Ollama exhaustion by gating LLM-heavy tasks behind a latency check.
Designed to slot into the future SSRE parallel dispatch layer as the resource
arbiter for GPU/CPU model slots.

Usage (simple):
    from ollama_guard import wait_for_ready
    wait_for_ready()        # blocks until Ollama is responsive

Usage (future SSRE orchestration):
    guard = OllamaGuard(slots=2)   # 2 parallel model slots
    with guard.slot():
        articulate(...)
"""

import time
import subprocess
import requests
import threading
from contextlib import contextmanager

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3:8b"

# Probe request — minimal, just measures round-trip latency
_PROBE_PAYLOAD = {
    "model":   OLLAMA_MODEL,
    "prompt":  "hi",
    "stream":  False,
    "options": {"num_predict": 3},
}


def probe_latency(timeout: float = 8.0) -> float | None:
    """Fire a cheap probe. Returns latency in seconds, or None on failure."""
    try:
        t0 = time.time()
        r = requests.post(OLLAMA_URL, json=_PROBE_PAYLOAD, timeout=timeout)
        r.raise_for_status()
        return time.time() - t0
    except Exception:
        return None


def wait_for_ready(
    threshold_s: float = 4.0,
    poll_interval: float = 10.0,
    max_wait: float = 300.0,
    label: str = "",
) -> bool:
    """
    Block until Ollama responds within threshold_s.

    Returns True when ready, False if max_wait exceeded.
    Call before any batch of articulator/ingest LLM requests.
    """
    tag = f"[{label}] " if label else ""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        lat = probe_latency()
        if lat is not None and lat < threshold_s:
            return True
        remaining = int(deadline - time.time())
        print(f"  {tag}Ollama busy (lat={lat:.1f}s), waiting... ({remaining}s left)")
        time.sleep(poll_interval)
    print(f"  {tag}Ollama not ready after {max_wait:.0f}s — proceeding anyway")
    return False


class OllamaGuard:
    """
    Slot-based concurrency guard for parallel SSRE model dispatch.

    Limits simultaneous Ollama requests to `slots` (default 1 = sequential).
    When the SSRE orchestration layer ships, raise slots to match GPU/CPU capacity.

    Usage:
        guard = OllamaGuard(slots=1)
        with guard.slot():
            result = articulate(...)
    """

    def __init__(self, slots: int = 1):
        self._sem = threading.Semaphore(slots)
        self.slots = slots

    @contextmanager
    def slot(self):
        self._sem.acquire()
        try:
            yield
        finally:
            self._sem.release()

    def wait_ready(self, **kwargs) -> bool:
        return wait_for_ready(**kwargs)


def restart_ollama(wait_s: float = 15.0) -> bool:
    """
    Restart the Ollama systemd service and wait for readiness.

    Requires passwordless sudo for this command — add once via:
        sudo visudo -f /etc/sudoers.d/ollama-restart
    and add the line:
        <your-user> ALL=(ALL) NOPASSWD: /bin/systemctl restart ollama

    Returns True if Ollama is responsive after restart.
    """
    print("  [ollama_guard] Restarting Ollama service...")
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "ollama"],
            check=True, timeout=30, capture_output=True
        )
        print(f"  [ollama_guard] Restart issued, waiting {wait_s:.0f}s for warmup...")
        time.sleep(wait_s)
        return wait_for_ready(threshold_s=6.0, poll_interval=5.0, max_wait=120.0, label="post-restart")
    except subprocess.CalledProcessError as e:
        print(f"  [ollama_guard] Restart failed: {e.stderr.decode().strip()}")
        return False
    except Exception as e:
        print(f"  [ollama_guard] Restart error: {e}")
        return False


# Degradation threshold: latency above this on a cheap probe → suspect exhaustion
DEGRADED_THRESHOLD_S = 8.0

def ingest_checkpoint(
    concept_index: int,
    interval: int = 30,
    threshold_s: float = DEGRADED_THRESHOLD_S,
    label: str = "",
) -> None:
    """
    Call inside an ingest loop every concept. Checks Ollama health every
    `interval` concepts and restarts if latency exceeds threshold_s.

    Usage in ingest script main loop:
        for i, (concept, anchor_id) in enumerate(ANCHORS.items()):
            ingest_checkpoint(i, interval=30)
            ...
    """
    if concept_index == 0 or concept_index % interval != 0:
        return
    tag = f"[{label}] " if label else ""
    print(f"\n  {tag}--- checkpoint at concept {concept_index} ---")
    lat = probe_latency(timeout=10.0)
    if lat is None:
        print(f"  {tag}Ollama unresponsive — restarting...")
        restart_ollama()
    elif lat > threshold_s:
        print(f"  {tag}Ollama degraded (lat={lat:.1f}s > {threshold_s:.0f}s) — restarting...")
        restart_ollama()
    else:
        print(f"  {tag}Ollama healthy (lat={lat:.2f}s) — continuing.")


# Module-level default guard (1 slot — sequential, safe for current architecture)
default_guard = OllamaGuard(slots=1)
