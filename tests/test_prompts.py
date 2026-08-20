"""Functional tests for prompt templates."""

from clickbust.models import Article
from clickbust.rewriter import SYSTEM_PROMPT, BATCH_SYSTEM_PROMPT, _build_prompt, _batch_prompt
from clickbust.notes import VOICE_SYSTEM_PROMPT, _build_prompt as note_build_prompt


def test_system_prompt_size():
    """SYSTEM_PROMPT should be under 500 tokens (rough char/4 estimate)."""
    assert len(SYSTEM_PROMPT) < 2000, f"SYSTEM_PROMPT too long: {len(SYSTEM_PROMPT)} chars"


def test_batch_system_prompt_size():
    """BATCH_SYSTEM_PROMPT should be under 500 tokens."""
    assert len(BATCH_SYSTEM_PROMPT) < 2200, f"BATCH_SYSTEM_PROMPT too long: {len(BATCH_SYSTEM_PROMPT)} chars"


def test_voice_system_prompt_size():
    """VOICE_SYSTEM_PROMPT should be under 200 tokens."""
    assert len(VOICE_SYSTEM_PROMPT) < 800, f"VOICE_SYSTEM_PROMPT too long: {len(VOICE_SYSTEM_PROMPT)} chars"


def test_individual_prompt_structure():
    """Individual prompt should have headline, content, and output instruction."""
    art = Article(
        title="Test Headline: You Won't Believe This",
        url="https://example.com/article",
        site_name="Test Site",
        content_text="This is the article content. " * 100,
    )
    prompt = _build_prompt(art)

    assert "Headline:" in prompt, "Prompt missing headline section"
    assert "Content:" in prompt, "Prompt missing content section"
    assert "JSON" in prompt, "Prompt missing output format instruction"

    # Content should be truncated to 2500 chars
    assert len(prompt) < 2700, f"Prompt too long: {len(prompt)} chars"


def test_batch_prompt_structure():
    """Batch prompt should have header, article separators, footer."""
    articles = [
        Article(title=f"Headline {i}", url=f"https://example.com/{i}",
                site_name="Test", content_text="Content here. " * 50)
        for i in range(3)
    ]
    prompt = _batch_prompt(articles)

    assert "Analyze each headline" in prompt, "Batch prompt missing header"
    assert "Output JSON array" in prompt, "Batch prompt missing footer"
    assert "Article 1" in prompt, "Batch prompt missing article separator"
    assert "Article 2" in prompt, "Batch prompt missing article separator"
    assert "Article 3" in prompt, "Batch prompt missing article separator"

    # Content per article should be truncated to 2000 chars
    for i in range(3):
        assert f"Headline {i}" in prompt, f"Missing headline {i}"


def test_content_truncation_individual():
    """Individual prompt should truncate content to ~2500 chars."""
    art = Article(
        title="Test",
        url="https://example.com",
        site_name="Test",
        content_text="word " * 2000,
    )
    prompt = _build_prompt(art)
    # 2500 chars of content + overhead
    assert len(prompt) < 2700, f"Individual prompt too long: {len(prompt)} chars"


def test_content_truncation_batch():
    """Batch prompt should truncate content to ~2000 chars per article."""
    art = Article(
        title="Test",
        url="https://example.com",
        site_name="Test",
        content_text="word " * 2000,
    )
    prompt = _batch_prompt([art])
    # 2000 chars of content + overhead for header/footer
    assert len(prompt) < 2200, f"Batch prompt too long: {len(prompt)} chars"


def test_empty_content_fallback():
    """Prompt should work with empty content_text — uses summary."""
    art = Article(
        title="Test",
        url="https://example.com",
        site_name="Test",
        content_text="",
        summary="This is a fallback summary.",
    )
    prompt = _build_prompt(art)
    assert len(prompt) > 0, "Prompt should not be empty with summary fallback"


def test_note_prompt_structure():
    """Note prompt should include topic and optional context."""
    prompt = note_build_prompt("Something about today", context="The weather was good")
    assert "Something about today" in prompt, "Missing topic"
    assert "weather was good" in prompt, "Missing context"


def test_all_sections_present():
    """All critical sections should be present in system prompts."""
    sections = [
        "clickbait", "rewrite", "headline",
        "not clickbait", "JSON",
        "120 chars",
    ]
    for section in sections:
        assert section.lower() in SYSTEM_PROMPT.lower(), f"Missing section: {section}"