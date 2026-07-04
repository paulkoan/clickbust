"""Track seen articles to avoid redundant LLM calls and content extraction."""

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

CACHE_FILENAME = "seen_articles.json"
MAX_AGE_DAYS = 7  # Prune entries not seen in this long


def load_cache(output_dir: str) -> dict:
    """Load the seen-articles cache from disk."""
    path = os.path.join(output_dir, CACHE_FILENAME)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load seen cache: %s — starting fresh", e)
    return {}


def save_cache(output_dir: str, cache: dict) -> None:
    """Save the seen-articles cache to disk."""
    path = os.path.join(output_dir, CACHE_FILENAME)
    try:
        with open(path, "w") as f:
            json.dump(cache, f, indent=2)
        log.info("Seen cache saved (%d entries, %d cached results reused)",
                  len(cache),
                  sum(1 for e in cache.values() if e.get("is_cached", False)))
    except OSError as e:
        log.warning("Failed to save seen cache: %s", e)


def _headline_hash(headline: str) -> str:
    return hashlib.sha256(headline.encode()).hexdigest()[:16]


def is_unchanged(cache: dict, url: str, headline: str) -> bool:
    """Check if article URL is in cache and its headline hasn't changed."""
    entry = cache.get(url)
    if not entry:
        return False
    return entry.get("headline_hash") == _headline_hash(headline)


def get_cached_result(cache: dict, url: str) -> dict | None:
    """Get cached LLM result and summary for a previously-seen article."""
    entry = cache.get(url)
    if entry:
        return {
            "is_clickbait": entry.get("is_clickbait", False),
            "rewritten_title": entry.get("rewritten_title"),
            "summary": entry.get("summary", ""),
            "image_url": entry.get("image_url", ""),
        }
    return None


def mark_processed(
    cache: dict,
    url: str,
    headline: str,
    is_clickbait: bool,
    rewritten_title: str | None,
    summary: str = "",
    image_url: str = "",
    is_cached: bool = False,
) -> None:
    """Store or update an article's result in the cache.

    Args:
        is_cached: True when the result came from a previous run (reused).
                    False when freshly processed in this run.
    """
    cache[url] = {
        "headline_hash": _headline_hash(headline),
        "is_clickbait": is_clickbait,
        "rewritten_title": rewritten_title,
        "summary": summary,
        "image_url": image_url,
        "is_cached": is_cached,
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }


def prune_stale(cache: dict) -> dict:
    """Remove entries that haven't been seen in MAX_AGE_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    cutoff_str = cutoff.isoformat()
    stale = [url for url, entry in cache.items()
             if entry.get("last_seen", "") < cutoff_str]
    for url in stale:
        del cache[url]
    if stale:
        log.info("Pruned %d stale entries from seen cache", len(stale))
    return cache