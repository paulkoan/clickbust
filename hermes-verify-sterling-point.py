#!/usr/bin/env python3
"""Ad-hoc verification: Sterling Point headline rewrite on clickbust.cybr.fi"""
import json, sys, os, re

errors = []

# 1. Check the archive (articles.json) has the corrected data
ARCHIVE = "/opt/data/clickbust/output/articles.json"
URL = "https://screenrant.com/jeffrey-dean-morgan-sterling-point-season-1-rotten-tomatoes-score/"
NEW_HEADLINE = "Sterling Point: Jeffrey Dean Morgan's Prime Video Series Debuts To Rare Rotten Tomatoes Score"

with open(ARCHIVE) as f:
    archive = json.load(f)

entry = archive.get(URL)
if entry is None:
    errors.append("articles.json: URL not found in archive")
else:
    assert entry["is_clickbait"] == True, "is_clickbait should be True"
    assert entry["rewritten_title"] == NEW_HEADLINE, f"rewritten_title mismatch: {entry['rewritten_title']!r}"
    print(f"  ✓ articles.json: is_clickbait=True, rewritten_title set")

# 2. Check the seen cache
SEEN = "/opt/data/clickbust/output/seen_articles.json"
with open(SEEN) as f:
    seen = json.load(f)

sentry = seen.get(URL)
if sentry is None:
    errors.append("seen_articles.json: URL not found")
else:
    assert sentry["is_clickbait"] == True, "seen cache is_clickbait should be True"
    assert sentry["rewritten_title"] == NEW_HEADLINE, f"seen cache rewritten_title mismatch"
    print(f"  ✓ seen_articles.json: is_clickbait=True, rewritten_title set")

# 3. Check the generated HTML article page (all headline locations)
HTML = "/opt/data/clickbust/output/a/jeffrey-dean-morgan-s-new-prime-video-series-debuts-to-rare--395b2f0e6962.html"
with open(HTML) as f:
    html = f.read()

checks = {
    "<title>": r"<title>Sterling Point:.*?</title>",
    "<h1>": r"<h1>Sterling Point:.*?</h1>",
    "og:title": r'og:title" content="Sterling Point:',
    "twitter:title": r'twitter:title" content="Sterling Point:',
    "JSON-LD headline": r'"headline":\s*"Sterling Point:',
    "img alt": r'alt="Sterling Point:',
}

for label, pattern in checks.items():
    if re.search(pattern, html):
        print(f"  ✓ HTML: {label} contains 'Sterling Point:'")
    else:
        errors.append(f"HTML: {label} does NOT contain 'Sterling Point:'")

# 4. Check old headline is gone from <h1> and <title>
old_h1 = r"<h1>Jeffrey Dean Morgan's New Prime Video Series"
if re.search(old_h1, html):
    errors.append("HTML: old headline still present in <h1>")

old_title = r"<title>Jeffrey Dean Morgan's New Prime Video Series"
if re.search(old_title, html):
    errors.append("HTML: old headline still present in <title>")

original_ref = "Originally published as"
if original_ref in html:
    print(f"  ✓ HTML: original headline preserved in 'Originally published as' line (correct)")

print(f"\n  ───────────────────────────────────")
if errors:
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
else:
    print(f"  ✓ All checks passed — headline corrected on all 6 locations")