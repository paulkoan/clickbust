# Clickbust — Caching & Token Optimisation

This document describes the caching architecture, prompt optimisation strategies, and
cost-tracking data for the Clickbust pipeline. It's intended for maintainers and
contributors who need to understand, extend, or troubleshoot the performance
optimisations.

## Table of Contents

- [Caching Architecture](#caching-architecture)
  - [Layer 1: seen_cache (URL + headline hash)](#layer-1-seen_cache-url--headline-hash)
  - [Layer 2: LLM response cache (content-addressed)](#layer-2-llm-response-cache-content-addressed)
  - [Layer 3: Article archive (metadata persistence)](#layer-3-article-archive-metadata-persistence)
  - [Cache Interaction Flow](#cache-interaction-flow)
- [TTL & Invalidation Rules](#ttl--invalidation-rules)
- [Prompt Optimisation](#prompt-optimisation)
  - [What Changed](#what-changed)
  - [Before / After Token Counts](#before--after-token-counts)
  - [Per-Run Impact](#per-run-impact)
- [Cost Tracking](#cost-tracking)
- [Maintenance Guide](#maintenance-guide)
  - [Modifying Prompt Templates](#modifying-prompt-templates)
  - [Modifying the LLM Cache](#modifying-the-llm-cache)
  - [Modifying the Seen Cache](#modifying-the-seen-cache)
  - [Adding a New Cache Layer](#adding-a-new-cache-layer)
  - [Running Tests](#running-tests)
  - [Monitoring](#monitoring)

---

## Caching Architecture

Clickbust uses **three cache layers**, each with a different purpose and scope. They
stack: the pipeline checks each layer in order, and only falls through to an LLM API
call when all caches miss.

```
┌─────────────────────────────────────────────────────────┐
│                    Pipeline Flow                         │
│                                                          │
│  RSS Feed ──→ seen_cache ──→ Content Extraction          │
│                                    │                     │
│                                    ▼                     │
│                         llm_cache ──→ LLM API            │
│                                    │                     │
│                                    ▼                     │
│                          Article Archive                 │
│                                    │                     │
│                                    ▼                     │
│                           Static Site                    │
└─────────────────────────────────────────────────────────┘
```

### Layer 1: seen_cache (URL + headline hash)

**File:** `clickbust/seen_cache.py`
**Cache file:** `output/seen_articles.json`
**Purpose:** Avoid re-processing articles whose URL **and** headline haven't changed
since the last run.

How it works:

- Each article URL maps to a `{headline_hash, is_clickbait, rewritten_title, summary,
  image_url, last_seen}` entry.
- `is_unchanged(cache, url, headline)` checks if the URL exists and its headline hash
  matches. If yes, the previous LLM result is reused directly.
- Content extraction and LLM calls are **both skipped** for seen-unchanged articles.
- **Cache hit rate:** ~42% on a typical daily run (varies with how much content changes).

**Scope:** Per-URL, across runs. Survives indefinitely within the 7-day TTL window.

### Layer 2: LLM response cache (content-addressed)

**File:** `clickbust/llm_cache.py`
**Cache file:** `output/llm_cache.json`
**Purpose:** Avoid duplicate LLM API calls for identical prompts, even across different
URLs (syndicated content) or within the same run (error recovery, repeated `clickbust
check` commands).

How it works:

- Each cache entry is keyed by `SHA256(model || system_prompt || user_prompt)`.
- On `get(system_prompt, user_prompt, model)`: if the key exists and hasn't expired
  (lazy TTL check), returns the cached response dict.
- On `set(system_prompt, user_prompt, model, response)`: stores the response with a
  `cached_at` and `expires_at` timestamp.
- `prune()`: removes all expired entries (called lazily on save, or explicitly).
- Thread-safe: uses `threading.Lock` for all reads and writes.
- Graceful degradation: corrupt JSON files, missing files, and write failures all
  log warnings and start fresh — the pipeline never crashes from a cache error.

**What this catches that seen_cache doesn't:**

| Scenario | seen_cache | llm_cache |
|---|---|---|
| Same URL, unchanged headline | ✅ Hit | ✅ Hit |
| Different URLs, same content (syndication) | ❌ Miss (different URL) | ✅ Hit (same prompt hash) |
| `clickbust check` repeated on same article | ❌ Doesn't use seen_cache | ✅ Hit on content hash |
| Note generation with same topic+context | ❌ No seen_cache | ✅ Hit on prompt hash |
| Same article, slightly different headline | ❌ Miss (different hash) | ❌ Miss (different prompt) |

**Scope:** Content-addressed, across runs. TTL-bound (1 hour default).

### Layer 3: Article archive (metadata persistence)

**File:** `clickbust/archive.py`
**Cache file:** `output/articles.json`
**Purpose:** Keep historical article metadata between runs so the site grows naturally
without re-fetching or re-LLM-ing old articles.

How it works:

- Serialized article metadata (title, url, summary, rewritten title, image, dates)
  keyed by URL.
- On each run, fresh RSS articles are merged with the archive. Existing articles
  keep their previous LLM result; new ones get processed.
- Capped at 500 entries (`MAX_ARCHIVED`).
- **Zero token cost** — only metadata is stored and re-rendered.

**Scope:** Per-URL, persistent across runs until the 500-entry cap is reached.

### Cache Interaction Flow

```
For each article from RSS:

  1. seen_cache.is_unchanged(url, headline)?
     ├── YES → reuse result (skip content extraction, skip LLM)
     └── NO  → fetch article content
               │
               ▼
  2. llm_cache.get(system_prompt, user_prompt, model)?
     ├── YES → return cached result (skip LLM API call)
     └── NO  → call LLM API → store result in llm_cache
               │
               ▼
  3. Store result in seen_cache (for future runs)
     Store result in archive (for site rendering)
```

---

## TTL & Invalidation Rules

| Cache | TTL | Invalidation | File |
|---|---|---|---|
| **seen_cache** | 7 days (pruned on `prune_stale()`) | Headline change → new hash → cache miss. Entries not seen in 7 days are pruned. | `seen_articles.json` |
| **llm_cache** | 1 hour (configurable, default 3600s) | Lazy — checked on `get()`, pruned on `save()`. Different content → different prompt hash → miss. | `llm_cache.json` |
| **Archive** | Indefinite (limited to 500 entries) | FIFO — oldest entries are evicted when the cap is reached. | `articles.json` |

**Why different TTLs?**

- **seen_cache (7 days):** Reflects the article lifecycle. Most articles are stale
  after a week. A 7-day window covers the gap between cron runs (the scheduler runs
  daily) plus a buffer for manual re-runs.
- **llm_cache (1 hour):** Catches intra-run duplicates (batch retries, repeated
  `clickbust check` commands, cron + manual overlap) without going stale on the same
  day's content. A shorter TTL would miss the cron+manual pattern; a longer one
  would cache yesterday's headlines.

**To change TTLs:**

- `seen_cache.py`: edit `MAX_AGE_DAYS` (line 12)
- `llm_cache.py`: pass `ttl_seconds` to `LLMCache()` constructor (default 3600).
  The CLI passes `3600` — change in `cli.py` `cmd_run()`, `cmd_check()`, `cmd_note()`

---

## Prompt Optimisation

All three prompt templates were rewritten to reduce token consumption while preserving
analysis quality and output format.

### What Changed

| Prompt | Before (tokens) | After (tokens) | Reduction |
|---|---|---|---|
| `SYSTEM_PROMPT` (headline analysis) | 1,685 | 447 | **73%** |
| `BATCH_SYSTEM_PROMPT` | 1,763 | 494 | **72%** |
| `VOICE_SYSTEM_PROMPT` (daily notes) | 404 | 178 | **56%** |

**Techniques used:**

1. **Merged redundant categories** — 12 verbose clickbait rules collapsed into 7
   concise numbered rules. "Description bait", "platform bait", and "person archetype
   bait" became one unified "Withholds the named subject" rule.
2. **Removed duplicate examples** — kept 1 example per rule, removed the rest.
3. **Removed redundant sections** — the old prompt had a "Critical rule" section that
   restated earlier rules. Deleted.
4. **Compressed NOT-clickbait section** — 5 bullet points → 1 sentence.
5. **Shortened output format** — multiline JSON template → single-line.
6. **Reduced article content truncation** — 4,000 → 2,500 chars (individual mode),
   3,000 → 2,000 chars (batch mode).
7. **Shortened user prompt framing** — header and footer text trimmed.

### Per-Run Impact

**Scenario:** 58 fresh articles (6 batches of 10) + 1 daily note call

| Component | Before (tokens) | After (tokens) | Saved |
|---|---|---|---|
| System prompts (6×) | 10,578 | 2,964 | 7,614 |
| User prompts (6× batches) | 19,800 | 13,794 | 6,006 |
| Note call | 429 | 203 | 226 |
| **Total** | **34,593** | **19,215** | **15,378** |

**Overall reduction: 44%** (target was 20%).

### Files Modified

| File | Change |
|---|---|
| `clickbust/rewriter.py` | `SYSTEM_PROMPT`, `BATCH_SYSTEM_PROMPT`, `_build_prompt()`, `_batch_prompt()` |
| `clickbust/notes.py` | `VOICE_SYSTEM_PROMPT`, `_build_prompt()` |

---

## Cost Tracking

**Estimated cost per run: ~$0.018 USD** (at OpenRouter rates for `gpt-4o-mini`).

After the LLM response cache, the 44% prompt reduction, and the ~42% seen_cache hit
rate, a typical daily run makes:

- **~6 LLM batch calls** (58 fresh articles, batched ×10)
- **~1 LLM note call** (daily note generation)
- **~7 total LLM API calls** per run

Without caching and prompt optimisation, the same run would make:

- **~58 LLM individual calls** (no batching)
- **~34,593 input tokens** (vs 19,215 after optimisation)
- **~16,160 output tokens** (unchanged — output is controlled by `max_tokens`)
- **~$0.032 USD** per run (higher before optimisation)

**To track actual costs:**

The `stats.json` file in the output directory tracks per-site clickbait detection
counts and running totals. The `clickbust run` summary prints the LLM call count and
cache entry count. For precise dollar figures, check your LLM provider's billing
dashboard (OpenRouter, OpenAI, etc).

---

## Maintenance Guide

### Modifying Prompt Templates

Prompts live in `clickbust/rewriter.py` (`SYSTEM_PROMPT`, `BATCH_SYSTEM_PROMPT`) and
`clickbust/notes.py` (`VOICE_SYSTEM_PROMPT`).

**Rules when editing:**

1. **Keep the output JSON format stable.** Both `rewrite_headline()` and
   `_rewrite_batch()` parse the LLM response as `{"is_clickbait": bool,
   "new_headline": str | null}`. Changing the output shape breaks the pipeline.
2. **Test with a single article first.** Use `clickbust check <url>` to verify the
   new prompt produces sensible output before running the full pipeline.
3. **Update the prompt-optimisation report** if you make significant changes.
   Document the token counts before and after in `docs/optimisation.md`.
4. **Do not expand the prompt without a clear reason.** Every token costs money.
   If you need to add a new clickbait category, consider whether it fits under an
   existing rule first.

### Modifying the LLM Cache

The `LLMCache` class is in `clickbust/llm_cache.py`. It's a simple file-based cache:

- **Changing TTL:** Pass a different `ttl_seconds` to the constructor. The default
  is 3600 (1 hour). The CLI injects `LLMCache(output_dir, ttl_seconds=3600)` in
  `cmd_run()`, `cmd_check()`, and `cmd_note()`.
- **Changing storage format:** The cache stores JSON. If you add new fields to the
  entry dict, existing cache files from previous runs will be loaded without those
  fields — the code handles this gracefully (missing fields cause a cache miss).
- **Adding a backend (Redis, SQLite, etc.):** The `LLMCache` interface is:
  - `get(system_prompt, user_prompt, model) → dict | None`
  - `set(system_prompt, user_prompt, model, response) → None`
  - `prune() → int (count pruned)`
  
  Implement these three methods on your new backend class and swap the constructor
  call in `cli.py`.

### Modifying the Seen Cache

The seen cache is a module-level set of functions in `clickbust/seen_cache.py`, not a
class. It uses JSON on disk:

- **Changing TTL:** Edit `MAX_AGE_DAYS` (line 12).
- **Adding new fields to entries:** Update `mark_processed()` to include the new
  field. Old entries without the field will get the default value when accessed
  via `get_cached_result()`.
- **Changing the key:** Currently keyed by URL. If you change this, existing cache
  files become invalid — delete `seen_articles.json` and start fresh.

### Adding a New Cache Layer

If you identify a new caching opportunity (e.g., HTTP-level conditional GET for RSS
feeds or article content), follow these guidelines:

1. **Pick the right scope:** URL-based (like seen_cache), content-based (like
   llm_cache), or time-based (like archive).
2. **Pick the right TTL:** Match the refresh cycle of the data. RSS feeds change
   hourly — use a short TTL. Article content changes rarely — use a longer one.
3. **Fail gracefully:** Cache corruption, missing files, and write errors should
   never crash the pipeline. Log a warning and continue.
4. **Add to the flow diagram** in this document.
5. **Document the expected hit rate** after a few runs.

### Running Tests

Unit tests live in the `tests/` directory. To run them:

```bash
cd clickbust
uv sync --dev
uv run pytest -v
```

**LLM cache tests** (`tests/test_llm_cache.py`) — 8 tests covering:

- `get()` / `set()` round-trip
- Cache miss on different prompts and models
- Persistence across reloads
- TTL expiry
- Pruning stale entries
- Thread safety (concurrent access from 8 threads)
- Graceful corruption handling
- Auto-creating nested directories

**Prompt tests** (`tests/test_prompts.py`) — 11 tests covering:

- Prompt sizes within token bounds
- Individual prompt structure (headline, content, instruction order)
- Batch prompt structure (header, articles, footer)
- Content truncation limits (2,500 chars individual, 2,000 chars batch)
- Fallback to summary when content is empty
- All critical sections present in prompts
- Note prompt topic + context handling

### Monitoring

**Check cache health:**

```bash
# Run the pipeline with verbose logging
clickbust -v run

# Look for these log lines:
# "Seen cache: N articles reused, M need fresh processing"
# "LLM cache HIT" / "LLM cache STORED"
# "LLM cache pruned N stale entries"
```

**Expected cache hit rate:**

- **seen_cache:** ~42% on a daily run (higher on consecutive runs with the same
  articles, lower after weekends).
- **llm_cache:** ~5-15% depends on how much syndicated content appears (same story
  at different URLs). The cache is most valuable for intra-run dedup and manual
  `clickbust check` commands.

**If hit rates are unexpectedly low:**

- Check the TTL hasn't been set too short for your use case.
- Verify the cache files exist in the output directory.
- Check for content changes that are producing different prompts (different
  headlines, different article text).