"""Tests for image extraction: getBestImage() and _parse_img_width()."""

from clickbust.fetcher import _parse_img_width, getBestImage


def test_parse_img_width_attribute():
    """_parse_img_width reads width attribute."""
    import lxml.html

    el = lxml.html.fromstring('<img width="800" src="https://example.com/img.jpg">')
    assert _parse_img_width(el) == 800


def test_parse_img_width_style():
    """_parse_img_width reads style='width: Xpx'."""
    import lxml.html

    el = lxml.html.fromstring('<img style="width: 600px" src="https://example.com/img.jpg">')
    assert _parse_img_width(el) == 600


def test_parse_img_width_style_max():
    """_parse_img_width reads style='max-width: Xpx'."""
    import lxml.html

    el = lxml.html.fromstring('<img style="max-width: 450px" src="https://example.com/img.jpg">')
    assert _parse_img_width(el) == 450


def test_parse_img_width_no_width():
    """_parse_img_width returns 0 when no width info present."""
    import lxml.html

    el = lxml.html.fromstring('<img src="https://example.com/img.jpg">')
    assert _parse_img_width(el) == 0


def test_parse_img_width_percentage():
    """_parse_img_width returns 0 for percentage-based widths."""
    import lxml.html

    el = lxml.html.fromstring('<img style="width: 100%" src="https://example.com/img.jpg">')
    assert _parse_img_width(el) == 0


def test_parse_img_width_auto():
    """_parse_img_width returns 0 for 'auto' width."""
    import lxml.html

    el = lxml.html.fromstring('<img style="width: auto" src="https://example.com/img.jpg">')
    assert _parse_img_width(el) == 0


# --- getBestImage tests ---


def test_get_best_image_picks_large():
    """getBestImage returns the largest image >= 400px."""
    html = """\
<article>
  <p>Some text</p>
  <img width="300" src="https://example.com/small.jpg">
  <img width="800" src="https://example.com/large.jpg" alt="Large">
  <img width="400" src="https://example.com/medium.jpg" alt="Medium">
</article>"""
    result = getBestImage(html, fallback_url="https://example.com/og.jpg")
    assert result == "https://example.com/large.jpg"


def test_get_best_image_falls_back():
    """getBestImage returns fallback when no images >= 400px."""
    html = """\
<article>
  <p>Some text</p>
  <img width="300" src="https://example.com/tiny.jpg">
  <img width="150" src="https://example.com/icon.jpg">
</article>"""
    result = getBestImage(html, fallback_url="https://example.com/og.jpg")
    assert result == "https://example.com/og.jpg"


def test_get_best_image_no_images():
    """getBestImage returns fallback when no images at all."""
    html = "<article><p>Just text, no images.</p></article>"
    result = getBestImage(html, fallback_url="https://example.com/og.jpg")
    assert result == "https://example.com/og.jpg"


def test_get_best_image_skips_data_uri():
    """getBestImage skips data: URIs even if they'd have a width."""
    html = """\
<article>
  <img width="800" src="data:image/png;base64,iVBORw0KGgo=">
  <img width="600" src="https://example.com/real.jpg">
</article>"""
    result = getBestImage(html, fallback_url="https://example.com/og.jpg")
    assert result == "https://example.com/real.jpg"


def test_get_best_image_empty_fallback():
    """getBestImage returns fallback_url (empty string) as-is when nothing found."""
    html = "<article><p>No images</p></article>"
    result = getBestImage(html, fallback_url="")
    assert result == ""


def test_get_best_image_barely_qualifies():
    """getBestImage accepts images at exactly 400px width."""
    html = """\
<article>
  <img width="400" src="https://example.com/exactly.jpg">
</article>"""
    result = getBestImage(html, fallback_url="https://example.com/og.jpg")
    assert result == "https://example.com/exactly.jpg"


def test_get_best_image_with_style_width():
    """getBestImage reads style='width: ...px' as well as width attribute."""
    html = """\
<article>
  <img src="https://example.com/style-large.jpg" style="width: 850px">
  <img width="500" src="https://example.com/attr-med.jpg">
</article>"""
    result = getBestImage(html, fallback_url="https://example.com/og.jpg")
    assert result == "https://example.com/style-large.jpg"


def test_get_best_image_malformed_html():
    """getBestImage gracefully handles malformed HTML, returns fallback."""
    result = getBestImage(">>> not html <<<", fallback_url="https://example.com/og.jpg")
    assert result == "https://example.com/og.jpg"


# ── extract_content default banner tests ──────────────────────────────────


def test_extract_content_falls_back_to_default_banner(monkeypatch):
    """extract_content returns default_banner_url when no body image and no og:image found."""
    import httpx
    from clickbust.fetcher import extract_content

    html_no_images = """\
<html><head></head><body>
<article><p>Just text with no images whatsoever.</p></article>
</body></html>"""

    class FakeResponse:
        status_code = 200
        text = html_no_images

        def raise_for_status(self):
            pass

    original_get = httpx.Client.get

    def mock_get(self, url, **kw):
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    try:
        text, summary, image_url, body_images = extract_content(
            "https://example.com/no-images",
            default_banner_url="https://clickbust.cybr.fi/assets/og-default.svg",
        )
        assert image_url == "https://clickbust.cybr.fi/assets/og-default.svg", \
            f"Expected default banner, got: {image_url}"
    finally:
        monkeypatch.setattr(httpx.Client, "get", original_get)


def test_extract_content_og_image_overrides_default(monkeypatch):
    """extract_content uses og:image over default_banner_url when found."""
    import httpx
    from clickbust.fetcher import extract_content

    html_with_og = """\
<html><head>
<meta property="og:image" content="https://example.com/og.jpg">
</head><body>
<article><p>Text with no usable body images (too small or none).</p>
<img width="200" src="https://example.com/tiny.jpg">
</article>
</body></html>"""

    class FakeResponse:
        status_code = 200
        text = html_with_og

        def raise_for_status(self):
            pass

    original_get = httpx.Client.get

    def mock_get(self, url, **kw):
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    try:
        text, summary, image_url, body_images = extract_content(
            "https://example.com/has-og",
            default_banner_url="https://clickbust.cybr.fi/assets/og-default.svg",
        )
        # Should prefer og:image (extracted from raw HTML) over default banner
        # Body image is too small (< 400px), so getBestImage falls back to og:image
        assert image_url == "https://example.com/og.jpg", \
            f"Expected og:image, got: {image_url}"
    finally:
        monkeypatch.setattr(httpx.Client, "get", original_get)


def test_extract_content_no_body_or_og_without_default(monkeypatch):
    """extract_content returns empty string when no image found and no default given."""
    import httpx
    from clickbust.fetcher import extract_content

    html_no_images = """\
<html><head></head><body>
<article><p>Just text, no images.</p></article>
</body></html>"""

    class FakeResponse:
        status_code = 200
        text = html_no_images

        def raise_for_status(self):
            pass

    original_get = httpx.Client.get

    def mock_get(self, url, **kw):
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    try:
        text, summary, image_url, body_images = extract_content(
            "https://example.com/no-images"
        )
        assert image_url == "", \
            f"Expected empty string (no default given), got: {image_url}"
    finally:
        monkeypatch.setattr(httpx.Client, "get", original_get)
