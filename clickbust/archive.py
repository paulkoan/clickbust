"""Persistent article archive — stores metadata so historic articles survive between runs.

The archive holds serialized article data (title, url, summary, rewritten title, etc.)
keyed by URL. On each run we merge fresh RSS articles with the archive, keeping
everything. This allows the site to grow without re-fetching or re-LLM-ing old articles.

Token cost: ZERO. Only metadata is stored and re-rendered.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from .models import Article

log = logging.getLogger(__name__)

ARCHIVE_FILENAME = "articles.json"

# Maximum articles to keep in the archive
MAX_ARCHIVED = 500


def archive_path(output_dir: str) -> str:
    return os.path.join(output_dir, ARCHIVE_FILENAME)


def load_archive(output_dir: str) -> dict:
    """Load the persistent article archive from disk."""
    path = archive_path(output_dir)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load archive: %s — starting fresh", e)
    return {}


def save_archive(output_dir: str, archive: dict) -> None:
    """Save the archive to disk."""
    path = archive_path(output_dir)
    try:
        with open(path, "w") as f:
            json.dump(archive, f, indent=2)
        log.info("Archive saved — %d articles", len(archive))
    except OSError as e:
        log.warning("Failed to save archive: %s", e)


def article_to_entry(article: Article) -> dict:
    """Serialize an Article to a storage dict."""
    entry = {
        "title": article.title,
        "url": article.url,
        "site_name": article.site_name,
        "summary": article.summary,
        "image_url": article.image_url,
        "rewritten_title": article.rewritten_title,
        "is_clickbait": article.is_clickbait,
        "article_id": article.article_id,
    }
    if article.published_date:
        entry["published_date"] = article.published_date.isoformat()
    return entry


def entry_to_article(entry: dict) -> Article:
    """Restore an Article from a storage dict."""
    pd = None
    if entry.get("published_date"):
        try:
            pd = datetime.fromisoformat(entry["published_date"])
        except (ValueError, TypeError):
            pass
    return Article(
        title=entry.get("title", ""),
        url=entry.get("url", ""),
        site_name=entry.get("site_name", ""),
        summary=entry.get("summary", ""),
        image_url=entry.get("image_url", ""),
        rewritten_title=entry.get("rewritten_title"),
        is_clickbait=entry.get("is_clickbait", False),
        article_id=entry.get("article_id", ""),
        published_date=pd,
    )


def merge_articles(
    archive: dict,
    fresh_articles: list[Article],
    cached_articles: list[Article],
) -> tuple[dict, list[Article]]:
    """Merge fresh + cached articles into the archive.

    Args:
        archive: Existing archive dict (url -> entry).
        fresh_articles: Articles freshly processed this run (have LLM results).
        cached_articles: Articles re-used from seen cache this run.

    Returns:
        (updated_archive, full_article_list_sorted_by_date)
    """
    # Upsert fresh articles
    for art in fresh_articles:
        archive[art.url] = article_to_entry(art)

    # Upsert cached articles
    for art in cached_articles:
        archive[art.url] = article_to_entry(art)

    # Prune to max size
    if len(archive) > MAX_ARCHIVED:
        # Sort by published date descending, keep newest
        sorted_urls = sorted(
            archive.keys(),
            key=lambda u: archive[u].get("published_date", ""),
            reverse=True,
        )
        keep = set(sorted_urls[:MAX_ARCHIVED])
        archive = {u: archive[u] for u in sorted_urls if u in keep}

    # Convert back to article list sorted by date
    articles = []
    for url, entry in archive.items():
        art = entry_to_article(entry)
        art.url = url  # ensure URL is set
        articles.append(art)

    articles.sort(
        key=lambda a: a.published_date or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    return archive, articles