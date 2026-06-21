# Clickbust — Clickbait Headline Rewriter Implementation Plan

> **Goal:** Build a CLI tool that fetches articles from clickbait-prone sites, uses an LLM to rewrite misleading headlines, and publishes a Google Discover-compatible static site with clean titles + redirects to original articles.

**Architecture:** Python CLI tool → RSS feed parsing → article content extraction via readability → LLM-based headline analysis & rewriting → static site generator (HTML pages + RSS feed) → deployable to GitHub Pages or any static host.

**Tech Stack:** Python 3.13, feedparser, readability-lxml, httpx, lxml, Jinja2, yaml, httpx (for LLM API calls to any OpenAI-compatible endpoint)

---

## Project Structure

```
clickbust/
├── config.yaml                  # Sites list + LLM config
├── clickbust/
│   ├── __init__.py
│   ├── cli.py                   # CLI entry point (argparse)
│   ├── models.py                # Data models (Article, Config, etc.)
│   ├── fetcher.py               # RSS fetching + article extraction
│   ├── rewriter.py              # LLM-based headline analysis
│   └── generator.py             # Static site + RSS feed generation
├── templates/
│   ├── index.html.j2            # Article listing page
│   └── feed.xml.j2              # RSS feed template
├── output/                      # Generated site (gitignore)
├── pyproject.toml               # uv-based project config
└── README.md                    # Setup + usage docs
```

### Tasks

### Task 1: Project scaffolding

**Objective:** Create the project structure, pyproject.toml, and config skeleton.

**Files:**
- Create: `clickbust/pyproject.toml`
- Create: `clickbust/config.yaml`
- Create: `clickbust/clickbust/__init__.py`
- Create: `clickbust/.gitignore`

**Steps:**
1. Create directory structure
2. Write pyproject.toml with dependencies (httpx, feedparser, readability-lxml, lxml, jinja2, pyyaml)
3. Write config.yaml with example site entry and LLM endpoint config
4. Write .gitignore

### Task 2: Data models

**Objective:** Define the data structures used throughout the app.

**Files:**
- Create: `clickbust/clickbust/models.py`

**Models:**
- `SiteConfig`: name, rss_url, site_url, enabled
- `Article`: title, url, site_name, content_text, rewritten_title, summary, published_date
- `AppConfig`: sites list, llm_endpoint, llm_api_key, llm_model, output_dir, template_dir

### Task 3: RSS fetcher + article content extractor

**Objective:** Fetch RSS feeds, parse articles, extract readable content.

**Files:**
- Create: `clickbust/clickbust/fetcher.py`

**Functions:**
- `fetch_rss(url: str) -> list[dict]`: Fetch and parse RSS feed
- `extract_content(url: str) -> tuple[str, str]`: Get article text + summary using readability-lxml
- `fetch_all_sites(config: AppConfig) -> list[Article]`: Orchestrate fetching all configured sites

### Task 4: LLM-based headline rewriter

**Objective:** Send headlines + content to an LLM, get clickbait assessment + better title.

**Files:**
- Create: `clickbust/clickbust/rewriter.py`

**Functions:**
- `rewrite_headline(article: Article, api_config) -> tuple[bool, str]`: Send to LLM, returns (is_clickbait, new_title)

**LLM Prompt:**
Analyze if the headline is clickbait (vague, withholds key info, exaggerated) vs the actual content. If it's clickbait, produce a specific, informative replacement. If not, return the original.

### Task 5: Static site + RSS feed generator

**Objective:** Generate HTML pages and RSS feed from rewritten articles.

**Files:**
- Create: `clickbust/clickbust/generator.py`
- Create: `clickbust/templates/index.html.j2`
- Create: `clickbust/templates/feed.xml.j2`

**Functions:**
- `generate_site(articles: list[Article], config: AppConfig)`: Write output/ directory
- `generate_index(articles, template_dir, output_dir)`: Article listing page
- `generate_article(article, template_dir, output_dir)`: Individual article redirect page
- `generate_feed(articles, template_dir, output_dir)`: RSS feed

**Article page:** Single HTML page with:
- The rewritten headline as `<title>` and `<h1>`
- Original site attribution
- Meta description with article summary
- Auto-redirect script to original URL (meta refresh + JS redirect)
- Google Discover-compatible meta tags

### Task 6: CLI entry point

**Objective:** Wire everything together into a single `clickbust` command.

**Files:**
- Create: `clickbust/clickbust/cli.py`

**Commands:**
- `clickbust run`: Fetch all sites, rewrite headlines, generate site
- `clickbust list-sites`: Show configured sites
- `clickbust check <url>`: Test headline analysis on a single URL

### Task 7: README + usage docs

**Objective:** Document setup, configuration, and deployment.

**Files:**
- Create: `clickbust/README.md`

---

## Verification

1. `cd clickbust && uv run clickbust list-sites` — shows configured sites
2. `cd clickbust && uv run clickbust check https://screenrant.com/example-article` — analyzes one article
3. `cd clickbust && uv run clickbust run` — fetches all, rewrites, generates output/
4. Check output/index.html in browser — cards with rewritten titles
5. Check output/feed.xml — valid RSS feed
6. Click an article card — should redirect to original URL after a moment

## Deployment

The `output/` directory is a self-contained static site. Deploy to:
- **GitHub Pages:** push to `docs/` or `gh-pages` branch
- **Vercel/Netlify:** point to `output/` as publish directory
- **Any static host:** just serve the files