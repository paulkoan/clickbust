"""LLM-based headline analysis and rewriting."""

import logging
import os
from pathlib import Path
from typing import Optional

import httpx
import yaml
from dotenv import load_dotenv

from .llm_cache import LLMCache
from .models import Article, LLMConfig

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a headline analyst. Detect clickbait headlines and rewrite them to be specific and informative.

**Clickbait** means the headline:

1. **Withholds the named subject.** If the article names the work, person, or event, the headline must too. Covers:
   - Description bait: describes a work in rich detail but withholds the title. "Keanu Reeves' Adaptation Of Philip K. Dick Sci-Fi Novel" is clickbait when the movie is "A Scanner Darkly"
   - Platform bait: names only platform+genre without the actual title. "Apple TV's Hit Detective Show" is clickbait when the show is called Sugar. "Netflix's New Thriller" is clickbait when the show is "I Will Find You."
   - **Reference bait: uses a more famous franchise/character as a stand-in for the actual subject. "Reacher Replacement" when the show is "Neagley", "Star Wars-style epic" without naming the actual movie, "the next Game of Thrones" without naming the actual show.** If the article body names the specific work and the headline only describes it via reference to something else, it's clickbait.
   - **Superlative bait: pairs a ranking/milestone/view-count with a vague description of the subject instead of naming it. "Climbs Most-Watched List With 106.7M Views" without naming the show is clickbait. If the article body names the title and the headline doesn't, it's clickbait.**
   - Person archetype bait: "90s Action Icon" or "Star Trek's Most Beloved Character" instead of naming them
   - **Actor-as-bait: names a famous actor + platform (or actor + vague genre) but not the actual show. "Jeffrey Dean Morgan's New Prime Video Series" without naming "Sterling Point" is clickbait. "Julia Roberts' Chilling Netflix Thriller" without naming the movie is clickbait. If the body names the title and the headline only names the actor, it's clickbait.**
   - **RT-score bait: uses a Rotten Tomatoes score (96%, 100%, etc.) as the hook while withholding the title. "HBO's 96% RT Steamy Surprise Hit" is clickbait when the show is "Heated Rivalry."**
   - Exception: cancelled/rumoured projects with no official title

2. **Medium bait.** Implies mainline franchise (Bond, Star Trek, MCU) but covers a different medium (audiobook, comic, game). Must name the medium.

3. **Curiosity gap / vague.** "You won't believe", "This one trick", "Changed everything". Withholds what the reader needs.

4. **Exaggerated / ALL CAPS.** Hyperbolic claims for mundane content.

5. **Hook-word bait.** "quietly", "secretly", "finally", "officially" as the only informative content. If removing the hook leaves the headline vague, it's clickbait.

6. **Fan-reaction bait.** "Fans Are Divided/React/Furious" without saying the actual reaction.

7. **Misleading.** Headline doesn't match what the article covers.

**Not clickbait** if it names the work, gives enough context, and describes the article. Listicles/roundups are fine. Using "quietly" while still being specific is fine.

Output JSON:
{"is_clickbait": true/false, "new_headline": "..."}

New headline: under 120 chars, informative, names the subject, no sensationalism, names the medium if it differs from the implied one."""


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

Content:
{article.content_text[:2500]}

Analyze and output JSON."""


def rewrite_headline(
    article: Article,
    config: LLMConfig,
    cache: Optional[LLMCache] = None,
) -> tuple[bool, Optional[str]]:
    """Send article to LLM and determine if headline is clickbait + get replacement.

    Returns:
        (is_clickbait, new_title_or_None)
    """
    api_key = _load_api_key(config)
    if not api_key:
        log.warning("No API key configured for LLM, skipping rewriting")
        return False, None

    prompt = _build_prompt(article)

    # --- Cache check: skip API call if we already have this exact prompt ---
    if cache:
        cached_resp = cache.get(SYSTEM_PROMPT, prompt, config.model)
        if cached_resp is not None:
            is_cb = cached_resp.get("is_clickbait", False)
            new_hd = cached_resp.get("new_headline")
            if is_cb and new_hd:
                log.info("  Cached (LLM): Clickbait → \"%s\"", new_hd[:80])
            elif is_cb:
                log.info("  Cached (LLM): Clickbait but no replacement")
            else:
                log.info("  Cached (LLM): Not clickbait — keeping original")
            return is_cb, new_hd

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

        # Store in LLM cache for future identical prompts
        if cache:
            cache.set(SYSTEM_PROMPT, prompt, config.model, {
                "is_clickbait": is_clickbait,
                "new_headline": new_headline,
            })

        return is_clickbait, new_headline

    except Exception as e:
        log.error("  LLM call failed for '%s': %s", article.title[:40], e)
        return False, None


