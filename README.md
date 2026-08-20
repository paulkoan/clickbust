# Clickbust

[!["This sci-fi show on Netflix will change your life" → "Severance Season 2 Sets Up A Dark Slow-Burn Mystery"]]()

Clickbust is a CLI tool that replaces misleading clickbait headlines with informative ones.

It fetches articles from your favourite sites via RSS, uses an LLM to detect clickbait and rewrite headlines, then publishes a Google Discover-compatible static site with clean titles and redirects to the original articles.

## How it works

1. **Fetch** — Reads RSS feeds from configured sites
2. **Extract** — Pulls article content using readability-lxml
3. **Rewrite** — Sends headline + content to an LLM, gets a better title
4. **Publish** — Generates a static site with:
   - Individual article pages (with Google Discover meta tags)
   - A feed index with rewritten headlines
   - An RSS feed (Google News compatible)
   - Auto-redirect to the original article (meta refresh + JS fallback)

## Setup

```bash
# Clone or copy the project
cd clickbust

# Install dependencies
uv sync

# Configure your API key and sites
vim config.yaml
```

### Configuration

Edit `config.yaml`:

```yaml
llm:
  endpoint: "https://openrouter.ai/api/v1/chat/completions"
  api_key: "sk-or-v1-your-key-here"     # Your API key
  model: "openai/gpt-4o-mini"            # Or any OpenAI-compatible model

sites:
  - name: "ScreenRant"
    rss_url: "https://screenrant.com/feed/"
    site_url: "https://screenrant.com"
    enabled: true

output:
  dir: "output"
  site_title: "Clickbust — Rewritten Headlines"
  site_description: "Clickbait-free headlines from your favourite sites"
  base_url: "https://your-site.com"       # Your deployment URL
  max_articles: 50
```

**API key:** Clickbust supports any OpenAI-compatible API. Set your key directly in `config.yaml` or use an env var reference like `${MY_API_KEY}`.

**Recommended models:** `openai/gpt-4o-mini` (fast & cheap), `anthropic/claude-3-5-sonnet`, `openai/gpt-4o`, or any model good at text analysis.

## Usage

```bash
# List configured sites
clickbust list-sites

# Check a single article for clickbait
clickbust check https://screenrant.com/some-clickbait-article/

# Run the full pipeline
clickbust run

# With more articles per site
clickbust run --max-per-site 30

# Verbose mode for debugging
clickbust -v run
```

## Output

The `output/` directory is a self-contained static site:

```
output/
├── index.html          # Article listing with rewritten headlines
├── feed.xml            # RSS feed for Google News readers
└── a/
    ├── article-slug-1.html   # Individual article → redirects to original
    ├── article-slug-2.html
    └── ...
```

Each article page includes:
- Rewritten headline as `<title>` and Open Graph tags
- Article summary as meta description
- Google Discover-friendly meta tags (`og:`, `twitter:`, `article:`)
- Auto-redirect to the original article after 3 seconds

## Deployment

The `output/` folder is a static site — deploy anywhere:

### GitHub Pages

```bash
# Push output/ to the gh-pages branch
cd output
git init
git checkout -b gh-pages
git add .
git commit -m "deploy: clickbust rotation"
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -f origin gh-pages
```

Then enable GitHub Pages from the `gh-pages` branch in your repo settings.

### Netlify / Vercel

```bash
# Point your deploy to the output/ directory
# Netlify: drag-and-drop output/ or connect repo with publish dir = output
```

### Any static host

Just copy the `output/` directory to your web server or S3 bucket.

## Adding to Google Discover / Google News

1. Deploy your site and note the URL (e.g. `https://your-site.com`)
2. Add `https://your-site.com/feed.xml` to your RSS reader
3. For Google News: submit your site via [Google Publisher Center](https://publishercenter.google.com/)
4. For Google Discover: Google needs to index your pages naturally — the `og:`, `twitter:`, and `article:` meta tags help with this
5. In your Google News settings, add your Clickbust RSS feed and remove the original clickbait feed

## Post-deploy: Request Indexing via URL Inspection Tool

After each deploy, priority new articles should be manually submitted to Google via the
[Search Console URL Inspection Tool](https://search.google.com/search-console/inspect).
This is the closest thing Google offers to a manual "recrawl now" button, with a ~10/day limit.

**Process:**

1. Run the deploy (or wait for the cron job to complete)
2. Open [Google Search Console → URL Inspection](https://search.google.com/search-console/inspect) for `clickbust.cybr.fi`
3. Paste the URL of each priority article (one at a time)
4. Click "Request Indexing"

**Which URLs to submit:**

Pick the most important new articles from the day's run:
- Breaking news / human interest stories (highest traffic potential)
- Articles about popular franchises (Marvel, Star Wars, streaming hits)
- Timely content (reviews, announcements, season premieres)
- Skip evergreen listicles — they'll get indexed naturally via the sitemap

The deploy script outputs the top 10 priority URLs after each successful deploy as a reminder.
This is a supplement for the most important individual articles, not a replacement.

## Examples

**Before (ScreenRant original):**
> "John Cena's 2-Part Sci-Fi Thriller Is Far Better Than His Recent Movies"

**After (Clickbust rewritten):**
> "Peacemaker Season 2 Proves John Cena Can Lead A Sci-Fi Hit"

**Before (ScreenRant original):**
> "Prime Video's New High Fantasy Dragon Series Faces A Major Challenge"

**After (Clickbust rewritten):**
> "Fourth Wing TV Series On Prime Video Will Struggle To Match The Books' Scale"

*Results depend on your LLM model — better models produce sharper rewrites.*

## Testing

```bash
# Install dev dependencies
uv sync --dev

# Run all tests
uv run pytest -v
```

Tests live in the `tests/` directory:
- **`tests/test_llm_cache.py`** — 8 unit tests for the LLM response cache
- **`tests/test_prompts.py`** — 11 functional tests for prompt templates

## Requirements

- Python 3.11+
- `uv` package manager (or `pip`)
- An OpenAI-compatible API key

## Caching & Optimisation

Clickbust uses a multi-layer caching architecture to minimise LLM API calls and reduce
token consumption. Three cache layers work together — see
[docs/optimisation.md](docs/optimisation.md) for full details:

| Layer | What it caches | Cache key | TTL |
|---|---|---|---|
| **seen_cache** | Article headlines + LLM results | URL + headline hash | 7 days |
| **llm_cache** | LLM API responses | SHA256(prompt content) | 1 hour |
| **Archive** | Article metadata | URL | Indefinite (500 cap) |

**Key numbers:**
- **42%** seen_cache hit rate on daily runs
- **44%** token reduction from prompt optimisation
- **~$0.018** estimated cost per daily run (OpenRouter gpt-4o-mini)
- **~7** LLM API calls per run (down from ~58 without batching + caching)

## Extending

### Add more sites

Just add entries to the `sites:` list in `config.yaml`:

```yaml
sites:
  - name: "Collider"
    rss_url: "https://collider.com/feed/"
    site_url: "https://collider.com"
    enabled: true
  - name: "CBR"
    rss_url: "https://www.cbr.com/feed/"
    site_url: "https://www.cbr.com"
    enabled: true
```

### Custom LLM endpoint

Use any OpenAI-compatible API:

```yaml
llm:
  endpoint: "http://localhost:1234/v1/chat/completions"
  api_key: "not-needed"
  model: "local-model"
```

## License

MIT
