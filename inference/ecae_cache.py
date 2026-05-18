"""
ecae_cache.py — Ephemeral Cognitive Activation Engine cache.

In-memory LRU cache with TTL. Cache key includes domain epochs so that
HITL-approved graph mutations automatically invalidate stale entries.

Usage:
    from inference.ecae_cache import get_or_run

    result = get_or_run(engine, query, max_chains=15)
"""

import hashlib
import sqlite3
import time
from collections import OrderedDict

CMS_PATH  = __import__('os').path.expanduser("~/resonance_v11.db")
TTL       = 300    # seconds — matches Anthropic prompt cache window
MAX_ITEMS = 512    # LRU eviction after this many entries


class _Cache:
    def __init__(self):
        self._store: OrderedDict[str, tuple[dict, float]] = OrderedDict()

    @staticmethod
    def _normalize(query: str) -> str:
        """Strip question framing to get the core concept term."""
        q = query.lower().strip().rstrip("?. ")
        for prefix in ("what is ", "what are ", "what was ", "who is ", "who was ",
                       "tell me about ", "explain ", "define ", "describe ",
                       "how does ", "why does ", "how do ", "why do "):
            if q.startswith(prefix):
                q = q[len(prefix):].strip()
                break
        return q

    def _key(self, query: str, max_chains: int, domain_override: set | None) -> str:
        epoch_sig  = _get_epoch_sig()
        domain_sig = ",".join(sorted(domain_override)) if domain_override else ""
        norm = self._normalize(query)
        raw = f"{norm}:{max_chains}:{domain_sig}:{epoch_sig}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get(self, query: str, max_chains: int, domain_override: set | None) -> dict | None:
        key = self._key(query, max_chains, domain_override)
        if key not in self._store:
            return None
        result, ts = self._store[key]
        if time.time() - ts > TTL:
            del self._store[key]
            return None
        self._store.move_to_end(key)  # LRU refresh
        return result

    def put(self, query: str, max_chains: int, domain_override: set | None, result: dict):
        key = self._key(query, max_chains, domain_override)
        self._store[key] = (result, time.time())
        self._store.move_to_end(key)
        if len(self._store) > MAX_ITEMS:
            self._store.popitem(last=False)  # evict oldest

    def invalidate_all(self):
        self._store.clear()


_cache = _Cache()


def _get_epoch_sig() -> str:
    """
    Returns a signature of current domain epochs from cms_domain_epochs table.
    Falls back to empty string if the table doesn't exist yet.
    When HITL commits update the epoch table, cache entries automatically
    become stale on next key computation.
    """
    try:
        con = sqlite3.connect(f"file:{CMS_PATH}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT domain, epoch FROM cms_domain_epochs ORDER BY domain"
        ).fetchall()
        con.close()
        return "|".join(f"{d}:{e}" for d, e in rows)
    except Exception:
        return ""


def get_or_run(engine, query: str, max_chains: int = 15,
               domain_override: set | None = None) -> dict:
    """
    Return cached activation result if available and fresh.
    Otherwise run engine.infer() and cache the result.
    """
    hit = _cache.get(query, max_chains, domain_override)
    if hit is not None:
        hit["_cache"] = "hit"
        return hit

    result = engine.infer(query, max_chains=max_chains, domain_override=domain_override)
    result["_cache"] = "miss"
    _cache.put(query, max_chains, domain_override, result)
    return result


def invalidate():
    """Force-clear all cache entries (e.g. after bulk HITL commit)."""
    _cache.invalidate_all()
