"""TTL-based file cache for LLM API responses keyed by prompt content hash.

This cache sits ABOVE the seen_cache layer. While seen_cache avoids LLM
calls for articles whose URL+headline hasn't changed, this cache avoids
LLM calls for prompts whose content (system + user) hasn't changed.

Use cases this catches that seen_cache doesn't:
  1. Syndicated articles — same story at different URLs → same prompt → cache hit
  2. Repeated runs within the TTL window (cron + manual, or error recovery)
  3. Identical daily-note prompts across re-runs
"""

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

CACHE_FILENAME = "llm_cache.json"
DEFAULT_TTL_SECONDS = 3600  # 1 hour


class LLMCache:
    """Content-addressed, thread-safe, TTL-based file cache for LLM responses.

    Keyed by SHA256 of (model || system_prompt || user_prompt).
    TTL is applied lazily — entries are checked on get() and pruned on save().
    """

    def __init__(self, cache_dir: str, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.cache_dir = cache_dir
        self.ttl = timedelta(seconds=ttl_seconds)
        self._lock = threading.Lock()
        self._cache = self._load()

    def _cache_path(self) -> str:
        return os.path.join(self.cache_dir, CACHE_FILENAME)

    def _load(self) -> dict:
        path = self._cache_path()
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Failed to load LLM cache: %s — starting fresh", e)
        return {}

    @staticmethod
    def _key(system_prompt: str, user_prompt: str, model: str) -> str:
        raw = f"{model}||{system_prompt}||{user_prompt}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, system_prompt: str, user_prompt: str, model: str) -> dict | None:
        """Return cached response dict, or None if missing/expired.

        The response dict is the parsed JSON from the LLM (e.g. the
        'choices[0].message.content' parsed into Python objects).
        """
        key = self._key(system_prompt, user_prompt, model)
        with self._lock:
            entry = self._cache.get(key)
        if not entry:
            return None

        # Lazy TTL check
        expires_at = entry.get("expires_at", "")
        if expires_at < datetime.now(timezone.utc).isoformat():
            with self._lock:
                self._cache.pop(key, None)
                self._save_locked()
            log.info("LLM cache entry expired, key=%s...", key[:12])
            return None

        age = (datetime.now(timezone.utc) - datetime.fromisoformat(entry["cached_at"])).total_seconds()
        log.info("LLM cache HIT, key=%s..., age=%.1fs", key[:12], age)
        return entry.get("response")

    def set(self, system_prompt: str, user_prompt: str, model: str, response: dict) -> None:
        """Store a response in the cache with TTL."""
        key = self._key(system_prompt, user_prompt, model)
        expires_at = datetime.now(timezone.utc) + self.ttl
        with self._lock:
            self._cache[key] = {
                "response": response,
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": expires_at.isoformat(),
                "model": model,
            }
            self._save_locked()
        log.info("LLM cache STORED, key=%s..., TTL=%.0fs", key[:12], self.ttl.total_seconds())

    def _save_locked(self) -> None:
        """Save cache to disk. Caller MUST hold self._lock."""
        path = self._cache_path()
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(path, "w") as f:
                json.dump(self._cache, f, indent=2)
        except OSError as e:
            log.warning("Failed to save LLM cache: %s", e)

    def prune(self) -> int:
        """Remove all expired entries. Returns count pruned."""
        now = datetime.now(timezone.utc).isoformat()
        stale = []
        with self._lock:
            stale = [k for k, v in self._cache.items()
                     if v.get("expires_at", "") < now]
            for k in stale:
                del self._cache[k]
            if stale:
                self._save_locked()
        if stale:
            log.info("LLM cache pruned %d stale entries", len(stale))
        return len(stale)