BATCH_SYSTEM_PROMPT = SYSTEM_PROMPT + """

When analyzing multiple articles, output a JSON array in article order:
[
  {"is_clickbait": true, "new_headline": "..."},
  {"is_clickbait": false, "new_headline": null}
]"""

# ── LLM-based image selection fallback ──────────────────────────────────────
#
# When a clickbait article has been rewritten (and the og:image likely shows
# the reference franchise rather than the actual subject), this module uses a
# separate LLM call to pick the best image from the article body.
#
# Toggle: set CLICKBUST_IMAGE_SELECTION=0 in the environment, or change the
# constant below to False, to skip LLM-based image selection entirely.
# Body image extraction still runs (data is stored in the archive), but the
# headline-selected og:image is used as-is.
_IMAGE_SELECTION_ENABLED = os.environ.get("CLICKBUST_IMAGE_SELECTION", "1") == "1"

_IMAGE_SELECTION_SYSTEM_PROMPT = """You are an editorial image selector. Given article text and a list of image candidates (URL + alt text), choose the image that best represents the actual subject of the article.

Rules:
1. Pick the image whose subject matches the article's MAIN subject (not a referenced franchise, not a comparison point).
2. Prefer images that contain the named work (show/movie/game title) or the actual person/character.
3. If the article is a "X meets Y" comparison, favour an image of the primary subject (X), not the reference (Y).
4. If multiple candidates are equally good, pick the largest/highest-quality image.
5. Return ONLY the URL — no explanation, no markdown, just the URL.

Example: Article is about "Blue Eye Samurai" (headline mentions "Shogun meets John Wick").
- Image of Hiroyuki Sanada in Shogun → BAD (it's the reference franchise)
- Image of Blue Eye Samurai poster → GOOD (it's the actual subject)
- Image of Keanu Reeves in John Wick → BAD (it's a comparison point)"""


def _build_image_sel_prompt(article: Article) -> str:
    """Build a prompt asking the LLM to pick the best image for this article.

    Includes the rewritten headline (which names the actual subject) and the
    full body-image candidate list with alt text.
    """
    display_title = article.rewritten_title or article.title
    candidates = "\n".join(
        f"  {i+1}. URL: {img['url']}\n     Alt: {img.get('alt', '')}"
        for i, img in enumerate(article.body_images[:20])  # cap at 20 candidates
    )
    content_preview = (article.content_text or "")[:1200]
    return f"""Article title: {article.title}
Rewritten headline: {display_title}

Content preview:
{content_preview}

Body image candidates:
{candidates}

Which of these images best represents the subject "{display_title}"? Return ONLY the URL."""


def _needs_image_selection(article: Article) -> bool:
    """Return True if this article should get LLM-based image selection.

    Only triggers for clickbait articles with a rewritten title (meaning the
    original og:image likely shows the wrong subject) AND at least one body
    image candidate to choose from.
    """
    if not _IMAGE_SELECTION_ENABLED:
        return False
    if not article.is_clickbait or not article.rewritten_title:
        return False
    if not article.body_images:
        return False
    return True


