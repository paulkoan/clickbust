#!/usr/bin/env python3
"""Clickbust CLI — Rewrite clickbait headlines with informative ones."""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

import yaml

from .fetcher import extract_content, fetch_all_sites
from .generator import generate_notes_index, generate_site
from .models import AppConfig, LLMConfig, OutputConfig, SiteConfig
from .notes import generate_note
from .rewriter import rewrite_all
from .seen_cache import (
    get_cached_result,
    is_unchanged,
    load_cache,
    mark_processed,
    prune_stale,
    save_cache,
)
from .stats import get_site_stats, load_stats, save_stats, update_stats

log = logging.getLogger(__name__)


def load_config(path: str) -> AppConfig:
    """Load and parse config.yaml."""
    if not os.path.exists(path):
        log.error("Config file not found: %s", path)
        sys.exit(1)

    with open(path) as f:
        raw = yaml.safe_load(f)

    # Parse sites
    sites = []
    for s in raw.get("sites", []):
        sites.append(
            SiteConfig(
                name=s.get("name", "Unknown"),
                rss_url=s.get("rss_url", ""),
                site_url=s.get("site_url", ""),
                enabled=s.get("enabled", True),
            )
        )

    # Parse LLM config
    llm_raw = raw.get("llm", {})
    llm = LLMConfig(
        endpoint=llm_raw.get("endpoint", ""),
        api_key=llm_raw.get("api_key", ""),
        model=llm_raw.get("model", ""),
    )

    # Parse output config
    out_raw = raw.get("output", {})
    output = OutputConfig(
        dir=out_raw.get("dir", "output"),
        site_title=out_raw.get("site_title", "Clickbust"),
        site_description=out_raw.get("site_description", ""),
        base_url=out_raw.get("base_url", ""),
        max_articles=out_raw.get("max_articles", 50),
        max_per_site=out_raw.get("max_per_site", 10),
    )

    return AppConfig(llm=llm, sites=sites, output=output)


def cmd_run(args):
    """Run the full pipeline: fetch → rewrite → generate."""
    config = load_config(args.config)
    log.info("Clickbust run started — %d sites configured", len(config.sites))

    enabled = [s for s in config.sites if s.enabled]
    if not enabled:
        log.warning("No enabled sites in config")
        return

    # Step 1: Fetch RSS feeds
    max_per_site = args.max_per_site if args.max_per_site else config.output.max_per_site
    articles = fetch_all_sites(enabled, max_per_site=max_per_site)
    log.info("Fetched %d articles total", len(articles))

    if not articles:
        log.warning("No articles fetched — nothing to do")
        return

    # Step 1.5: Load seen cache and split articles into new vs cached
    os.makedirs(config.output.dir, exist_ok=True)
    seen_cache = load_cache(config.output.dir)

    fresh_articles = []   # Need content extraction + LLM
    cached_articles = []  # Can reuse previous result

    for art in articles:
        if is_unchanged(seen_cache, art.url, art.title):
            cached_result = get_cached_result(seen_cache, art.url)
            if cached_result:
                art.is_clickbait = cached_result["is_clickbait"]
                art.rewritten_title = cached_result["rewritten_title"]
                art.summary = cached_result.get("summary", "")
                cached_articles.append(art)
                continue
        fresh_articles.append(art)

    reused = len(cached_articles)
    log.info("Seen cache: %d articles reused, %d need fresh processing", reused, len(fresh_articles))

    # Step 2: Extract content (only for new/changed articles)
    log.info("Extracting article content...")
    for i, art in enumerate(fresh_articles):
        try:
            text, summary = extract_content(art.url)
            art.content_text = text
            art.summary = summary
        except Exception as e:
            log.warning("  [%d/%d] Failed to extract '%s': %s", i + 1, len(fresh_articles), art.title[:40], e)
            art.content_text = ""
            art.summary = ""
        if (i + 1) % 5 == 0:
            log.info("  Extracted %d/%d fresh articles", i + 1, len(fresh_articles))

    # Step 3: Rewrite headlines (batched, only for new/changed articles)
    articles_with_content = [a for a in fresh_articles if a.content_text]
    if articles_with_content:
        log.info("Rewriting headlines for %d articles (batched)...", len(articles_with_content))
        articles_with_content = rewrite_all(articles_with_content, config.llm, batch_size=10)
    log.info("Skipped rewrite for %d cached articles", reused)

    # Merge fresh and cached for final list
    all_articles = articles_with_content + [a for a in fresh_articles if not a.content_text] + cached_articles

    # Count per-site for stats (fresh only — cached ones contributed on their original run)
    fresh_clickbait_count = sum(1 for a in articles_with_content if a.is_clickbait)
    per_site_counts: dict[str, int] = {}
    for art in articles_with_content:
        if art.is_clickbait:
            per_site_counts[art.site_name] = per_site_counts.get(art.site_name, 0) + 1

    total_clickbait = sum(1 for a in all_articles if a.is_clickbait)
    total_rewritten = sum(1 for a in all_articles if a.rewritten_title)
    log.info("Detected %d clickbait headlines (%d fresh) — %d rewritten",
             total_clickbait, fresh_clickbait_count, total_rewritten)

    # Step 4: Update persistent stats
    stats = load_stats(config.output.dir)
    stats = update_stats(stats, per_site_counts)
    save_stats(config.output.dir, stats)

    # Step 5: Update seen cache with fresh results
    for art in articles_with_content:
        mark_processed(
            seen_cache, art.url, art.title,
            art.is_clickbait, art.rewritten_title,
            summary=art.summary,
        )
    for art in cached_articles:
        # Re-mark as seen today (bumps last_seen)
        mark_processed(
            seen_cache, art.url, art.title,
            art.is_clickbait, art.rewritten_title,
            summary=art.summary,
            is_cached=True,
        )
    seen_cache = prune_stale(seen_cache)
    save_cache(config.output.dir, seen_cache)

    # Step 6: Generate site
    count = generate_site(all_articles, config, stats)
    log.info("Site generated — %s/ with %d articles", config.output.dir, count)

    # Step 7: Generate notes index (if any notes exist)
    note_count = generate_notes_index(config.output.dir, config)
    if note_count:
        log.info("Notes index generated — %d notes", note_count)

    # Summary
    print(f"\n✅ Clickbust run complete!")
    print(f"   Sites checked: {len(enabled)}")
    print(f"   Articles fetched: {len(articles)}")
    print(f"   Freshly processed: {len(fresh_articles)}")
    print(f"   Reused from cache: {reused}")
    print(f"   Clickbait detected: {total_clickbait} ({fresh_clickbait_count} fresh)")
    print(f"   Published: {count} articles")
    print(f"   LLM calls: {(len(articles_with_content) + 9) // 10} (batched)")
    print(f"   Output: {os.path.abspath(config.output.dir)}/")


