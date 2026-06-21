"""LLM-based headline analysis and rewriting."""

import logging
import os
from pathlib import Path
from typing import Optional

import httpx
import yaml
from dotenv import load_dotenv

from .models import Article, LLMConfig

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a headline analyst. Your job is to detect clickbait headlines and replace them.

A headline is **clickbait** if it:
- Withholds key information the reader needs ("You won't believe what happened next")
- Is overly vague or generic ("This amazing thing changed everything")
- Makes an exaggerated claim unsupported by the article
- Uses curiosity gaps to force clicks ("The one trick [experts/scientists] hate")
- Is misleading about what the content actually covers
- Uses ALL CAPS or excessive sensationalism for mundane content
- **Medium bait**: Implies it's about the mainline TV/film franchise (Bond, Star Trek, MCU)
  but is actually about a different medium — audiobook, comic, animated series, podcast,
  video game, or stage show. If the article covers a spin-off or adaptation in a
  different medium, the headline MUST name that medium.
  (e.g. "New Bond actor confirmed" → it's an audiobook narrator, not the next movie Bond)

A headline is **NOT clickbait** if it:
- Simply describes what the article is about in specific terms
- Names the specific subject, show, person, or event
- Gives the reader enough context to decide if they're interested
- Clearly states the medium/format (comic, TV series, movie, game, etc.)

**Critical rule about named subjects:**
- If the article is about a **released/announced work** (a movie, TV show, game, album, book, etc.), the headline MUST name that work's title. "Gore Verbinski's new R-rated sci-fi thriller" is clickbait if you don't name the movie. "A new Arnold Schwarzenegger movie" is clickbait if the movie has a name.
- If the article is about a **cancelled, rumoured, or hypothetical project** where no official title ever existed, a descriptive label is acceptable ("Arnold Schwarzenegger's cancelled swashbuckling movie" — if the project never had a real title this is fine).
- **When in doubt, err on the side of naming the specific work title.** If the article names it and the headline omits it, that's clickbait.

For each headline you analyze, output ONLY valid JSON with two fields:
{
  "is_clickbait": true/false,
  "new_headline": "The improved headline here, or null if not clickbait"
}

The new headline must be:
- Informative and specific (name the subject: show, person, event)
- Under 120 characters
- Honest to the article's content
- Not sensational or hyperbolic
- **Name the medium** if it differs from what the title implies (e.g. "comic", "audiobook", "animated")"""


def _load_api_key(config: LLMConfig) -> str:
    """Resolve API key, supporting env var references like ${VAR_NAME}.

    Also attempts to load from a .env file in the project root.
    """
    key = config.api_key

    # If it's an env var reference, try to resolve it
    if key.startswith("${") and key.endswith("}"):
        env_var = key[2:-1]
        # Try loading from .env first
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)
        key = os.environ.get(env_var, "")

    return key


def _build_prompt(article: Article) -> str:
    """Build the LLM prompt for a single article."""
    return f"""Headline: {article.title}

Article content (first part):
{article.content_text[:4000]}

Analyze the headline against the actual content and output the JSON result."""


def rewrite_headline(article: Article, config: LLMConfig) -> tuple[bool, Optional[str]]:
    """Send article to LLM and determine if headline is clickbait + get replacement.

    Returns:
        (is_clickbait, new_title_or_None)
    """
    api_key = _load_api_key(config)
    if not api_key:
        log.warning("No API key configured for LLM, skipping rewriting")
        return False, None

    prompt = _build_prompt(article)

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 256,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    log.info("Rewriting headline for: %s", article.title[:60])

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(config.endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()

        content = result["choices"][0]["message"]["content"].strip()

        # Extract JSON from the response (handle markdown code fences)
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()

        import json

        data = json.loads(content)
        is_clickbait = data.get("is_clickbait", False)
        new_headline = data.get("new_headline")

        if is_clickbait and new_headline:
            log.info("  Clickbait detected! New: %s", new_headline[:80])
        elif is_clickbait:
            log.info("  Clickbait detected but no replacement generated")
        else:
            log.info("  Not clickbait — keeping original")

        return is_clickbait, new_headline

    except Exception as e:
        log.error("  LLM call failed for '%s': %s", article.title[:40], e)
        return False, None


BATCH_SYSTEM_PROMPT = SYSTEM_PROMPT + """

When analyzing multiple articles, output ONLY a JSON array, one entry per article,
in the same order as the input articles. Example:
[
  {"is_clickbait": true, "new_headline": "Informative headline here"},
  {"is_clickbait": false, "new_headline": null},
  ...
]
The array length must match the number of articles provided."""


def _batch_prompt(articles: list[Article]) -> str:
    """Build a batch prompt that lists multiple articles for a single API call."""
    parts = ["Analyze each of the following headlines against their article content."]
    for i, art in enumerate(articles, 1):
        content_preview = (art.content_text or art.summary or "")[:3000]
        parts.append(
            f"\n--- Article {i} ---\n"
            f"Headline: {art.title}\n"
            f"Content: {content_preview}"
        )
    parts.append("\n\nOutput ONLY a JSON array matching the article order.")
    return "\n".join(parts)


def _rewrite_batch(articles: list[Article], config: LLMConfig) -> list[Article]:
    """Send a batch of articles in a single LLM call and parse results.

    Falls back to individual calls if the batch call fails.
    """
    api_key = _load_api_key(config)
    if not api_key:
        for art in articles:
            art.is_clickbait = False
            art.rewritten_title = None
        return articles

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": _batch_prompt(articles)},
        ],
        "temperature": 0.3,
        "max_tokens": 512 * len(articles),  # Scale tokens with batch size
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    log.info("Batch rewriting %d headlines...", len(articles))

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(config.endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()

        content = result["choices"][0]["message"]["content"].strip()

        # Handle markdown code fences
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()

        import json
        data = json.loads(content)

        if not isinstance(data, list):
            log.warning("Batch response was not a list — falling back to individual calls")
            raise ValueError("Expected JSON array")

        for i, art in enumerate(articles):
            if i < len(data):
                entry = data[i]
                art.is_clickbait = entry.get("is_clickbait", False)
                art.rewritten_title = entry.get("new_headline")
                if art.is_clickbait and art.rewritten_title:
                    log.info("  [%d/%d] ✅ Clickbait → \"%s\"", i + 1, len(articles), art.rewritten_title[:60])
                else:
                    log.info("  [%d/%d] 🟢 Not clickbait — keeping \"%s\"", i + 1, len(articles), art.title[:50])
            else:
                log.warning("  [%d] Missing response entry — marking as not clickbait", i)
                art.is_clickbait = False
                art.rewritten_title = None

        return articles

    except Exception as e:
        log.warning("Batch LLM call failed (%s) — falling back to individual calls", e)
        for art in articles:
            _, _ = rewrite_headline(art, config)
        return articles


def rewrite_all(articles: list[Article], config: LLMConfig, batch_size: int = 10) -> list[Article]:
    """Rewrite headlines for all articles that have content.

    Uses batched API calls by default to reduce HTTP overhead and token usage.
    Falls back to individual calls on failure.

    Args:
        articles: List of articles to process (must have content_text).
        config: LLM configuration.
        batch_size: Number of articles per batch call (default: 10).
    """
    to_rewrite = [a for a in articles if a.content_text]
    skipped = [a for a in articles if not a.content_text]

    for i in range(0, len(to_rewrite), batch_size):
        batch = to_rewrite[i:i + batch_size]
        _rewrite_batch(batch, config)

    return to_rewrite + skipped