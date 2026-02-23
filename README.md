# Big Idea Generator (Opportunity Radar)

Fetches news from multiple sources, sends them to Claude for grouped business-opportunity analysis, and delivers a formatted digest via email. Opportunities are scored on feasibility, demand, and uniqueness, then grouped by domain (AI/ML, Dev Tools, Food, Health, Real Estate, Finance, E-commerce, Compliance, EdTech). Digests are stored in **Neon PostgreSQL** and the web dashboard is rebuilt automatically via **GitHub Actions + Netlify**.

## How it works

```
NewsAPI (top-headlines + everything)  ─┐
Hacker News (top 30 stories)          ─┤
RSS feeds (general + domain-specific) ─┤  Bucket by group  ─▶  Claude (4 grouped calls, prompt-cached)  ─▶  HTML email
Reddit (general + domain subreddits)  ─┤                                                                  ─▶  Neon DB
Federal Register API                  ─┤                                                                  ─▶  Netlify rebuild
Google Trends                         ─┤
GitHub Trending                       ─┘
```

1. **Fetch** — pulls articles from 9 source types, deduplicates by normalized URL and title similarity (>80%).
2. **Prepare** — buckets articles into 4 groups using `domain_hint` tags from RSS/Reddit, supplementing each group with general news if under 10 items.
3. **Analyse** — sends each group's articles to Claude in parallel (2 workers). Two groups use Sonnet (tech, lifestyle), two use Haiku (commerce, civic). The static system prompt is shared across all 4 calls with `cache_control: ephemeral` to reduce token costs.
4. **Deduplicate** — merges similar ideas across groups (>70% title similarity), combining domain tags.
5. **Save to DB** — upserts opportunities, per-group slices, token usage, and news context into Neon PostgreSQL.
6. **Deliver** — renders a styled HTML email with plain-text fallback and sends via SMTP/TLS.
7. **Rebuild dashboard** — GitHub Actions triggers a Netlify build which queries Neon and regenerates the static frontend.

## Quickstart

```bash
# Clone and install
git clone <repo-url> && cd big-idea-generator
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys, SMTP credentials, and NEON_DATABASE_URL

# Run
python main.py                              # fetch, analyse, save to DB, email
python main.py --dry-run                    # print digest to console, skip email
python main.py --domains tech,civic         # specific groups only
python main.py --resend                     # re-send the latest stored digest
python main.py --resend 2026-02-15          # re-send a specific date's digest
python main.py --resend --dry-run           # preview latest without emailing
python main.py --expand 'RentFlow'          # expand a slim idea to full schema
python main.py --expand 3                   # expand idea by rank number
python main.py --cost                       # show 7-day cost summary
```

## Configuration

All settings are loaded from environment variables (or a `.env` file). See `.env.example` for the full list:

