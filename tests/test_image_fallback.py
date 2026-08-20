"""Tests for the image fallback pipeline: resolve_article_image()."""

from datetime import datetime, timezone

import pytest

from clickbust.generator import resolve_article_image
from clickbust.models import Article


# ── Helpers ──────────────────────────────────────────────────────────


def _make_article(
    title: str = "Fallout Meets Stardew Valley In Stunning New Game",
    image_url: str = "https://example.com/og-star-wars-like.jpg",
    body_images: list[dict] | None = None,
    rewritten_title: str | None = None,
    summary: str = "",
    url: str = "https://screenrant.com/fallout-stardew-new-game/",
) -> Article:
    """Build an Article with sensible defaults for image-fallback tests."""
    return Article(
        title=title,
        url=url,
        site_name="ScreenRant",
        content_text=summary or "Some article body text about Fallout and Stardew Valley.",
        summary=summary or "Fallout meets Stardew Valley in this new crossover game.",
        image_url=image_url,
        body_images=body_images or [],
        rewritten_title=rewritten_title,
        article_id="test-article-abc123",
        published_date=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )


# ── resolve_article_image tests ────────────────────────────────────


def test_match_image_unchanged(monkeypatch):
    """When detect.predict() returns MATCH, image_url stays unchanged."""
    import clickbust.detect as detect

    original_predict = detect.predict

    def mock_predict(**kw):
        return ("MATCH", 0.85)

    monkeypatch.setattr(detect, "predict", mock_predict)

    art = _make_article(image_url="https://example.com/og.jpg")
    result = resolve_article_image(art)
    assert result == "https://example.com/og.jpg", f"Expected unchanged, got {result}"

    monkeypatch.setattr(detect, "predict", original_predict)


def test_mismatch_falls_back_to_body_image(monkeypatch):
    """When MISMATCH and body_images exist, fallback to first body image."""
    import clickbust.detect as detect

    original_predict = detect.predict

    def mock_predict(**kw):
        return ("MISMATCH", 0.65)

    monkeypatch.setattr(detect, "predict", mock_predict)

    art = _make_article(
        image_url="https://example.com/og-wrong.jpg",
        body_images=[
            {"url": "https://example.com/body-img.jpg", "alt": "Fallout vault door"},
            {"url": "https://example.com/body-img2.jpg", "alt": "Stardew Valley farm"},
        ],
    )
    result = resolve_article_image(art)
    assert result == "https://example.com/body-img.jpg", f"Expected body image, got {result}"

    monkeypatch.setattr(detect, "predict", original_predict)


def test_mismatch_no_body_images_uses_default_banner(monkeypatch):
    """When MISMATCH and no body images, use configured default_banner_url."""
    import clickbust.detect as detect

    original_predict = detect.predict

    def mock_predict(**kw):
        return ("MISMATCH", 0.60)

    monkeypatch.setattr(detect, "predict", mock_predict)

    art = _make_article(image_url="https://example.com/og-wrong.jpg", body_images=[])
    result = resolve_article_image(
        art, default_banner_url="https://clickbust.cybr.fi/assets/og-default.svg"
    )
    assert result == "https://clickbust.cybr.fi/assets/og-default.svg", \
        f"Expected default banner, got {result}"

    monkeypatch.setattr(detect, "predict", original_predict)


def test_mismatch_no_fallback_keeps_original(monkeypatch):
    """When MISMATCH but no body images and no default banner, keep original."""
    import clickbust.detect as detect

    original_predict = detect.predict

    def mock_predict(**kw):
        return ("MISMATCH", 0.50)

    monkeypatch.setattr(detect, "predict", mock_predict)

    art = _make_article(
        image_url="https://example.com/og-last-resort.jpg",
        body_images=[],
    )
    result = resolve_article_image(art)
    assert result == "https://example.com/og-last-resort.jpg", \
        f"Expected original image, got {result}"

    monkeypatch.setattr(detect, "predict", original_predict)


def test_mismatch_body_image_missing_url_skips_to_banner(monkeypatch):
    """When body_images entry has no 'url' key, skip to default banner."""
    import clickbust.detect as detect

    original_predict = detect.predict

    def mock_predict(**kw):
        return ("MISMATCH", 0.55)

    monkeypatch.setattr(detect, "predict", mock_predict)

    art = _make_article(
        image_url="https://example.com/og.jpg",
        body_images=[{"url": "", "alt": "description only"}],
    )
    result = resolve_article_image(
        art, default_banner_url="https://clickbust.cybr.fi/assets/og-default.svg"
    )
    assert result == "https://clickbust.cybr.fi/assets/og-default.svg", \
        f"Expected default banner, got {result}"

    monkeypatch.setattr(detect, "predict", original_predict)


def test_match_with_rewritten_title_uses_it(monkeypatch):
    """resolve_article_image uses rewritten_title when available for detection."""
    import clickbust.detect as detect

    original_predict = detect.predict
    captured = {}

    def mock_predict(**kw):
        captured["headline"] = kw.get("headline", "")
        return ("MATCH", 0.85)

    monkeypatch.setattr(detect, "predict", mock_predict)

    art = _make_article(
        title="Original Clickbait Title You Won't Believe",
        rewritten_title="Fallout Meets Stardew Valley In New Crossover Game",
    )
    resolve_article_image(art)
    assert captured["headline"] == "Fallout Meets Stardew Valley In New Crossover Game", \
        f"Expected rewritten title, got: {captured['headline']}"

    monkeypatch.setattr(detect, "predict", original_predict)


def test_passess_body_alt_and_snippet(monkeypatch):
    """resolve_article_image passes body-image alt text and snippet to detect."""
    import clickbust.detect as detect

    original_predict = detect.predict
    captured = {}

    def mock_predict(**kw):
        captured.update(kw)
        return ("MATCH", 0.80)

    monkeypatch.setattr(detect, "predict", mock_predict)

    art = _make_article(
        image_url="https://example.com/og.jpg",
        body_images=[{"url": "https://example.com/body.jpg", "alt": "Stardew Valley farm landscape"}],
        summary="Fallout meets Stardew Valley in this amazing new crossover game that fans are loving.",
    )
    resolve_article_image(art)
    assert "Stardew Valley farm landscape" in captured.get("alt_text_body", ""), \
        f"Expected alt text in args, got: {captured}"
    assert "Fallout meets Stardew Valley" in captured.get("article_snippet", ""), \
        f"Expected snippet in args, got: {captured}"

    monkeypatch.setattr(detect, "predict", original_predict)


def test_no_body_images_list_ok(monkeypatch):
    """Article with body_images=None doesn't crash."""
    import clickbust.detect as detect

    original_predict = detect.predict

    def mock_predict(**kw):
        return ("MATCH", 0.85)

    monkeypatch.setattr(detect, "predict", mock_predict)

    art = _make_article(body_images=None)
    result = resolve_article_image(art)
    assert result == "https://example.com/og-star-wars-like.jpg"

    monkeypatch.setattr(detect, "predict", original_predict)
