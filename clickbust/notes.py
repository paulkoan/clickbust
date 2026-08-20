"""Daily notes — short, voice-y, anti-AI personal essays."""

import logging
import os
import re
from datetime import date as Date
from typing import Optional

import httpx
from jinja2 import Environment, FileSystemLoader

from .llm_cache import LLMCache
from .models import LLMConfig
from .rewriter import _load_api_key

log = logging.getLogger(__name__)

VOICE_SYSTEM_PROMPT = """You write short daily notes in Paul's voice — UK-based, direct, no filler.

Hard rules:
- No em dashes (use comma or full stop)
- No filler: "it's worth noting", "in a world where", "let's explore", "notably", "interestingly", "importantly", "journey", "leverage", "utilise", "however" (use "but")
- No hedging — say it directly
- No "in conclusion" — stop when done
- Title is a statement, not a question
- One topic, 200-400 words

CRITICAL: Do not invent actions. Never say "I bought/sold/did". Observations only.

Output: First line = title (plain). Blank line. Body in paragraphs separated by double newlines. No markdown, no bullets. End with: Paul"""


def _build_prompt(topic: str, context: str = "") -> str:
    """Build the user prompt for the LLM note request."""
    parts = [f"Write a note about: {topic}"]
    if context:
        parts.append(f"\nContext:\n{context}")
    return "\n".join(parts)


def _parse_note_response(content: str) -> tuple[str, str]:
    """Parse the LLM response into (title, body_html).

    First line is the title, rest is body paragraphs separated by blank lines.
    """
    content = content.strip()
    lines = content.split("\n")

    # First non-empty line is the title
    title = ""
    body_lines = []
    found_title = False
    for line in lines:
        stripped = line.strip()
        if not found_title:
            if stripped:
                title = stripped
                found_title = True
            continue
        # Skip blank lines between paragraphs
        body_lines.append(line)

    body_text = "\n".join(body_lines).strip()
    # Split into paragraphs on double newlines and wrap in <p>
    paragraphs = re.split(r"\n{2,}", body_text)
    body_html = "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())

    return title, body_html


def _get_preview(body_html: str, max_chars: int = 200) -> str:
    """Get a plain-text preview of the note body for the index page."""
    text = re.sub(r"<[^>]+>", "", body_html)
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "..."


def generate_note(
    topic: str,
    config: LLMConfig,
    context: str = "",
    template_dir: str = "templates",
    output_dir: str = "output",
    base_url: str = "https://clickbust.cybr.fi",
    today: Optional[Date] = None,
    cache: Optional[LLMCache] = None,
) -> tuple[str, str, str]:
    """Generate a daily note: LLM call + template render + save.

    Returns:
        (filename, title, date_string)
    """
    if today is None:
        today = Date.today()
    date_str = today.isoformat()  # 2026-06-19
    filename = f"{date_str}.html"

    api_key = _load_api_key(config)
    if not api_key:
        log.warning("No API key configured — can't generate note")
        return filename, "", ""

    prompt = _build_prompt(topic, context)

    # --- Cache check: skip API call if identical note was generated recently ---
    if cache:
        cached_resp = cache.get(VOICE_SYSTEM_PROMPT, prompt, config.model)
        if cached_resp is not None:
            cached_content = cached_resp.get("content", "")
            log.info("Note LLM cache HIT — reusing cached response")
            title, body_html = _parse_note_response(cached_content)
            if title:
                _render_and_save_note(title, body_html, date_str, filename,
                                      template_dir, output_dir, base_url, today)
                return filename, title, date_str
            log.warning("Cached note had empty title — falling through to fresh call")

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": VOICE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 800,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    log.info("Generating note on: %s", topic[:60])
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(config.endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()
        content = result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.error("Note LLM call failed: %s", e)
        return filename, "", ""

    # Store in cache for future identical prompts
    if cache:
        cache.set(VOICE_SYSTEM_PROMPT, prompt, config.model, {"content": content})

    title, body_html = _parse_note_response(content)
    _render_and_save_note(title, body_html, date_str, filename,
                         template_dir, output_dir, base_url, today)
    return filename, title, date_str


def _render_and_save_note(
    title: str,
    body_html: str,
    date_str: str,
    filename: str,
    template_dir: str,
    output_dir: str,
    base_url: str,
    today: Date,
) -> None:
    """Render a note HTML template and save to disk."""
    meta_description = _get_preview(body_html)
    published_date = today.strftime("%d %B %Y")

    abs_template_dir = os.path.abspath(template_dir)
    env = Environment(loader=FileSystemLoader(abs_template_dir), autoescape=False)
    template = env.get_template("note.html.j2")
    html = template.render(
        title=title,
        site_name="Paul Murphy",
        meta_description=meta_description,
        base_url=base_url.rstrip("/"),
        filename=filename,
        published_date=published_date,
        body_html=body_html,
    )

    notes_dir = os.path.join(output_dir, "notes")
    os.makedirs(notes_dir, exist_ok=True)
    out_path = os.path.join(notes_dir, filename)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    log.info("Note saved: %s — \"%s\"", filename, title)