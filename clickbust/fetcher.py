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


def _extract_image_url(raw_html: str, base_url: str | None = None) -> str:
    """Extract the best thumbnail URL from article HTML.

    Precedence:
      1. og:image meta tag
      2. twitter:image meta tag
      3. First <img> with a large-looking src

    Returns empty string if nothing found.
    """
    import lxml.html
    try:
        tree = lxml.html.fromstring(raw_html)
    except Exception:
        return ""

    # 1. og:image
    for el in tree.xpath('//meta[@property="og:image"]'):
        content = el.get("content", "").strip()
        if content:
            return content

    # 2. twitter:image
    for el in tree.xpath('//meta[@name="twitter:image"] | //meta[@property="twitter:image"]'):
        content = el.get("content", "").strip()
        if content:
            return content

    # 3. First img with a reasonable src (skip tracking pixels / icons)
    for el in tree.xpath('//img[@src]'):
        src = el.get("src", "").strip()
        if not src or src.startswith("data:"):
            continue
        width = el.get("width")
        # Skip tiny images
        if width and width.isdigit() and int(width) < 100:
            continue
        # Make relative URLs absolute
        if src.startswith("/") and base_url:
            src = base_url.rstrip("/") + src
        elif src.startswith("//"):
            src = "https:" + src
        if src.startswith("http"):
            return src

    return ""


def extract_content(url: str, timeout: float = 30) -> tuple[str, str, str]:
    """Fetch article URL and extract readable content + summary + thumbnail.

    Returns:
        (full_text, summary, image_url) where summary is the first ~300 chars
        of clean text and image_url is the article thumbnail.
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

    raw_html = resp.text

    # Extract image from raw HTML before readability strips it
    image_url = _extract_image_url(raw_html)

    doc = Document(raw_html)
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

    return full_text, summary, image_url


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