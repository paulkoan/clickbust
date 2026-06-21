"""Daily notes — short, voice-y, anti-AI personal essays."""

import logging
import os
import re
from datetime import date as Date
from typing import Optional

import httpx
from jinja2 import Environment, FileSystemLoader

from .models import LLMConfig
from .rewriter import _load_api_key

log = logging.getLogger(__name__)

VOICE_SYSTEM_PROMPT = """You are writing a short daily note in Paul's voice.

Paul is UK-based. He talks the way he thinks - direct, no filler, like he's telling a mate.

**Hard rules - violate these and the note is junk:**
- No em dashes. Use a comma or a full stop.
- No "it's worth noting", "in a world where", "let's explore", "let's dive in", "as we can see"
- No "notably", "interestingly", "importantly" - just say the thing
- No "journey", "explore", "leverage", "utilise", "however" (use "but")
- No hedging - "this is the thing" is better than "it could be argued"
- No "in conclusion", "to summarise" - just stop when you're done
- No title that's a question. Titles are statements.
- One topic per note. 200-400 words. If you only have 150 words worth of thought, write 150 words.

**CRITICAL - DO NOT INVENT SPECIFICS:**
Never say "I bought", "I sold", "I did", or attribute any action or decision to Paul
that he hasn't stated. The note must stay at the level of ideas and observations.
"It reminds me of the dot-com crash" is fine. "I sold my NVIDIA stock" is NOT fine.

**Output format:**
First line is the title (plain text, no markdown, no quotes).
Then a blank line.
Then the body, written as plain paragraphs separated by double newlines.
No markdown, no formatting, no bullet points.

The note must be about something real - a thing Paul thought about today, a conversation
he had, or something he noticed. Not generic commentary. Be specific about the observation
or the idea, but never fabricate personal actions.

End the note with a new line containing just:
"Paul"
"""


def _build_prompt(topic: str, context: str = "") -> str:
    """Build the user prompt for the LLM note request."""
    parts = [f"Write a note about: {topic}"]
    if context:
        parts.append(f"\nContext from today:\n{context}")
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

    title, body_html = _parse_note_response(content)
    meta_description = _get_preview(body_html)
    published_date = today.strftime("%d %B %Y")

    # Render the template
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

    # Save to output/notes/
    notes_dir = os.path.join(output_dir, "notes")
    os.makedirs(notes_dir, exist_ok=True)
    out_path = os.path.join(notes_dir, filename)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    log.info("Note saved: %s — \"%s\"", filename, title)
    return filename, title, date_str