def _select_images_batch(
    articles: list[Article],
    config: LLMConfig,
    cache: Optional[LLMCache] = None,
) -> list[Article]:
    """Send articles needing image selection in a single batched LLM call.

    Each article gets its own prompt within the call; the LLM returns one
    URL per article (or empty string if none suitable). Selected URLs replace
    ``article.image_url``.

    Falls back to individual calls if the batch fails.
    Max 1 extra LLM call per article (the batch IS that one call).
    """
    if not articles:
        return articles

    api_key = _load_api_key(config)
    if not api_key:
        log.info("  No API key — skipping image selection for %d articles", len(articles))
        return articles

    # Check cache for each article's image-selection prompt
    uncached: list[Article] = []
    for art in articles:
        if cache:
            sel_prompt = _build_image_sel_prompt(art)
            cached_entry = cache.get(_IMAGE_SELECTION_SYSTEM_PROMPT, sel_prompt, config.model)
            if cached_entry is not None and isinstance(cached_entry, dict):
                cached_url = cached_entry.get("selected_url", "")
                if cached_url:
                    art.image_url = cached_url
                    log.info("  [img] 🟢 Cached → %s", cached_url[:80])
                    continue
        uncached.append(art)

    if not uncached:
        log.info("  Image selection — ALL cached, skipping API call")
        return articles

    log.info("  Selecting images for %d articles via LLM...", len(uncached))

    # Build batch prompt: one article per section
    parts = ["For each article below, pick the best body image that represents the subject."]
    for i, art in enumerate(uncached, 1):
        parts.append(f"\n--- Article {i} ---")
        parts.append(_build_image_sel_prompt(art))

    parts.append("\n\nRespond with a JSON array of URLs in article order, e.g.")
    parts.append('["https://...", "https://..."]')
    parts.append('Use empty string "" if no image fits.')

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": _IMAGE_SELECTION_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(parts)},
        ],
        "temperature": 0.2,
        "max_tokens": 512 * max(1, len(uncached)),
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

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
            log.warning("  Image selection response not a list — falling back to individual calls")
            raise ValueError("Expected JSON array")

        for i, art in enumerate(uncached):
            if i < len(data):
                chosen_url = data[i]
                if isinstance(chosen_url, str) and chosen_url:
                    art.image_url = chosen_url
                    log.info("  [img] ✅ Selected → %s", chosen_url[:80])
                else:
                    log.info("  [img] ⚠️  No image selected for article %d", i + 1)
            else:
                log.warning("  [img] Missing response for article %d", i)

            # Cache the result
            if cache:
                sel_prompt = _build_image_sel_prompt(art)
                cache.set(
                    _IMAGE_SELECTION_SYSTEM_PROMPT, sel_prompt, config.model,
                    {"selected_url": art.image_url if art.image_url else ""},
                )

        return articles

    except Exception as e:
        log.warning("  Batch image selection failed (%s) — falling back to individual calls", e)
        for art in uncached:
            _select_image_single(art, config, cache=cache)
        return articles


def _select_image_single(
    article: Article,
    config: LLMConfig,
    cache: Optional[LLMCache] = None,
) -> Article:
    """Make a single LLM call to pick the best image for one article.

    Fallback used when batch selection fails.
    """
    api_key = _load_api_key(config)
    if not api_key:
        return article

    prompt = _build_image_sel_prompt(article)

    # Check cache
    if cache:
        cached_entry = cache.get(_IMAGE_SELECTION_SYSTEM_PROMPT, prompt, config.model)
        if cached_entry is not None and isinstance(cached_entry, dict):
            cached_url = cached_entry.get("selected_url", "")
            if cached_url:
                article.image_url = cached_url
                return article

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": _IMAGE_SELECTION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 256,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=30) as client:
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

        if content and content != '""' and content != "''":
            article.image_url = content
            log.info("  [img] Single: %s", content[:80])

        if cache:
            cache.set(_IMAGE_SELECTION_SYSTEM_PROMPT, prompt, config.model,
                      {"selected_url": article.image_url if article.image_url else ""})
    except Exception as e:
        log.warning("  Single image selection failed: %s", e)

    return article


def _batch_prompt(articles: list[Article]) -> str:
    """Build a batch prompt that lists multiple articles for a single API call."""
    parts = ["Analyze each headline against its article content."]
    for i, art in enumerate(articles, 1):
        content_preview = (art.content_text or art.summary or "")[:2000]
        parts.append(
            f"\n--- Article {i} ---\n"
            f"Headline: {art.title}\n"
            f"Content: {content_preview}"
        )
    parts.append("\n\nOutput JSON array in article order.")
    return "\n".join(parts)


