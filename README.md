# Big Idea Generator

Fetches news from multiple sources, sends them to Claude for domain-categorised business-opportunity analysis, and delivers a formatted digest via email. Opportunities are scored on feasibility, demand, and uniqueness, then grouped by domain (Education, Real Estate, Government, Food, Health, Finance, E-commerce).

## How it works

```
NewsAPI (top-headlines + everything)  ─┐
Hacker News (top 30 stories)          ─┤
RSS feeds (general + domain-specific) ─┤
Reddit (general + domain subreddits)  ─┼─▶  Prepare by domain  ─▶  Claude (per-domain analysis, parallel)  ─▶  Re-score  ─▶  HTML email
Federal Register API                  ─┤
Google Trends                         ─┤
GitHub Trending                       ─┘
```

1. **Fetch** — pulls articles from 9 source types, deduplicates by normalized URL and title similarity (>80%).
2. **Prepare** — buckets articles into 7 domains using `domain_hint` tags from RSS/Reddit sources, supplementing each domain with general news if under 10 items.
3. **Analyse** — sends each domain's articles to Claude in parallel (3 workers), requesting 3–5 structured opportunities per domain.
4. **Deduplicate** — merges similar ideas across domains (>70% title similarity), combining domain tags.
5. **Re-score** — a second Claude call independently rates each opportunity on feasibility, demand confidence, and uniqueness (1–10 each).
6. **Deliver** — renders the scored, domain-grouped opportunities as a styled HTML email with plain-text fallback and sends via SMTP/TLS.

## Quickstart

```bash
# Clone and install
git clone <repo-url> && cd big-idea-generator
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys and SMTP credentials

# Run
python main.py                      # fetch, analyse, email (all 7 domains)
python main.py --dry-run             # print digest to console, skip email
python main.py --save                # also write JSON to digests/<date>/
python main.py --dry-run --save      # both
python main.py --domains education_edtech,food_hospitality   # specific domains only
python main.py --resend              # re-send the latest saved digest
python main.py --resend 2026-02-15   # re-send a specific date's digest
```

## Configuration

All settings are loaded from environment variables (or a `.env` file). See `.env.example` for the full list:

