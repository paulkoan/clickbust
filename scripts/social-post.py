#!/usr/bin/env python3
"""Post new articles to social media (Bluesky, X/Twitter) from the Clickbust archive.

Usage:
    python scripts/social-post.py                     # post to Bluesky (default)
    python scripts/social-post.py --dry-run           # show what would be posted
    python scripts/social-post.py --platform x        # post to X/Twitter
    python scripts/social-post.py --platform bluesky  # post to Bluesky
    python scripts/social-post.py --max 5             # post up to 5 articles

Config (in config.yaml):
    social:
      bluesky:
        handle: "user.bsky.social"
        app_password: "${BLUESKY_APP_PASSWORD}"
        max_per_run: 3
        enabled: false
      x:
        xurl_app: "clickbust"
        enabled: false

Posted tracker: output/posted.json (tracks which articles have been posted to which platform)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# Load config
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.yaml")
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
POSTED_PATH = os.path.join(OUTPUT_DIR, "posted.json")
ARTICLES_PATH = os.path.join(OUTPUT_DIR, "articles.json")

# Default site URL
BASE_URL = "https://clickbust.cybr.fi"


def load_config():
    """Load social config from config.yaml."""
    import yaml

    if not os.path.exists(CONFIG_PATH):
        print(f"❌ Config not found: {CONFIG_PATH}")
        sys.exit(1)

    with open(CONFIG_PATH) as f:
        raw = yaml.safe_load(f)

    social = raw.get("social", {})
    return social


def resolve_env(value: str) -> str:
    """Resolve ${VAR} or $VAR patterns in config values."""
    import re

    if not isinstance(value, str):
        return value

    def _replace(m):
        var = m.group(1)
        return os.environ.get(var, "")

    return re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: os.environ.get(m.group(1) or m.group(2), ""), value)


def load_posted() -> dict:
    """Load the posted tracker. Returns {url: {platform: timestamp, ...}, ...}"""
    if os.path.exists(POSTED_PATH):
        with open(POSTED_PATH) as f:
            return json.load(f)
    return {}


def save_posted(posted: dict):
    """Save the posted tracker."""
    os.makedirs(os.path.dirname(POSTED_PATH), exist_ok=True)
    with open(POSTED_PATH, "w") as f:
        json.dump(posted, f, indent=2, sort_keys=True)


def load_articles() -> list[dict]:
    """Load articles from the archive, newest first."""
    if not os.path.exists(ARTICLES_PATH):
        print(f"❌ No articles archive found at {ARTICLES_PATH}")
        return []

    with open(ARTICLES_PATH) as f:
        raw = json.load(f)

    # raw is a dict keyed by URL
    articles = list(raw.values())

    # Sort by published_date descending
    articles.sort(
        key=lambda a: a.get("published_date", ""),
        reverse=True,
    )

    return articles


def make_post_text(article: dict) -> str:
    """Generate the post text for an article.

    Format:
    [Rewritten Title]

    Originally from [Site Name]

    https://clickbust.cybr.fi/a/[article_id].html
    """
    display_title = article.get("rewritten_title") or article.get("title", "")
    site_name = article.get("site_name", "")
    article_id = article.get("article_id", "")

    text = f"{display_title}\n\nFrom {site_name}\n\n{BASE_URL}/a/{article_id}.html"

    # Bluesky has a 300-char limit for posts
    if len(text) > 297:
        # Truncate the title
        max_title = 297 - len(f"\n\nFrom {site_name}\n\n{BASE_URL}/a/{article_id}.html")
        if max_title < 20:
            # Fallback: short post
            text = f"{display_title[:200]}\n\n{BASE_URL}/a/{article_id}.html"
        else:
            text = f"{display_title[:max_title - 3]}...\n\nFrom {site_name}\n\n{BASE_URL}/a/{article_id}.html"

    return text


def post_to_bluesky(article: dict, handle: str, app_password: str, dry_run: bool = False) -> bool:
    """Post an article to Bluesky. Returns True on success."""
    from atproto import Client, models

    text = make_post_text(article)
    article_id = article.get("article_id", "")
    url = f"{BASE_URL}/a/{article_id}.html"
    display_title = article.get("rewritten_title") or article.get("title", "")

    if dry_run:
        print(f"  [DRY-RUN] Would post to Bluesky:")
        print(f"    Text: {text[:100]}...")
        print(f"    Link: {url}")
        print(f"    Title: {display_title[:60]}")
        return True

    try:
        client = Client()
        client.login(handle, app_password)

        # Post with an external link embed (link card)
        embed = models.AppBskyEmbedExternal.Main(
            external=models.AppBskyEmbedExternal.External(
                uri=url,
                title=display_title[:200],
                description=article.get("summary", "")[:200] or (display_title[:200]),
            )
        )

        post = client.send_post(text=text, embed=embed)
        print(f"  ✅ Posted to Bluesky: {post.uri}")
        return True

    except Exception as e:
        print(f"  ❌ Bluesky post failed: {e}")
        return False


def post_to_x(article: dict, dry_run: bool = False) -> bool:
    """Post an article to X/Twitter via xurl CLI. Returns True on success."""
    import subprocess

    text = make_post_text(article)
    article_id = article.get("article_id", "")
    url = f"{BASE_URL}/a/{article_id}.html"

    if dry_run:
        print(f"  [DRY-RUN] Would post to X:")
        print(f"    Text: {text[:100]}...")
        return True

    try:
        # Check if xurl is available
        result = subprocess.run(["xurl", "auth", "status"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            print(f"  ❌ xurl not configured: {result.stderr.strip()}")
            return False

        # Post with xurl
        result = subprocess.run(
            ["xurl", "post", text],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            post_id = data.get("data", {}).get("id", "unknown")
            print(f"  ✅ Posted to X: https://x.com/i/status/{post_id}")
            return True
        else:
            print(f"  ❌ X post failed: {result.stderr.strip() or result.stdout}")
            return False

    except FileNotFoundError:
        print(f"  ⚠️  xurl CLI not installed — can't post to X")
        return False
    except Exception as e:
        print(f"  ❌ X post error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Post new articles to social media")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be posted without posting")
    parser.add_argument("--platform", choices=["bluesky", "x", "all"], default="bluesky",
                        help="Which platform to post to (default: bluesky)")
    parser.add_argument("--max", type=int, default=3, help="Max articles to post per run (default: 3)")
    args = parser.parse_args()

    social = load_config()
    posted = load_posted()
    articles = load_articles()

    if not articles:
        print("No articles to process.")
        return

    # Filter to unposted articles
    platforms = ["bluesky", "x"] if args.platform == "all" else [args.platform]
    unscored = []

    for art in articles:
        url = art.get("url", "")
        if not url:
            continue
        # Check if already posted to all requested platforms
        already = all(url in posted and p in posted[url] for p in platforms)
        if not already:
            # Only post clickbait articles that have rewritten titles
            # (non-clickbait articles are fine too, but clickbait ones are the value-add)
            if art.get("is_clickbait") and art.get("rewritten_title"):
                unscored.append(art)
            elif not art.get("is_clickbait"):
                unscored.append(art)

    if not unscored:
        print("No new articles to post.")
        return

    # Limit to max per run
    to_post = unscored[:args.max]
    total = len(unscored)
    print(f"📱 Social cross-posting: {len(to_post)} article(s) to post ({total} new total available)")

    posted_this_run = 0
    for art in to_post:
        display_title = art.get("rewritten_title") or art.get("title", "")
        site_name = art.get("site_name", "")
        print(f"\n  Article: {display_title[:80]}")
        print(f"  Source:  {site_name}")

        url = art.get("url", "")
        for platform in platforms:
            if url in posted and platform in posted.get(url, {}):
                print(f"  [{platform}] Already posted — skipping")
                continue

            success = False
            if platform == "bluesky":
                bsky_cfg = social.get("bluesky", {})
                if not bsky_cfg.get("enabled", False):
                    if not args.dry_run:
                        print(f"  [bluesky] Disabled in config — skipping")
                        continue
                handle = resolve_env(bsky_cfg.get("handle", ""))
                app_password = resolve_env(bsky_cfg.get("app_password", ""))
                if not handle or not app_password:
                    if not args.dry_run:
                        print(f"  [bluesky] Missing handle or app_password in config — skipping")
                        continue
                success = post_to_bluesky(art, handle, app_password, dry_run=args.dry_run)

            elif platform == "x":
                x_cfg = social.get("x", {})
                if not x_cfg.get("enabled", False):
                    if not args.dry_run:
                        print(f"  [x] Disabled in config — skipping")
                        continue
                success = post_to_x(art, dry_run=args.dry_run)

            if success:
                if url not in posted:
                    posted[url] = {}
                posted[url][platform] = datetime.now(timezone.utc).isoformat()
                posted_this_run += 1

    # Save posted tracker (only in live mode — dry-run must not mutate state)
    if not args.dry_run:
        save_posted(posted)

    print(f"\n{'─' * 40}")
    if args.dry_run:
        print(f"✅ DRY-RUN completed — {len(to_post)} article(s) would have been posted")
    else:
        print(f"✅ Posted {posted_this_run} article(s) to social media")


if __name__ == "__main__":
    main()