def cmd_list_sites(args):
    """List configured sites."""
    config = load_config(args.config)
    print(f"\nConfigured sites ({len(config.sites)}):")
    print(f"{'Enabled':>7} | {'Site':<25} | {'RSS Feed'}")
    print("-" * 70)
    for s in config.sites:
        status = "✅" if s.enabled else "❌"
        print(f"{status:>7} | {s.name:<25} | {s.rss_url}")
    print()


def cmd_check(args):
    """Check a single URL for clickbait."""
    config = load_config(args.config)

    print(f"\nChecking: {args.url}")
    print("─" * 60)

    # Extract content
    print("Extracting content...")
    try:
        text, summary = extract_content(args.url)
    except Exception as e:
        print(f"  ❌ Failed to extract: {e}")
        sys.exit(1)

    print(f"  Content length: {len(text)} chars")
    print(f"  Summary: {summary[:150]}...")

    # Create a temporary article
    from .models import Article

    art = Article(
        title=args.title or text[:60].split(".")[0],
        url=args.url,
        site_name="Manual Check",
        content_text=text,
        summary=summary,
    )

    print(f"\nHeadline: {art.title}")
    print()

    # Rewrite headline
    from .rewriter import rewrite_headline as rh
    is_cb, new_title = rh(art, config.llm)

    if is_cb:
        print(f"  🔴 Clickbait detected!")
        if new_title:
            print(f"  ✅ Rewritten: {new_title}")
        else:
            print(f"  ⚠️  No replacement generated")
    else:
        print(f"  🟢 Looks fine — not clickbait")

    print()


def cmd_note(args):
    """Write a daily note in your voice."""
    config = load_config(args.config)
    context = args.context or ""
    topic = " ".join(args.topic) if args.topic else "Something from today — what happened, what caught your eye"

    # Check if a note already exists for today
    from datetime import date
    today = date.today().isoformat()
    existing_path = os.path.join(config.output.dir, "notes", f"{today}.html")
    if os.path.exists(existing_path):
        log.info("Note already exists for %s — skipping", today)
        print(f"⏭️  Note already exists for {today} — not overwriting")
        return

    log.info("Generating daily note...")
    filename, title, date_str = generate_note(
        topic=topic,
        config=config.llm,
        context=context,
        template_dir="templates",
        output_dir=config.output.dir,
        base_url=config.output.base_url,
    )

    if title:
        # Regenerate notes index with the new note included
        try:
            generate_notes_index(config.output.dir, config)
        except Exception as e:
            log.warning("Notes index generation: %s", e)

        print(f"\n✅ Note written: {date_str} — \"{title}\"")
        print(f"   File: {config.output.dir}/notes/{filename}")
    else:
        print("❌ Failed to generate note")


def main():
    parser = argparse.ArgumentParser(
        description="Clickbust — Rewrite clickbait headlines with informative ones"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    sub = parser.add_subparsers(dest="command", help="Command to run")

    # run
    run_p = sub.add_parser("run", help="Run full pipeline: fetch → rewrite → generate")
    run_p.add_argument(
        "--max-per-site",
        type=int,
        default=None,
        help="Max articles per site (default: from config.yaml)",
    )

    # list-sites
    sub.add_parser("list-sites", help="List configured sites")

    # check
    check_p = sub.add_parser("check", help="Analyze a single article URL for clickbait")
    check_p.add_argument("url", help="Article URL to check")
    check_p.add_argument("--title", help="Article headline (if not auto-detectable)")

    # note
    note_p = sub.add_parser("note", help="Write a daily note in your voice")
    note_p.add_argument("topic", nargs="*", help="What to write about (optional)")
    note_p.add_argument("--context", help="Context from today's events")

    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "run":
        cmd_run(args)
    elif args.command == "list-sites":
        cmd_list_sites(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "note":
        cmd_note(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()