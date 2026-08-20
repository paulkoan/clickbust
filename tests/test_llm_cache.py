"""Tests for the LLM response cache."""

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone

from clickbust.llm_cache import LLMCache, CACHE_FILENAME


def test_set_and_get():
    """Basic round-trip: store then retrieve."""
    with tempfile.TemporaryDirectory() as d:
        cache = LLMCache(d, ttl_seconds=3600)
        cache.set("sys1", "user1", "model-x", {"result": "hello"})
        result = cache.get("sys1", "user1", "model-x")
        assert result == {"result": "hello"}, f"Expected dict, got {result}"


def test_miss_on_different_prompt():
    """Different prompts produce different keys -> miss."""
    with tempfile.TemporaryDirectory() as d:
        cache = LLMCache(d, ttl_seconds=3600)
        cache.set("sys1", "user1", "model-x", {"result": "a"})
        result = cache.get("sys2", "user1", "model-x")
        assert result is None, "Should miss on different system prompt"


def test_miss_on_different_model():
    """Different model in the key -> miss."""
    with tempfile.TemporaryDirectory() as d:
        cache = LLMCache(d, ttl_seconds=3600)
        cache.set("sys1", "user1", "model-a", {"result": "a"})
        result = cache.get("sys1", "user1", "model-b")
        assert result is None, "Should miss on different model"


def test_persistence():
    """Cache survives reload from disk."""
    with tempfile.TemporaryDirectory() as d:
        cache = LLMCache(d, ttl_seconds=3600)
        cache.set("sys1", "user1", "m", {"k": "v"})

        # Re-create the cache instance (re-reads from disk)
        cache2 = LLMCache(d, ttl_seconds=3600)
        result = cache2.get("sys1", "user1", "m")
        assert result == {"k": "v"}, "Should persist across reloads"


def test_ttl_expiry():
    """Expired entries return None and are pruned on get."""
    with tempfile.TemporaryDirectory() as d:
        # Zero-second TTL means entries expire immediately
        cache = LLMCache(d, ttl_seconds=0)
        cache.set("sys1", "user1", "m", {"k": "v"})

        result = cache.get("sys1", "user1", "m")
        assert result is None, "Expired entry should return None"

        # Verify entry was pruned from the dict
        assert len(cache._cache) == 0, "Expired entry should be removed"


def test_prune():
    """prune() removes expired entries and returns count."""
    with tempfile.TemporaryDirectory() as d:
        cache = LLMCache(d, ttl_seconds=3600)

        # Add a fresh entry
        cache.set("sys1", "user1", "m", {"k": "v"})

        # Manually add an expired entry
        expired_key = cache._key("sys_exp", "user_exp", "m")
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        cache._cache[expired_key] = {
            "response": {"x": "y"},
            "cached_at": past,
            "expires_at": past,
            "model": "m",
        }

        pruned = cache.prune()
        assert pruned >= 1, "Should prune expired entry"
        assert expired_key not in cache._cache


def test_thread_safety():
    """Concurrent access doesn't corrupt the cache."""
    with tempfile.TemporaryDirectory() as d:
        cache = LLMCache(d, ttl_seconds=3600)

        errors = []

        def writer():
            try:
                for i in range(50):
                    cache.set(f"sys_{i}", f"user_{i}", "m", {"i": i})
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(50):
                    cache.get(f"sys_{i}", f"user_{i}", "m")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        threads += [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety errors: {errors}"
        assert len(cache._cache) == 50, f"Expected 50 entries, got {len(cache._cache)}"


def test_corruption_graceful():
    """Corrupt cache file logs warning and starts fresh."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, CACHE_FILENAME)
        with open(path, "w") as f:
            f.write("not json")

        # Loading corrupt file should not crash
        cache = LLMCache(d, ttl_seconds=3600)
        assert len(cache._cache) == 0, "Should start fresh on corruption"


def test_save_creates_dir():
    """Cache saves work even if the output dir doesn't exist yet."""
    with tempfile.TemporaryDirectory() as d:
        subdir = os.path.join(d, "nonexistent", "nested")
        cache = LLMCache(subdir, ttl_seconds=3600)
        cache.set("s", "u", "m", {"k": "v"})
        assert os.path.exists(os.path.join(subdir, CACHE_FILENAME))