| Variable | Required | Description |
|---|---|---|
| `NEWS_API_KEY` | yes | NewsAPI key ([newsapi.org](https://newsapi.org)) |
| `ANTHROPIC_API_KEY` | yes | Anthropic API key ([console.anthropic.com](https://console.anthropic.com)) |
| `NEON_DATABASE_URL` | yes | Neon PostgreSQL connection string ([neon.tech](https://neon.tech)) |
| `SMTP_USER` | yes | SMTP login / From address |
| `SMTP_PASSWORD` | yes | SMTP password (use an app password for Gmail) |
| `DIGEST_RECIPIENT` | yes | Email address to receive the digest |
| `SMTP_HOST` | no | SMTP server (default: `smtp.gmail.com`) |
| `SMTP_PORT` | no | SMTP port (default: `587`) |
| `NEWS_QUERY` | no | Search query for NewsAPI `/v2/everything` (default: `technology startup funding`) |
| `NEWS_PAGE_SIZE` | no | Articles per NewsAPI request (default: `10`) |
| `REDDIT_CLIENT_ID` | no | Reddit OAuth client ID (skipped if not set) |
| `REDDIT_CLIENT_SECRET` | no | Reddit OAuth client secret |
| `REDDIT_USERNAME` | no | Reddit username |
| `REDDIT_PASSWORD` | no | Reddit password |

### Domain groups

Nine domains are organized into 4 groups in `config.py`. Each group is analyzed in a single Claude call:

| Group | Model | Domains |
|---|---|---|
| `tech` 🤖 | Sonnet | `ai_ml`, `dev_tools` |
| `lifestyle` 🌿 | Sonnet | `food_hospitality`, `health_wellness`, `real_estate_housing` |
| `commerce` 💼 | Haiku | `finance_personal_finance`, `ecommerce_retail` |
| `civic` 🏛️ | Haiku | `government_compliance`, `education_edtech` |

Each domain has dedicated RSS feeds and subreddits in `DOMAIN_NEWS_SOURCES`. Use `--domains tech,civic` to run only specific groups, or a domain ID like `--domains ai_ml` to run just the group containing that domain.

## Database & Scheduling Setup

### 1. Create a Neon project

1. Go to [neon.tech](https://neon.tech) and create a free account.
2. Create a new project (any name, region `us-east-2` recommended).
3. Copy the connection string from the project dashboard — it looks like `postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require`.
4. Add it to your `.env` as `NEON_DATABASE_URL=...`.

The database schema (a single `digests` table) is created automatically on first run.

### 2. Migrate existing digests (if you have a `digests/` folder)

```bash
python migrate_digests_to_db.py
```

Then remove the directory from git once you've verified:

```bash
git rm -r digests/
echo "digests/" >> .gitignore
git add .gitignore
git commit -m "chore: remove digests/ directory, now stored in Neon PostgreSQL"
```

### 3. Set up GitHub Actions for daily scheduling

The pipeline runs automatically at **10:00 AM Eastern (15:00 UTC)** via `.github/workflows/daily.yml`.

Add these secrets in your GitHub repo (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic key |
| `NEWS_API_KEY` | Your NewsAPI key |
| `SMTP_USER` | SMTP login email |
| `SMTP_PASSWORD` | SMTP app password |
| `DIGEST_RECIPIENT` | Recipient email |
| `NEON_DATABASE_URL` | Your Neon connection string |
| `NETLIFY_BUILD_HOOK_URL` | From Netlify: Site Settings → Build & Deploy → Build hooks → Add |
| `REDDIT_CLIENT_ID` | (optional) |
| `REDDIT_CLIENT_SECRET` | (optional) |
| `REDDIT_USERNAME` | (optional) |
| `REDDIT_PASSWORD` | (optional) |

**Timezone note:** `0 15 * * *` = 10 AM EST (winter). During EDT (summer, UTC-4) this runs at 11 AM. Change to `0 14 * * *` in `.github/workflows/daily.yml` if you prefer 10 AM EDT year-round.

After each run, GitHub Actions commits `data/previous_ideas.json` (the dedup memory) back to the repo so it persists across ephemeral runner environments.

### 4. Add `NEON_DATABASE_URL` to Netlify

In Netlify: Site Settings → Environment variables → Add variable → `NEON_DATABASE_URL`.

The Netlify build command (`pip install psycopg2-binary && python build_manifest.py`) queries Neon to regenerate all static digest JSON files at deploy time.

## Project structure

```
big-idea-generator/
  main.py              CLI entry point, wires everything together
  config.py            Loads env vars, defines DOMAIN_GROUPS and DOMAIN_NEWS_SOURCES
  db.py                Neon PostgreSQL persistence (save, load, list digests)
  news_fetcher.py      9 news source fetchers + domain bucketing
  analyzer.py          Claude-powered opportunity analysis (grouped + prompt caching)
  digest_sender.py     HTML email builder and SMTP sender
  build_manifest.py    Queries Neon DB and generates static JSON files for frontend/
  migrate_digests_to_db.py  One-time migration script (digests/ → Neon)
  frontend/index.html  Vue 3 dashboard (single-file, no build step)
  netlify.toml         Netlify deployment config
  .github/workflows/daily.yml  GitHub Actions daily schedule
  requirements.txt     Python dependencies
  .env.example         Template for environment variables
  data/                DailyMemory storage (auto-created, committed to git by CI)
```

## Module reference

### `config.py`

Loads all environment variables at import time via `python-dotenv`. Required vars raise `KeyError` if missing.

| Export | Type | Description |
|---|---|---|
| `NEWS_API_KEY` | `str` | NewsAPI key |
| `ANTHROPIC_API_KEY` | `str` | Anthropic API key |
| `NEON_DATABASE_URL` | `str` | Neon PostgreSQL connection string |
| `SMTP_HOST`, `SMTP_PORT` | `str`, `int` | SMTP server settings |
| `SMTP_USER`, `SMTP_PASSWORD` | `str` | SMTP credentials |
| `DIGEST_RECIPIENT` | `str` | Email recipient |
| `NEWS_QUERY`, `NEWS_PAGE_SIZE` | `str`, `int` | NewsAPI settings |
| `REDDIT_CLIENT_ID`, etc. | `str \| None` | Reddit OAuth (optional) |
| `DOMAIN_GROUPS` | `list[dict]` | 4 group configs (group_id, group_name, model, icon, min_ideas, max_ideas, domains) |
| `DOMAINS` | `list[dict]` | Flattened list of all 9 domain dicts (backward-compatible) |
| `DOMAIN_ICONS` | `dict[str, str]` | Per-domain emoji map |
| `DOMAIN_NEWS_SOURCES` | `dict` | Per-domain RSS feeds and subreddits |

---

### `db.py`

Neon PostgreSQL persistence. Uses a single `digests` table with `(run_date, artifact)` primary key. Schema is auto-created on first connection.

Artifact values: `'all'` (full list), group IDs (`'tech'`, `'lifestyle'`, `'commerce'`, `'civic'`), `'usage'` (token/cost data), `'news'` (news context for `--expand`).

| Function | Signature | Description |
|---|---|---|
| `save_digest` | `(run_date, opportunities, domain_groups=None, group_usages=None, news_by_group=None)` | Upserts all artifacts for a run. Non-fatal per artifact. |
| `load_digest` | `(run_date, artifact='all') → list\|dict\|None` | Returns parsed JSONB data or None. |
| `get_latest_run_date` | `() → str\|None` | Returns most recent run_date as ISO string. |
| `list_all_digests` | `() → list[tuple[str, list]]` | All 'all' rows, newest first. Used by `build_manifest.py`. |
| `list_usage_history` | `(days=7) → list[tuple[str, dict]]` | Usage rows for last N days. Used by `--cost`. |
| `close` | `() → None` | Closes the cached connection. |

---

### `news_fetcher.py`

Fetches and normalizes news from 9 source types. All HTTP goes through `_request_with_retry()` with exponential backoff for 429/5xx/connection errors. Optional sources gracefully degrade if dependencies aren't installed.

| Function | Description |
|---|---|
| `fetch_all_news()` | Calls all 9 fetchers, combines, deduplicates. Returns normalized articles. |
| `prepare_news_for_analysis(news_items, domain_groups)` | Buckets articles into per-group lists using `domain_hint`. |

#### Normalized article schema

```json
{
  "title": "...", "description": "...", "source": "Hacker News",
  "url": "https://...", "published_at": "2026-02-15T12:00:00+00:00",
  "category": "hacker_news", "domain_hint": "education_edtech", "metadata": {}
}
```

---

### `analyzer.py`

Sends articles to Claude for opportunity analysis. Supports three modes: **grouped** (primary, 4 parallel calls with prompt caching), **domain-aware** (legacy, 7 parallel calls), and **legacy** (single call, no domains).

| Function | Signature | Description |
|---|---|---|
| `analyze_opportunities` | `(news_items, domains=None) → list[dict]` | Main entry point. Detects mode by input shape. |
| `expand_idea` | `(idea, news_context) → dict` | Expands a slim idea into full schema using Haiku. |
| `get_token_usage` | `() → dict[str, int]` | Cumulative token counts for the session. |
| `get_group_usages` | `() → list[dict]` | Per-group token usage records (model, tokens, cache stats). |

#### Slim opportunity schema (grouped mode)

```json
{
  "rank": 1, "name": "...", "one_liner": "...",
  "domains": ["ai_ml", "dev_tools"], "primary_domain": "ai_ml", "group_id": "tech",
  "the_problem": "...", "revenue_model": "...",
  "competitive_landscape": "...", "complexity": "low | medium | high", "growth_hook": "..."
}
```

Full schema fields (`news_trigger`, `target_audience`, `product_description`, `market_signal`, `estimated_build_time`, `tech_stack`, `build_plan`, `risks_and_challenges`) are populated on demand via `--expand`.

---

### `digest_sender.py`

Builds and sends opportunity digest emails over SMTP/TLS.

| Function | Description |
|---|---|
| `send_opportunities_digest(opportunities, domains, news_count, usage_summary)` | Sends multipart email (HTML + plain-text fallback). |
| `_build_plain_text(opportunities, domains)` | Plain-text renderer, also used by `--dry-run`. |

---

### `main.py`

CLI entry point. Orchestrates the pipeline.

| Flag | Description |
|---|---|
| `--dry-run` | Print digest to console, skip email |
| `--resend [DATE]` | Re-send a stored digest from DB. Pass `latest` (default) or `YYYY-MM-DD`. |
| `--domains IDS` | Comma-separated group IDs or domain IDs to filter (e.g. `tech,civic` or `ai_ml`) |
| `--expand IDEA` | Expand a slim idea from the latest digest. Pass name (fuzzy) or rank number. |
| `--cost` | Print 7-day cost summary from DB usage records |

---

### `build_manifest.py`

Queries Neon DB for all digests and writes static JSON files into `frontend/digests/` for the web dashboard. Runs at Netlify build time.

#### Manifest schema

```json
{
  "digests": [{ "date": "2026-02-15", "count": 7 }],
  "domains": [{ "id": "ai_ml", "name": "AI / Machine Learning", "icon": "🤖" }],
  "stats": {
    "total_digests": 5, "total_opportunities": 35, "avg_score": 6.2,
    "complexity_breakdown": { "medium": 28, "low": 5, "high": 2 },
    "domain_breakdown": { "ai_ml": 8, "dev_tools": 6 },
    "date_range": { "earliest": "2026-02-17", "latest": "2026-02-23" }
  }
}
```

## Web Dashboard

A read-only Vue 3 dashboard for browsing saved digests. Supports domain filtering, complexity filtering, search, and detail modals.

### Local dev

```bash
# Generate manifest from DB
python build_manifest.py

# Serve the frontend
python -m http.server 5500 -d frontend
```

Open http://localhost:5500.

### Netlify deployment

1. Push the repo to GitHub.
2. Connect the repo in Netlify. The `netlify.toml` runs `pip install psycopg2-binary && python build_manifest.py` as the build command and publishes `frontend/`.
3. Add `NEON_DATABASE_URL` to Netlify environment variables.
4. Each time GitHub Actions runs the pipeline, it triggers a Netlify rebuild via build hook — the dashboard updates automatically.
