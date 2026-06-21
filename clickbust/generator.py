"""Static site and RSS feed generator."""

import logging
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import AppConfig, Article
from .stats import get_site_stats

log = logging.getLogger(__name__)

# Jinja2 environment
_env: Environment | None = None


def _get_env(template_dir: str) -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
        )
        _env.globals["now"] = lambda: datetime.now(timezone.utc)
    return _env


def generate_notes_index(
    output_dir: str,
    config: AppConfig,
    template_dir: str | None = None,
) -> int:
    """Generate the notes index page listing all notes. Returns note count."""
    if template_dir is None:
        template_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
    template_dir = os.path.abspath(template_dir)

    notes_dir = os.path.join(output_dir, "notes")
    if not os.path.isdir(notes_dir):
        return 0

    # Gather all note files, sorted newest first
    note_files = sorted(
        f for f in os.listdir(notes_dir) if f.endswith(".html") and f != "index.html"
    )
    note_files.reverse()

    notes = []
    for fname in note_files:
        path = os.path.join(notes_dir, fname)
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue

        # Extract title from <title> tag
        title_match = re.search(r"<title>([^<]+)", content)
        title = title_match.group(1).strip() if title_match else fname.replace(".html", "")

        # Extract first 150 chars of preview from body text
        body_match = re.search(r'<div class="body">(.*?)</div>', content, re.DOTALL)
        preview = ""
        if body_match:
            preview_text = re.sub(r"<[^>]+>", "", body_match.group(1)).strip()[:150]
            if len(preview_text) > 147:
                preview_text = preview_text[:147] + "..."
            preview = preview_text

        # Date: filename without .html, format
        date_str = fname.replace(".html", "")
        from datetime import datetime as dt
        try:
            parsed = dt.strptime(date_str, "%Y-%m-%d")
            date_display = parsed.strftime("%d %B %Y")
        except ValueError:
            date_display = date_str

        notes.append({
            "title": title,
            "url": fname,
            "date": date_display,
            "preview": preview,
        })

    if not notes:
        return 0

    # Render notes index
    env = _get_env(template_dir)
    template = env.get_template("notes_index.html.j2")
    html = template.render(
        notes=notes,
        site_name=config.output.site_title,
        base_url=config.output.base_url,
    )
    with open(os.path.join(notes_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    log.info("Generated notes index — %d notes", len(notes))
    return len(notes)


def _extract_domain(site_url: str) -> str:
    """Extract just the domain from a URL for favicon lookup."""
    match = re.search(r"://([^/]+)", site_url)
    return match.group(1) if match else site_url


def _slugify_name(name: str) -> str:
    """Turn a site name into a URL-safe slug."""
    return re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")


def generate_site(articles: list[Article], config: AppConfig, stats: dict | None = None) -> int:
    """Generate the complete static site. Returns number of articles published."""
    if stats is None:
        stats = {}
    output_dir = config.output.dir
    template_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
    template_dir = os.path.abspath(template_dir)

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Copy static assets & CNAME
    assets_src = os.path.join(template_dir, "assets")
    assets_dst = os.path.join(output_dir, "assets")
    if os.path.isdir(assets_src):
        shutil.copytree(assets_src, assets_dst, dirs_exist_ok=True)

    cname_src = os.path.join(template_dir, "CNAME")
    if os.path.isfile(cname_src):
        shutil.copy2(cname_src, os.path.join(output_dir, "CNAME"))
        log.info("Copied CNAME file")

    # Filter to rewritten articles (clickbait ones get new titles, others keep original)
    published = []
    for art in articles:
        if art.is_clickbait and art.rewritten_title:
            published.append(art)
        elif not art.is_clickbait:
            published.append(art)

    # Sort by published date (newest first), limit to max
    published.sort(key=lambda a: a.published_date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    published = published[: config.output.max_articles]

    if not published:
        log.warning("No articles to publish")
        return 0

    # Copy static assets
    assets_src = os.path.join(template_dir, "assets")
    assets_dst = os.path.join(output_dir, "assets")
    if os.path.isdir(assets_src):
        shutil.copytree(assets_src, assets_dst, dirs_exist_ok=True)

    # Copy article redirect pages
    articles_dir = os.path.join(output_dir, "a")
    os.makedirs(articles_dir, exist_ok=True)

    env = _get_env(template_dir)
    article_template = env.get_template("article.html.j2")

    # Build site lookup: name → site_url from config (needed for article favicons)
    site_url_map = {s.name: s.site_url for s in config.sites}

    for art in published:
        display_title = art.rewritten_title if art.is_clickbait and art.rewritten_title else art.title
        meta_desc = art.summary[:200] if art.summary else display_title

        # Look up the site URL for favicon
        art_site_url = site_url_map.get(art.site_name, "")
        art_domain = _extract_domain(art_site_url) if art_site_url else ""

        html = article_template.render(
            article=art,
            display_title=display_title,
            meta_description=meta_desc,
            site_name=config.output.site_title,
            site_domain=art_domain,
            base_url=config.output.base_url,
        )
        out_path = os.path.join(articles_dir, f"{art.article_id}.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)

    log.info("Generated %d article pages", len(published))

    # Group articles by site_name for per-site pages
    site_groups = defaultdict(list)
    for art in published:
        site_groups[art.site_name].append(art)

    # Build site lookup: name → site_url from config
    site_url_map = {s.name: s.site_url for s in config.sites}

    # Generate per-site pages
    site_template = env.get_template("site.html.j2")
    sites_info = []
    for site_name, site_articles in sorted(site_groups.items()):
        site_slug = _slugify_name(site_name)
        site_url = site_url_map.get(site_name, "")
        site_domain = _extract_domain(site_url) if site_url else site_slug
        site_stats = get_site_stats(stats, site_name)

        html = site_template.render(
            articles=site_articles,
            site=config.output,
            site_name=site_name,
            site_slug=site_slug,
            site_domain=site_domain,
            site_description=f"Clickbait-free headlines from {site_name}",
            site_stats=site_stats,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )
        with open(os.path.join(output_dir, f"{site_slug}.html"), "w", encoding="utf-8") as f:
            f.write(html)

        sites_info.append({
            "name": site_name,
            "slug": site_slug,
            "domain": site_domain,
            "count": len(site_articles),
        })

    log.info("Generated %d per-site pages", len(sites_info))

    # Generate About page
    about_template = env.get_template("about.html.j2")
    about_html = about_template.render(
        base_url=config.output.base_url,
        sites=config.sites,
    )
    with open(os.path.join(output_dir, "about.html"), "w", encoding="utf-8") as f:
        f.write(about_html)
    log.info("Generated about.html")

    # Generate robots.txt
    robots_template = env.get_template("robots.txt.j2")
    robots_txt = robots_template.render(base_url=config.output.base_url)
    with open(os.path.join(output_dir, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(robots_txt)
    log.info("Generated robots.txt")

    site_slugs = [(s["name"], s["slug"]) for s in sites_info]

    # Gather note files for sitemap
    notes_dir = os.path.join(output_dir, "notes")
    note_files = []
    if os.path.isdir(notes_dir):
        note_files = sorted(
            f for f in os.listdir(notes_dir)
            if f.endswith(".html") and f != "index.html"
        )

    # Build notes data for feed: title, filename, preview, pub_date
    notes_for_feed = []
    for fname in note_files:
        path = os.path.join(notes_dir, fname)
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        title_match = re.search(r"<title>([^<]+)", content)
        title = title_match.group(1).strip() if title_match else fname.replace(".html", "")
        date_match = re.search(r'<div class="date">([^<]+)', content)
        pub_date = date_match.group(1).strip() if date_match else fname.replace(".html", "")
        body_match = re.search(r'<div class="body">(.*?)</div>', content, re.DOTALL)
        preview = ""
        if body_match:
            preview_text = re.sub(r"<[^>]+>", "", body_match.group(1)).strip()[:300]
            preview = preview_text
        notes_for_feed.append({
            "title": title,
            "filename": fname,
            "preview": preview,
            "pub_date": pub_date,
        })

    sitemap_template = env.get_template("sitemap.xml.j2")
    sitemap_xml = sitemap_template.render(
        base_url=config.output.base_url,
        articles=published,
        site_slugs=site_slugs,
        note_files=note_files,
    )
    with open(os.path.join(output_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(sitemap_xml)
    log.info("Generated sitemap.xml")

    # Generate index page
    index_template = env.get_template("index.html.j2")
    index_html = index_template.render(
        articles=published,
        sites=sites_info,
        site=config.output,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    with open(os.path.join(output_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)

    # Generate RSS feed
    feed_template = env.get_template("feed.xml.j2")
    feed_xml = feed_template.render(
        articles=published,
        notes=notes_for_feed,
        site=config.output,
        generated_at=datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"),
    )
    with open(os.path.join(output_dir, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(feed_xml)

    log.info("Generated index.html and feed.xml")
    return len(published)