| Variable | Required | Description |
|---|---|---|
| `NEWS_API_KEY` | yes | NewsAPI key ([newsapi.org](https://newsapi.org)) |
| `ANTHROPIC_API_KEY` | yes | Anthropic API key ([console.anthropic.com](https://console.anthropic.com)) |
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

### Domains

Seven business domains are configured in `config.py`:

| ID | Name | Icon |
|---|---|---|
| `education_edtech` | Education / Courses / EdTech | 📚 |
| `real_estate_housing` | Real Estate / Housing | 🏠 |
| `government_compliance` | Government / Compliance / Policy | 🏛️ |
| `food_hospitality` | Food / Cooking / Hospitality | 🍳 |
| `health_wellness` | Health / Wellness / Fitness | 💪 |
| `finance_personal_finance` | Finance / Personal Finance | 💰 |
| `ecommerce_retail` | E-commerce / Retail | 🛒 |

Each domain has dedicated RSS feeds and subreddits defined in `DOMAIN_NEWS_SOURCES`. These are fetched alongside the general sources and tagged with a `domain_hint` field so the analyzer knows which domain the article is most relevant to.

## Project structure

```
big-idea-generator/
  main.py              CLI entry point, wires everything together
  config.py            Loads environment variables, defines DOMAINS and DOMAIN_NEWS_SOURCES
  news_fetcher.py      9 news source fetchers + domain bucketing
  analyzer.py          Claude-powered opportunity analysis (per-domain + re-scoring)
  digest_sender.py     HTML email builder and SMTP sender
  build_manifest.py    Copies digests into frontend/ and generates manifest.json
  frontend/index.html  Vue 3 dashboard (single-file, no build step)
  netlify.toml         Netlify deployment config
  requirements.txt     Python dependencies
  .env.example         Template for environment variables
  crontab.example      Cron schedule example
  data/                DailyMemory storage (auto-created)
  digests/             JSON output when using --save (auto-created)
```

## Module reference

### `config.py`

Loads all environment variables at import time via `python-dotenv`. Required vars raise `KeyError` if missing.

| Export | Type | Description |
|---|---|---|
| `NEWS_API_KEY` | `str` | NewsAPI key |
| `ANTHROPIC_API_KEY` | `str` | Anthropic API key |
| `SMTP_HOST`, `SMTP_PORT` | `str`, `int` | SMTP server settings |
| `SMTP_USER`, `SMTP_PASSWORD` | `str` | SMTP credentials |
| `DIGEST_RECIPIENT` | `str` | Email recipient |
| `NEWS_QUERY`, `NEWS_PAGE_SIZE` | `str`, `int` | NewsAPI settings |
| `REDDIT_CLIENT_ID`, etc. | `str \| None` | Reddit OAuth (optional) |
| `DOMAINS` | `list[dict]` | 7 domain configs (id, name, description, min_ideas, max_ideas, icon) |
| `DOMAIN_NEWS_SOURCES` | `dict` | Per-domain RSS feeds and subreddits |

---

### `news_fetcher.py`

Fetches and normalizes news from 9 source types. All HTTP goes through `_request_with_retry()` with exponential backoff for 429/5xx/connection errors. Optional sources gracefully degrade if dependencies aren't installed.

#### Public functions

| Function | Signature | Description |
|---|---|---|
| `fetch_top_headlines` | `(country, category, page_size) → list[dict]` | NewsAPI `/v2/top-headlines` |
| `fetch_everything` | `(query, page_size) → list[dict]` | NewsAPI `/v2/everything` |
| `fetch_hacker_news` | `(count) → list[dict]` | Top HN stories via Firebase API (parallelised, 10 workers) |
| `fetch_rss_feeds` | `() → list[dict]` | General RSS feeds (Ars Technica, TechCrunch, Wired, etc.) |
| `fetch_domain_rss_feeds` | `() → list[dict]` | Domain-specific RSS feeds from `DOMAIN_NEWS_SOURCES`. Tags each article with `domain_hint`. |
| `fetch_reddit` | `() → list[dict]` | Reddit OAuth API. Merges general + domain-specific subreddits, tags domain posts with `domain_hint`. |
| `fetch_federal_register` | `() → list[dict]` | Federal Register API (recent rules) |
| `fetch_google_trends` | `() → list[dict]` | Google Trends via pytrends |
| `fetch_github_trending` | `() → list[dict]` | GitHub Trending page (HTML scraping) |
| `fetch_all_news` | `() → list[dict]` | Calls all 9 fetchers, combines, deduplicates. Returns normalized articles. |
| `prepare_news_for_analysis` | `(news_items, domains) → dict[str, list[dict]]` | Buckets articles into per-domain lists using `domain_hint`. Supplements domains with <10 items from the general pool. |

#### Internal helpers

| Function | Description |
|---|---|
| `_request_with_retry` | HTTP request with 3 retries, exponential backoff for 429/5xx/connection errors |
| `_normalize_url` | Strips utm_*, www, trailing slashes for URL dedup |
| `_titles_similar` | SequenceMatcher ratio >80% check |
| `_deduplicate` | Dedup by normalized URL + title similarity |
| `_build_subreddit_domain_map` | Merges general + domain subreddits into a `subreddit → domain_id` map |
| `_normalize_newsapi_article` | Converts raw NewsAPI response to normalized schema |
| `_fetch_hn_item` | Fetches and normalizes a single HN story |

#### Normalized article schema

```json
{
  "title": "...",
  "description": "...",
  "source": "Hacker News",
  "url": "https://...",
  "published_at": "2026-02-15T12:00:00+00:00",
  "category": "hacker_news",
  "domain_hint": "education_edtech",
  "metadata": {}
}
```

Categories: `tech_news`, `hacker_news`, `reddit`, `regulation`, `trending`, `github_trending`

---

### `analyzer.py`

Sends articles to Claude for opportunity analysis. Supports two modes: legacy (single prompt) and domain-aware (parallel per-domain calls).

#### Public functions

| Function | Signature | Description |
|---|---|---|
| `analyze_opportunities` | `(news_items, domains=None) → list[dict]` | Main entry point. Accepts a flat list (legacy) or `dict[str, list[dict]]` (domain-aware). Runs analysis → validation → cross-domain dedup → re-scoring → memory recording. |
| `get_token_usage` | `() → dict[str, int]` | Returns cumulative `input_tokens`, `output_tokens`, `total_tokens` for the session. |

#### Key classes

| Class | Description |
|---|---|
| `DailyMemory` | Tracks previously generated ideas in `data/previous_ideas.json`. Looks back 7 days for dedup, prunes entries >30 days. Injects a "do not repeat" list into the system prompt. |

#### DailyMemory methods

| Method | Description |
|---|---|
| `recent_ideas()` | Returns ideas from the last 7 days |
| `format_for_prompt()` | Renders recent ideas as a bullet list for the system prompt |
| `record(opportunities)` | Appends today's ideas and saves to disk |
| `save()` | Writes entries to `data/previous_ideas.json` |

#### Internal functions

| Function | Description |
|---|---|
| `_call_claude_with_retry` | Shared retry engine (3 attempts, exponential backoff). Parses JSON response, falls back to partial extraction. Tracks token usage. |
| `_rescore` | Second Claude call scoring each opportunity on feasibility, demand_confidence, uniqueness (1–10). Re-sorts by avg_score. |
| `_build_domain_system_prompt` | Formats the domain-specific prompt template with domain name, description, valid IDs, min/max ideas. |
| `_analyze_single_domain` | Per-domain LLM call. Tags results with `domains` and `primary_domain`. Called from ThreadPoolExecutor. |
| `_deduplicate_across_domains` | Merges opportunities with >70% title similarity across domains, combining domain tags. |
| `_analyze_legacy` | Original single-prompt analysis (backward compatibility when DOMAINS is empty). |
| `_format_articles` | Renders articles as plain text for the prompt. |
| `_extract_json` | Extracts JSON array from model output (tries direct parse → fenced code block → outermost `[…]`). |
| `_extract_partial` | Last-resort fallback: salvages individual `{…}` objects from broken output. |
| `_validate` | Checks required fields, strips entries missing both name and one_liner. |

#### Opportunity schema

```json
{
  "rank": 1,
  "name": "Product Name",
  "one_liner": "What it does and for whom",
  "domains": ["education_edtech", "health_wellness"],
  "primary_domain": "education_edtech",
  "news_trigger": "...",
  "the_problem": "...",
  "target_audience": "...",
  "product_description": "...",
  "revenue_model": "...",
  "market_signal": "...",
  "competitive_landscape": "...",
  "complexity": "low | medium | high",
  "estimated_build_time": "2 weeks for MVP",
  "tech_stack": "...",
  "build_plan": ["Step 1: ...", "Step 2: ..."],
  "risks_and_challenges": "...",
  "growth_hook": "...",
  "feasibility": 7,
  "demand_confidence": 6,
  "uniqueness": 8,
  "avg_score": 7.0
}
```

---

### `digest_sender.py`

Builds and sends opportunity digest emails over SMTP/TLS. Supports domain-grouped layout with table of contents and cross-domain highlights.

#### Public functions

| Function | Signature | Description |
|---|---|---|
| `send_opportunities_digest` | `(opportunities, domains=None, news_count=None) → None` | Sends a multipart email (HTML + plain-text fallback). Groups opportunities by domain when domains are provided. |
| `send_digest` | `(digest: str) → None` | Legacy plain-text-only sender. |
| `_build_plain_text` | `(opportunities, domains=None) → str` | Renders opportunities as readable plain text, grouped by domain. Also used by `main.py` for `--dry-run`. |

#### Internal functions

| Function | Description |
|---|---|
| `_build_html` | Renders the full HTML email: header, TOC with anchor links, cross-domain highlights section, per-domain sections, footer with timestamp. |
| `_build_card_html` | Renders a single opportunity card (rank badge, scores, domain tags, detail grid, build plan). |
| `_build_domain_lookup` | Builds `domain_id → config` dict for quick lookup. |
| `_group_by_domain` | Groups opportunities by `primary_domain` in canonical domain order. |
| `_get_cross_domain_ideas` | Filters for opportunities tagged with 2+ domains. |
| `_detail_row` | Renders a single label/value row in the card detail grid. |

---

### `main.py`

CLI entry point. Configures logging, parses arguments, orchestrates the fetch → analyse → deliver pipeline.

#### Public functions

| Function | Signature | Description |
|---|---|---|
| `main` | `(argv=None) → None` | Full pipeline: resolve domains → fetch → prepare → analyse → save → deliver. |
| `resend_from_file` | `(path, dry_run=False) → None` | Loads a saved digest JSON and sends (or previews) the email. Infers domain mode from the data. |

#### Internal functions

| Function | Description |
|---|---|
| `_parse_args` | Argparse setup: `--dry-run`, `--save`, `--resend [FILE]`, `--domains IDS`. |
| `_resolve_domains` | Validates `--domains` flag against `config.DOMAINS`. Returns filtered list or full list. |
| `_save_json` | Writes to `digests/<date>/all.json` + per-domain `<domain_id>.json` files. |
| `_resolve_digest_path` | Resolves `--resend` value ("latest", date string, or file path) to a concrete path. |
| `_configure_logging` | Sets up root logging with readable console format. |

---

### `build_manifest.py`

Copies digest JSON files into `frontend/digests/` and generates `manifest.json` for the web dashboard.

#### Public functions

| Function | Description |
|---|---|
| `build()` | Scans `digests/` for date directories (new layout: `<date>/all.json`) and flat files (old layout: `<date>.json`). Copies them into `frontend/digests/` as flat files. Generates `manifest.json` with digest list, domain metadata, and aggregate stats. |

#### Manifest schema

```json
{
  "digests": [
    { "date": "2026-02-15", "count": 7 }
  ],
  "domains": [
    { "id": "education_edtech", "name": "Education / Courses / EdTech", "icon": "📚" }
  ],
  "stats": {
    "total_digests": 2,
    "total_opportunities": 12,
    "avg_score": 5.08,
    "complexity_breakdown": { "medium": 11, "high": 1 },
    "domain_breakdown": { "education_edtech": 3, "food_hospitality": 2 },
    "date_range": { "earliest": "2026-02-14", "latest": "2026-02-15" }
  }
}
```

## Web Dashboard

A read-only Vue 3 dashboard for browsing saved digests in the browser. Supports domain filtering, complexity filtering, search, and detail modals.

### Local dev

```bash
# Generate manifest and copy digests into frontend/
python build_manifest.py

# Serve the frontend
python -m http.server 5500 -d frontend
```

Open http://localhost:5500.

### Netlify deployment

The frontend is a fully static site — no backend server needed.

1. Push the repo to GitHub (make sure `digests/` is not in `.gitignore`).
2. Connect the repo in Netlify. The `netlify.toml` runs `python build_manifest.py` as the build command and publishes `frontend/`.
3. Each time you push new digests, Netlify rebuilds automatically.

## Scheduling

### Option A: cron (any Linux/macOS server)

A ready-to-use crontab entry is provided in `crontab.example`. It runs the script daily at 7:00 AM:

```bash
# Install directly
crontab -l > /tmp/crontab.bak
cat crontab.example >> /tmp/crontab.bak
crontab /tmp/crontab.bak

# Verify
crontab -l
```

Edit the paths in `crontab.example` to match your installation directory and virtualenv location.

### Option B: AWS Lambda + EventBridge schedule

This is a good fit if you don't want to maintain a server. Lambda's default 15-minute timeout is plenty for this workload.

#### 1. Create a Lambda handler

Add a `lambda_function.py` to the project root:

```python
from main import main


def handler(event, context):
    """AWS Lambda entry point."""
    # --save is omitted because Lambda has an ephemeral filesystem.
    # To persist JSON output, write to S3 instead.
    main([])  # no flags = fetch, analyse, email
    return {"statusCode": 200, "body": "Digest sent"}
```

#### 2. Package the deployment zip

```bash
# Create a fresh build directory
mkdir -p build
pip install -r requirements.txt -t build/

# Copy application code
cp main.py news_fetcher.py analyzer.py digest_sender.py config.py build/

# Zip it up
cd build && zip -r ../deployment.zip . && cd ..
```

#### 3. Create the Lambda function

```bash
aws lambda create-function \
  --function-name opportunity-radar \
  --runtime python3.12 \
  --handler lambda_function.handler \
  --zip-file fileb://deployment.zip \
  --timeout 300 \
  --memory-size 256 \
  --role arn:aws:iam::<ACCOUNT_ID>:role/<LAMBDA_ROLE> \
  --environment "Variables={
    NEWS_API_KEY=...,
    ANTHROPIC_API_KEY=...,
    SMTP_USER=...,
    SMTP_PASSWORD=...,
    DIGEST_RECIPIENT=...
  }"
```

> For production, store secrets in AWS Secrets Manager or SSM Parameter Store
> instead of plain environment variables, and fetch them in `config.py`.

#### 4. Add an EventBridge (CloudWatch) schedule rule

```bash
# Create a rule that fires every day at 7:00 AM UTC
aws events put-rule \
  --name opportunity-radar-daily \
  --schedule-expression "cron(0 7 * * ? *)"

# Allow EventBridge to invoke the Lambda
aws lambda add-permission \
  --function-name opportunity-radar \
  --statement-id eventbridge-daily \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn arn:aws:events:<REGION>:<ACCOUNT_ID>:rule/opportunity-radar-daily

# Wire the rule to the Lambda
aws events put-targets \
  --rule opportunity-radar-daily \
  --targets "Id"="1","Arn"="arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:opportunity-radar"
```

#### 5. Verify

```bash
# Invoke manually to test
aws lambda invoke --function-name opportunity-radar /dev/stdout

# Check the schedule is active
aws events describe-rule --name opportunity-radar-daily
```

#### Lambda tips

- **Timeout**: set to at least 300 seconds. The Hacker News fetcher makes ~30 sequential HTTP calls and the Claude API call can take 10-20 seconds.
- **Memory**: 256 MB is sufficient. Increase if you raise `NEWS_PAGE_SIZE` significantly.
- **Saving JSON**: Lambda's `/tmp` is ephemeral. To persist digests, swap `_save_json` to write to an S3 bucket instead of local disk.
- **Logs**: Lambda automatically sends stdout/stderr to CloudWatch Logs. All the `logging.info` calls will appear there.
- **python-dotenv**: works fine on Lambda -- it simply won't find a `.env` file and will fall through to the real environment variables set on the function.