def _rewrite_batch(
    articles: list[Article],
    config: LLMConfig,
    cache: Optional[LLMCache] = None,
) -> list[Article]:
    """Send a batch of articles in a single LLM call and parse results.

    Falls back to individual calls if the batch call fails.
    When a cache is provided, checks per-article content hash before calling
    the API so only genuinely new articles consume tokens.
    """
    api_key = _load_api_key(config)
    if not api_key:
        for art in articles:
            art.is_clickbait = False
            art.rewritten_title = None
        return articles

    # --- Cache check: split articles into cached vs need-LLM ---
    uncached: list[Article] = []
    cached_indices: set[int] = set()

    for i, art in enumerate(articles):
        if cache:
            single_prompt = _build_prompt(art)
            cached_resp = cache.get(BATCH_SYSTEM_PROMPT, single_prompt, config.model)
            if cached_resp is not None:
                art.is_clickbait = cached_resp.get("is_clickbait", False)
                art.rewritten_title = cached_resp.get("new_headline")
                cached_indices.add(i)
                if art.is_clickbait:
                    log.info("  [%d/%d] 🔵 Cached clickbait → \"%s\"",
                             i + 1, len(articles), (art.rewritten_title or "?")[:60])
                else:
                    log.info("  [%d/%d] 🔵 Cached not clickbait — \"%s\"",
                             i + 1, len(articles), art.title[:50])
                continue
        uncached.append(art)

    if not uncached:
        log.info("Batch %d articles — ALL cached, skipping API call", len(articles))
        return articles

    log.info("Batch rewriting %d headlines (%d cached, %d to fetch)...",
             len(articles), len(cached_indices), len(uncached))

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": _batch_prompt(uncached)},
        ],
        "temperature": 0.3,
        "max_tokens": 512 * len(uncached),  # Scale tokens with batch size
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

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

        for i, art in enumerate(uncached):
            if i < len(data):
                entry = data[i]
                art.is_clickbait = entry.get("is_clickbait", False)
                art.rewritten_title = entry.get("new_headline")

                # Store in cache for future use
                if cache:
                    single_prompt = _build_prompt(art)
                    cache.set(BATCH_SYSTEM_PROMPT, single_prompt, config.model, {
                        "is_clickbait": art.is_clickbait,
                        "new_headline": art.rewritten_title,
                    })

                if art.is_clickbait and art.rewritten_title:
                    log.info("  [%d/%d] ✅ Clickbait → \"%s\"",
                             i + 1, len(uncached), art.rewritten_title[:60])
                else:
                    log.info("  [%d/%d] 🟢 Not clickbait — keeping \"%s\"",
                             i + 1, len(uncached), art.title[:50])
            else:
                log.warning("  [%d] Missing response entry — marking as not clickbait", i)
                art.is_clickbait = False
                art.rewritten_title = None

        # ── LLM-based image selection fallback ──────────────────────────
        # After headlines are rewritten, check if any articles have body
        # images that better represent the actual subject. This catches
        # "reference bait" where og:image shows the comparison franchise
        # instead of the named subject. Runs as a single batched LLM call.
        # Toggle: CLICKBUST_IMAGE_SELECTION=0 (env var) or change the
        # _IMAGE_SELECTION_ENABLED constant at the top of this file.
        articles_needing_img = [a for a in articles if _needs_image_selection(a)]
        if articles_needing_img:
            log.info("  Running LLM image selection for %d/%d articles...",
                     len(articles_needing_img), len(articles))
            _select_images_batch(articles_needing_img, config, cache=cache)

        return articles

    except Exception as e:
        log.warning("Batch LLM call failed (%s) — falling back to individual calls", e)
        for art in uncached:
            _, _ = rewrite_headline(art, config, cache=cache)
        return articles


def rewrite_all(
    articles: list[Article],
    config: LLMConfig,
    batch_size: int = 10,
    cache: Optional[LLMCache] = None,
) -> list[Article]:
    """Rewrite headlines for all articles that have content.

    Uses batched API calls by default to reduce HTTP overhead and token usage.
    Falls back to individual calls on failure.

    Args:
        articles: List of articles to process (must have content_text).
        config: LLM configuration.
        batch_size: Number of articles per batch call (default: 10).
        cache: Optional LLM response cache to avoid duplicate API calls.
    """
    to_rewrite = [a for a in articles if a.content_text]
    skipped = [a for a in articles if not a.content_text]

    for i in range(0, len(to_rewrite), batch_size):
        batch = to_rewrite[i:i + batch_size]
        _rewrite_batch(batch, config, cache=cache)

    return to_rewrite + skipped