# Clickbust — Caching Architecture & Token Optimisation Guide

> A reference for maintainers on how Clickbust avoids redundant work, trims
> LLM costs, and how to keep these optimisations healthy.

---

## Table of Contents

1. [Three-Layer Caching Architecture](#1-three-layer-caching-architecture)
   - [1a. Seen Cache](#1a-seen-cache-seen_cachepy)
   - [1b. LLM Cache](#1b-llm-cache-llm_cachepy)
   - [1c. Article Archive](#1c-article-archive-archivepy)
2. [TTL & Pruning Rules](#2-ttl--pruning-rules)
3. [Cache Interaction Walkthrough](#3-cache-interaction-walkthrough)
4. [Token Optimisations](#4-token-optimisations)
5. [Cost Tracking](#5-cost-tracking)
6. [Maintenance Guide](#6-maintenance-guide)

---

## 1. Three-Layer Caching Architecture

Clickbust has three independent cache layers, each with a different scope and
lifetime. They sit between the RSS feeds and the LLM API, intercepting work
that has already been done.

```
RSS Feed  ──→  Seen Cache    ──→  Content Extraction  ──→  LLM Cache  ──→  LLM API
                     │                                           │
                     ↓                                           ↓
              Reuse previous result                        Reuse identical prompt
                     │                                           │
                     └──────────────→  Article Archive  ←──────────┘
                                            │
                                            ↓
                                     Static Site
```

### 1a. Seen Cache (`seen_cache.py`)

**Purpose:** Avoid re-processing articles whose URL + headline haven't changed
between runs. This is the **first** gate an article passes through.

**How it works:**

- Keyed by article **URL**.
- Stores the **SHA256 hash of the headline** alongside the previous LLM result
  (`is_clickbait`, `rewritten_title`, `summary`, `image_url`).
- On each run, every article from RSS is checked against this cache with
  `is_unchanged(url, headline)`. If the URL exists AND the headline hash
  matches, the article skips content extraction and LLM calls entirely.
- Results marked `is_cached=True` still flow through the archive merge so
  `last_seen` is bumped.

**What it catches (that the LLM cache doesn't):**

- Exact re-runs (cron + manual, or error recovery) — same articles, no new API calls.
- Incremental runs where only new articles need processing.
- Articles whose content changed under a stable URL — headline hash won't match,
  so they get re-processed automatically.

**Storage:** A single JSON file `output/seen_articles.json`.

### 1b. LLM Cache (`llm_cache.py`)

**Purpose:** Avoid LLM API calls for prompts whose exact text has already been
submitted. This is the **second** gate — it sits inside `rewrite_headline()`
and `_rewrite_batch()`.

**How it works:**

- Keyed by **SHA256(`model || system_prompt || user_prompt`)**.
- Stores the parsed JSON response (`is_clickbait`, `new_headline`).
- TTL is applied lazily — checked on `get()`, pruned on `save()`.
- Thread-safe (`threading.Lock`) for safety, though Clickbust is single-threaded
  in practice.

**What it catches (that the seen cache doesn't):**

1. **Syndicated articles** — same story at different URLs → seen cache misses
   (different URL), but the prompt content (headline + article text) is
   effectively the same → LLM cache hits.
2. **Identical daily-note prompts** — re-running `clickbust note` with the same
   topic within the TTL window reuses the cached note.
3. **Error recovery** — if the pipeline crashes mid-batch and is re-run, already
   cached articles skip the API.

**Storage:** A single JSON file `output/llm_cache.json`. **Default TTL: 1 hour.**

**Usage notes:**

- Both `rewrite_headline()` (individual) and `_rewrite_batch()` (batch) check
  this cache. In batch mode, each article in the batch gets its own LLM cache
  lookup *before* the batch API call, so only genuinely new articles consume
  tokens.
- Individual articles are cached using the **BATCH_SYSTEM_PROMPT** key (not
  SYSTEM_PROMPT), so a cached batch result can also serve an individual
  `rewrite_headline()` call and vice-versa. The system prompt text is nearly
  identical (BATCH_SYSTEM_PROMPT just appends the array format instruction),
  so the SHA256 keys differ but coverage overlaps in practice.

### 1c. Article Archive (`archive.py`)

**Purpose:** Maintain a persistent, growing list of all articles seen across
runs. The archive allows the site to accumulate articles over time without
re-fetching or re-LLM-ing anything.

**How it works:**

- Keyed by article **URL**.
- On each run, `merge_articles()` upserts fresh + cached articles into the
  archive, then sorts by `published_date` descending.
- The generator (`generate_site()`) reads the full archive to render the
  static site — no LLM calls, only template rendering.

**Capacity:** Capped at `MAX_ARCHIVED = 500` articles. The `output.max_articles`
config key is the site-level publish limit (default 500). When the archive
exceeds `MAX_ARCHIVED`, the oldest articles are pruned.

**Storage:** A single JSON file `output/articles.json`.

**Token cost: ZERO.** Only article metadata is stored and re-rendered.

---

## 2. TTL & Pruning Rules

| Cache | TTL / Age Limit | Pruning Mechanism | Configuration Point |
|-------|-----------------|-------------------|---------------------|
| **LLM Cache** | 1 hour (default) | Lazy: checked on `get()`, stale entries removed on `save()` + explicit `prune()` | `LLMCache(cache_dir, ttl_seconds=...)` in `cli.py` (lines 99, 265, 296) |
| **Seen Cache** | 7 days since `last_seen` | Explicit: `prune_stale()` called at end of each `cmd_run()` | `MAX_AGE_DAYS = 7` in `seen_cache.py` |
| **Article Archive** | Infinite (until capacity reached) | Size-based: oldest articles evicted when `> MAX_ARCHIVED` | `MAX_ARCHIVED = 500` in `archive.py` |

**Why these TTLs?**

- **LLM cache (1 hour):** Headlines change rarely, but a 1-hour window is safe
  for cron runs without stale data. A longer TTL (e.g. 24h) would risk caching
  syndicated articles whose source has been updated.
- **Seen cache (7 days):** Articles naturally drop off RSS feeds after ~3-5 days.
  7 days ensures coverage across daily runs without indefinite growth.
- **Archive (500 articles):** At max 10 fresh articles/day from 9 sites ≈ 90/day,
  500 articles covers ~5-6 days. Runs beyond that rotate the oldest out.

**To adjust:** Set `ttl_seconds` where `LLMCache()` is instantiated (3 call
sites in `cli.py`), or edit the constants at the top of `seen_cache.py` or
`archive.py`.

---

## 3. Cache Interaction Walkthrough

A full `clickbust run` processes articles through the cache layers in sequence:

```
1. Fetch RSS  ──────────────────────────────────────────────────── (always fresh)
       │
2. Seen cache check (is_unchanged?)
       ├── HIT → reuse previous LLM result, skip extraction + LLM
       └── MISS → proceed to content extraction
                      │
3. Extract article text from URL
                      │
4. Batch headlines for LLM (10 per batch)
       │              │
5. For each article in batch, check LLM cache
       ├── HIT → reuse cached response, exclude from batch API call
       └── MISS → include in batch API call
                      │
6. Send batch to LLM API (only genuinely new articles)
                      │
7. Store LLM results in LLM cache (per-article, for future hits)
                      │
8. Merge fresh + cached articles into seen cache
   (bump last_seen, mark is_cached)
                      │
9. Prune stale seen cache entries (>7 days)
                      │
10. Merge into persistent archive (upsert by URL)
                      │
11. Generate static site from archive (template rendering only)
```

### Example: Two consecutive daily runs

**Day 1 (first run):** 58 articles from RSS. Seen cache is empty → all 58 go
through content extraction → batched into 6 LLM calls → results stored in LLM
cache, seen cache, and archive. Site generated with 58 articles.

**Day 2 (second run):** 55 articles from RSS (3 dropped off feed). Seen cache
finds ~50 articles with unchanged headlines → they skip extraction and LLM
entirely → 5 new/changed articles proceed. Among those 5, the LLM cache may
hit 1-2 (syndicated content or identical prompt from day 1). So Day 2 makes
~3-4 LLM API calls instead of 55 naive ones.

**Savings from Day 1 → Day 2:** ~50 content extractions skipped, ~50 LLM
calls avoided. Per-run input tokens drop from ~34,593 to ~19,215 (44%
reduction from prompt compression) then further by the seen cache skipping
entire articles.

---

## 4. Token Optimisations

Prompt optimisations were applied in July 2026 to reduce per-run LLM input
tokens. Full before/after report at `prompt-optimisation-report.md`.

### 4a. System Prompt Compression

| Prompt | Before (tokens) | After (tokens) | Reduction |
|--------|----------------|---------------|-----------|
| `SYSTEM_PROMPT` (headline analysis) | 1,685 | 447 | **73%** |
| `BATCH_SYSTEM_PROMPT` | 1,763 | 494 | **72%** |
| `VOICE_SYSTEM_PROMPT` (daily notes) | 404 | 178 | **56%** |

**Techniques used:**

1. **Merged redundant categories** — 12 verbose clickbait patterns collapsed
   into 7 concise numbered rules. Three separate "withholds the subject" types
   (description bait, platform bait, person archetype) unified under one rule
   with sub-types.
2. **Removed redundant sections** — the "Critical rule about named subjects"
   section restated earlier rules without adding new information.
3. **Shortened examples** — kept the most instructive example per rule, dropped
   duplicates (e.g. 4 remaining examples trimmed to 1 for description bait).
4. **Compressed NOT-clickbait guidance** — 5 bullet points reduced to 1 sentence.
5. **Single-line output format** — multiline JSON template compressed to one line.
6. **Removed narrative framing** in VOICE_SYSTEM_PROMPT — "Paul is UK-based..."
   backstory dropped while keeping all hard rules intact.

### 4b. Content Truncation

| Mode | Before | After | Saved per article |
|------|--------|-------|-------------------|
| Batch | 3,000 chars | 2,000 chars | ~125 tokens |
| Individual | 4,000 chars | 2,500 chars | ~187 tokens |

Article body text truncated to these limits before being sent to the LLM. The
original values were conservative; experience showed that clickbait detection
is reliable with less content.

### 4c. User Prompt Framing

Header/footer/separator text around the article content was shortened:

| Component | Before (tokens) | After (tokens) | Saved |
|-----------|----------------|----------------|-------|
| Batch header | 12 | 9 | 3 |
| Batch footer | 11 | 8 | 3 |
| Individual prompt | 16 | 6 | 10 |

### 4d. Batch Processing

Multiple articles are sent in a single LLM call (batch size 10 by default)
instead of one API call per article. This:

- Shares HTTP overhead (connection, TLS, headers) across articles.
- Reduces the number of API calls from `N` to `ceil(N/10)`.
- The `max_tokens` per batch scales with batch size: `512 * len(uncached)`,
  so the model has enough room to respond.

### 4e. Per-Run Impact (before vs after)

**Scenario:** 58 fresh articles (6 batches of 10) + 1 daily note call.

| Component | Before (tokens) | After (tokens) | Saved |
|-----------|----------------|----------------|-------|
| System prompts (6×) | 10,578 | 2,964 | 7,614 |
| User prompts (6× batches) | 19,800 | 13,794 | 6,006 |
| Note call | 429 | 203 | 226 |
| **Per-run total** | **34,593** | **19,215** | **15,378** |

**Overall reduction: 44%** against a 20% target. On top of that, the seen cache
and LLM cache further reduce actual API calls in subsequent runs.

### 4f. Optimisation Surface

Files that contain prompt text (the optimisation targets):

| File | What's there |
|------|-------------|
| `clickbust/rewriter.py` | `SYSTEM_PROMPT`, `BATCH_SYSTEM_PROMPT`, `_build_prompt()`, `_batch_prompt()` |
| `clickbust/notes.py` | `VOICE_SYSTEM_PROMPT`, `_build_prompt()` |

---

## 5. Cost Tracking

Clickbust does **not** have built-in monetary cost tracking per LLM call. The
OpenAI-compatible API responses don't return token counts by default for all
providers, and the codebase doesn't parse them when present.

What **is** tracked:

| Data | Where | File |
|------|-------|------|
| Per-site clickbait counts (per run + cumulative) | `output/stats.json` | `stats.py` |
| LLM cache hit/miss logs | Standard output | `llm_cache.py` (log lines) |
| Articles fetched vs reused from seen cache | Standard output + CLI summary | `cli.py` |

The `stats.json` file records:

```json
{
  "ScreenRant": {
    "total_clickbait": 142,
    "total_runs": 34,
    "average_daily": 4.2,
    "runs": {
      "2026-07-08": 5,
      "2026-07-09": 3,
      "2026-07-10": 4
    },
    "last_updated": "2026-07-10T06:13:00+00:00"
  }
}
```

**To add monetary tracking,** parse the `usage` object from the API response
(available from most OpenAI-compatible providers) and accumulate it in a new
file, e.g. `output/costs.json`. The relevant call sites are:

- `rewriter.py` line 128: `result = resp.json()` — contains `result["usage"]`
- `notes.py` line 139: same structure for daily notes

Both are OpenAI chat-completions format responses which include `prompt_tokens`,
`completion_tokens`, and `total_tokens`.

---

## 6. Maintenance Guide

### When to adjust cache TTLs

- **LLM cache (1h):** Lengthen (e.g. 4h) if you have many syndicated articles
  across different URLs that persist for hours. Shorten (e.g. 15min) if
  headlines are frequently updated at the source.
- **Seen cache (7d):** Increase if your cron runs less than daily. Decrease if
  the JSON file grows large (>10 MB, though unlikely at current article volumes).
- **Archive (500):** Increase if you want a deeper back-catalog on the site.
  Decrease if the site generation takes too long or the `articles.json` file
  size is a deploy concern.

### When to re-optimise prompts

Signs that prompts need attention:

- **Token usage has drifted up** — check per-run log output for the "LLM calls"
  count and compare to the 19,215 baseline. New features often add prompt text.
- **Clickbait detection quality dropped** — adding more rules may help, but be
  disciplined: each added rule costs tokens. Prefer clarifying existing rules
  over adding new ones.
- **New prompt files were added** — any new feature that calls the LLM should
  use the same compression techniques: rule-based system prompts, minimum
  examples, single-line output format.

### How to measure impact

1. **Check the per-run CLI summary** — it shows articles fetched, fresh vs
   cached, and LLM calls made.
2. **Look at the cache files** in `output/`:
   - `seen_articles.json` — how many articles are in the seen cache.
   - `llm_cache.json` — how many unique prompts are cached.
3. **Count actual API calls** by grepping logs: `grep "Clickbait detected\|Not clickbait"` for LLM results vs `grep "Cached (LLM)"` for cache hits.
4. **Benchmark prompt size** using a tokeniser (e.g. `tiktoken` for OpenAI
   models):
   ```python
   import tiktoken
   enc = tiktoken.get_encoding("cl100k_base")
   tokens = len(enc.encode(SYSTEM_PROMPT))
   ```

### Adding a new cache layer

Clickbust's architecture keeps caches as flat JSON files in the output
directory. If you need a new cache:

1. Create a new module (e.g. `clickbust/my_cache.py`) with `load_*` and
   `save_*` functions.
2. Pick a key (URL, content hash, date) and a TTL/pruning strategy.
3. Wire it into the pipeline in `cli.py` — caches should be checked early and
   written late.
4. Add a log line showing hit/miss count so operators can monitor effectiveness.

**Don't** add a database dependency just for caching — flat JSON files are
fine for the volumes Clickbust deals with (<1,000 articles, <10 MB for all
caches combined).

### Gotchas

- **TTL is applied lazily** in the LLM cache. It won't shrink on disk until
  `prune()` is called (or `get()` finds a stale entry). The cache file may
  accumulate expired entries across many short-lived containers. Running
  `clickbust run` calls `llm_cache.prune()` implicitly through `get()` checks.
- **Seen cache and LLM cache are independent.** An article can be a seen-cache
  MISS (new URL) but an LLM cache HIT (same prompt text as another article).
  This is correct behaviour — the LLM cache saves the API call, the seen cache
  saves the content extraction.
- **Archive eviction is by published date, not insertion order.** If an article
  has no `published_date` it sorts to the bottom and is evicted first.
- **Logs are the best monitoring tool.** Cache hits are logged at INFO level
  with descriptive prefixes: `"Seen cache: N articles reused"`,
  `"LLM cache HIT"`, `"Cached (LLM)"`. Run with `-v` for per-article detail.