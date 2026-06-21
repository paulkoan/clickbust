"""RSS feed fetching and article content extraction."""

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import feedparser
import httpx
from lxml.html.clean import Cleaner
from readability import Document

from .models import Article, SiteConfig

log = logging.getLogger(__name__)

# Lightweight HTML cleaner — strips scripts, styles, etc.
_cleaner = Cleaner(
    scripts=True,
    javascript=True,
    comments=True,
    style=True,
    links=True,
    meta=False,
    page_structure=False,
    processing_instructions=True,
    embedded=True,
    frames=True,
    forms=True,
    annoying_tags=True,
    kill_tags=["nav", "header", "footer", "aside"],
)


def _slugify(title: str, url: str) -> str:
    """Create a unique, URL-safe identifier from title + url."""
    raw = (title + url).encode()
    h = hashlib.sha256(raw).hexdigest()[:12]
    # Make a readable slug from the title
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")[:60]
    return f"{slug}-{h}" if slug else h


def _parse_date(entry: dict) -> Optional[datetime]:
    """Try to extract a datetime from a feed entry."""
    for attr in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, attr, None)
        if tp:
            try:
                return datetime(*tp[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def fetch_rss(url: str, timeout: float = 30) -> list[dict]:
    """Fetch and parse an RSS feed. Returns list of raw entry dicts."""
    log.info("Fetching RSS: %s", url)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, headers={"User-Agent": "Clickbust/0.1"})
        resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    if feed.bozo and not feed.entries:
        log.warning("Feed parse error for %s: %s", url, feed.bozo_exception)
    return feed.entries


def extract_content(url: str, timeout: float = 30) -> tuple[str, str]:
    """Fetch article URL and extract readable content + summary.

    Returns:
        (full_text, summary) where summary is the first ~300 chars of clean text.
    """
    log.info("Extracting content: %s", url)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        )
        resp.raise_for_status()

    doc = Document(resp.text)
    html = doc.summary()

    # Clean up the HTML
    cleaned = _cleaner.clean_html(html)

    # Extract text from cleaned HTML using lxml
    import lxml.html

    tree = lxml.html.fromstring(cleaned)
    text = tree.text_content().strip()

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # Truncate to first 5000 chars for LLM processing
    full_text = text[:5000]

    # Summary is first ~300 chars
    summary = text[:300]
    # Try to end at a sentence boundary
    if len(text) > 300:
        last_period = summary.rfind(".")
        if last_period > 100:
            summary = summary[: last_period + 1]

    return full_text, summary


def fetch_all_sites(sites: list[SiteConfig], max_per_site: int = 20) -> list[Article]:
    """Fetch all configured sites and return list of Articles."""
    articles: list[Article] = []

    for site in sites:
        if not site.enabled:
            log.info("Skipping disabled site: %s", site.name)
            continue

        try:
            entries = fetch_rss(site.rss_url)
        except Exception as e:
            log.error("Failed to fetch RSS for %s: %s", site.name, e)
            continue

        for entry in entries[:max_per_site]:
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()
            if not title or not link:
                continue

            art = Article(
                title=title,
                url=link,
                site_name=site.name,
                published_date=_parse_date(entry),
                article_id=_slugify(title, link),
                fetched_at=datetime.now(timezone.utc),
            )
            articles.append(art)

        log.info("Fetched %d articles from %s", len(entries[:max_per_site]), site.name)

    return articles