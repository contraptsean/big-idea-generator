"""Fetch and normalize news from multiple sources.

Sources:
  1. NewsAPI (top-headlines + everything)
  2. Hacker News (Firebase API, parallelised)
  3. RSS feeds (via feedparser)
  4. Reddit (OAuth API)
  5. Federal Register (public API)
  6. Google Trends (via pytrends)
  7. GitHub Trending (HTML scraping)
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REQUEST_TIMEOUT = 30
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2  # seconds, doubled each retry

_NEWSAPI_TOP_HEADLINES = "https://newsapi.org/v2/top-headlines"
_NEWSAPI_EVERYTHING = "https://newsapi.org/v2/everything"

_HN_TOP_STORIES = "https://hacker-news.firebaseio.com/v0/topstories.json"
_HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
_HN_STORY_COUNT = 30
_HN_MIN_POINTS = 10
_HN_WORKERS = 10

_RSS_FEEDS = [
    ("https://feeds.arstechnica.com/arstechnica/technology-lab", "Ars Technica"),
    ("https://www.theverge.com/rss/index.xml", "The Verge"),
    ("https://techcrunch.com/feed/", "TechCrunch"),
    ("https://feeds.feedburner.com/venturebeat/SZYF", "VentureBeat"),
    ("https://www.wired.com/feed/rss", "Wired"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "NYT Tech"),
]

_REDDIT_SUBREDDITS = [
    "technology",
    "startups",
    "SaaS",
    "programming",
    "Entrepreneur",
]
_REDDIT_LIMIT = 10  # posts per subreddit

_FEDERAL_REGISTER_API = "https://www.federalregister.gov/api/v1/documents.json"
_GITHUB_TRENDING_URL = "https://github.com/trending"

_TITLE_SIMILARITY_THRESHOLD = 0.80
_CROSS_DOMAIN_TITLE_SIMILARITY = 0.70  # used for cross-domain dedup in analyzer


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _request_with_retry(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    data: dict | None = None,
    max_retries: int = _MAX_RETRIES,
    timeout: int = _REQUEST_TIMEOUT,
) -> requests.Response:
    """Make an HTTP request with retry logic for transient / rate-limit errors."""
    delay = _RETRY_BACKOFF
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.request(
                method, url,
                params=params, headers=headers, data=data,
                timeout=timeout,
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", delay))
                logger.warning(
                    "Rate-limited on %s (attempt %d/%d), retrying in %ds",
                    url, attempt, max_retries, retry_after,
                )
                time.sleep(retry_after)
                delay *= 2
                continue
            if resp.status_code >= 500:
                logger.warning(
                    "Server error %d on %s (attempt %d/%d), retrying in %ds",
                    resp.status_code, url, attempt, max_retries, delay,
                )
                time.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp
        except requests.ConnectionError as exc:
            last_exc = exc
            logger.warning(
                "Connection error on %s (attempt %d/%d): %s",
                url, attempt, max_retries, exc,
            )
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2

    if last_exc is not None:
        raise last_exc
    raise requests.HTTPError(f"Request to {url} failed after {max_retries} retries")


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    """Normalize a URL for deduplication.

    - Strips utm_* and other tracking query parameters.
    - Removes trailing slashes from the path.
    - Strips 'www.' from the hostname.
    - Lower-cases the scheme and host.
    """
    if not url:
        return ""

    parsed = urlparse(url)

    # Lower-case scheme + host, strip www.
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    if host.startswith("www."):
        host = host[4:]

    # Preserve port if non-standard
    port = parsed.port
    netloc = f"{host}:{port}" if port and port not in (80, 443) else host

    # Strip tracking params
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {
        k: v for k, v in query_params.items()
        if not k.lower().startswith("utm_")
        and k.lower() not in ("ref", "source", "fbclid", "gclid")
    }
    query = urlencode(cleaned, doseq=True) if cleaned else ""

    # Strip trailing slashes from path
    path = parsed.path.rstrip("/") or ""

    return urlunparse((scheme, netloc, path, parsed.params, query, ""))


# ---------------------------------------------------------------------------
# Title similarity dedup
# ---------------------------------------------------------------------------

def _titles_similar(a: str, b: str) -> bool:
    """Return True if two titles are >80% similar (SequenceMatcher ratio)."""
    if not a or not b:
        return False
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > _TITLE_SIMILARITY_THRESHOLD


# ---------------------------------------------------------------------------
# Source 1: NewsAPI
# ---------------------------------------------------------------------------

def _normalize_newsapi_article(article: dict, category: str = "tech_news") -> dict:
    """Convert a raw NewsAPI article into the normalized schema."""
    source_obj = article.get("source") or {}
    return {
        "title": article.get("title") or "",
        "description": article.get("description") or "",
        "source": source_obj.get("name") or "",
        "url": article.get("url") or "",
        "published_at": article.get("publishedAt") or "",
        "category": category,
        "metadata": {},
    }


def fetch_top_headlines(
    country: str = "us",
    category: str | None = "technology",
    page_size: int | None = None,
) -> list[dict]:
    """Fetch today's top headlines from NewsAPI /v2/top-headlines."""
    params: dict = {
        "country": country,
        "pageSize": page_size or config.NEWS_PAGE_SIZE,
        "apiKey": config.NEWS_API_KEY,
    }
    if category:
        params["category"] = category

    try:
        resp = _request_with_retry("GET", _NEWSAPI_TOP_HEADLINES, params=params)
    except (requests.RequestException, requests.HTTPError) as exc:
        logger.error("Failed to fetch top headlines: %s", exc)
        return []

    data = resp.json()
    if data.get("status") != "ok":
        logger.error("NewsAPI top-headlines error: %s", data.get("message"))
        return []

    articles = [_normalize_newsapi_article(a, "tech_news") for a in data.get("articles", [])]
    logger.info("NewsAPI top-headlines: %d articles", len(articles))
    return articles


def fetch_everything(
    query: str | None = None,
    page_size: int | None = None,
) -> list[dict]:
    """Fetch articles from NewsAPI /v2/everything."""
    params: dict = {
        "q": query or config.NEWS_QUERY,
        "pageSize": page_size or config.NEWS_PAGE_SIZE,
        "sortBy": "publishedAt",
        "apiKey": config.NEWS_API_KEY,
    }

    try:
        resp = _request_with_retry("GET", _NEWSAPI_EVERYTHING, params=params)
    except (requests.RequestException, requests.HTTPError) as exc:
        logger.error("Failed to fetch everything: %s", exc)
        return []

    data = resp.json()
    if data.get("status") != "ok":
        logger.error("NewsAPI everything error: %s", data.get("message"))
        return []

    articles = [_normalize_newsapi_article(a, "tech_news") for a in data.get("articles", [])]
    logger.info("NewsAPI everything: %d articles", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Source 2: Hacker News (Firebase API, parallelised)
# ---------------------------------------------------------------------------

def _fetch_hn_item(story_id: int) -> dict | None:
    """Fetch a single HN item. Returns normalized dict or None."""
    url = _HN_ITEM.format(id=story_id)
    try:
        resp = _request_with_retry("GET", url, max_retries=1)
    except (requests.RequestException, requests.HTTPError) as exc:
        logger.warning("Skipping HN item %d: %s", story_id, exc)
        return None

    item = resp.json()
    if item is None or item.get("type") != "story":
        return None

    score = item.get("score", 0)
    if score < _HN_MIN_POINTS:
        return None

    ts = item.get("time")
    published_at = (
        datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
    )

    return {
        "title": item.get("title") or "",
        "description": "",  # HN stories have no description field
        "source": "Hacker News",
        "url": item.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
        "published_at": published_at,
        "category": "hacker_news",
        "metadata": {
            "score": score,
            "comments": item.get("descendants", 0),
            "hn_id": story_id,
        },
    }


def fetch_hacker_news(count: int = _HN_STORY_COUNT) -> list[dict]:
    """Fetch top stories from Hacker News using parallel requests."""
    try:
        resp = _request_with_retry("GET", _HN_TOP_STORIES)
    except (requests.RequestException, requests.HTTPError) as exc:
        logger.error("Failed to fetch HN top stories: %s", exc)
        return []

    story_ids: list[int] = resp.json()[:count]
    articles: list[dict] = []

    with ThreadPoolExecutor(max_workers=_HN_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_hn_item, sid): sid
            for sid in story_ids
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                articles.append(result)

    # Sort by score descending (highest-scoring stories first)
    articles.sort(key=lambda a: a.get("metadata", {}).get("score", 0), reverse=True)
    logger.info("Hacker News: %d articles (min %d points)", len(articles), _HN_MIN_POINTS)
    return articles


# ---------------------------------------------------------------------------
# Source 3: RSS feeds (via feedparser)
# ---------------------------------------------------------------------------

def fetch_rss_feeds() -> list[dict]:
    """Fetch articles from a curated list of RSS feeds."""
    try:
        import feedparser
    except ImportError:
        logger.warning("feedparser not installed — skipping RSS feeds")
        return []

    articles: list[dict] = []

    for feed_url, feed_name in _RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as exc:
            logger.warning("Failed to parse RSS feed %s: %s", feed_name, exc)
            continue

        if feed.bozo and not feed.entries:
            logger.warning("RSS feed %s returned no entries (bozo: %s)",
                           feed_name, feed.bozo_exception)
            continue

        for entry in feed.entries[:10]:  # cap per feed
            published = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    published = datetime(*entry.published_parsed[:6],
                                         tzinfo=timezone.utc).isoformat()
                except (TypeError, ValueError):
                    pass
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                try:
                    published = datetime(*entry.updated_parsed[:6],
                                         tzinfo=timezone.utc).isoformat()
                except (TypeError, ValueError):
                    pass

            articles.append({
                "title": entry.get("title", ""),
                "description": entry.get("summary", ""),
                "source": feed_name,
                "url": entry.get("link", ""),
                "published_at": published,
                "category": "tech_news",
                "metadata": {"feed_url": feed_url},
            })

    logger.info("RSS feeds: %d articles from %d feeds", len(articles), len(_RSS_FEEDS))
    return articles


def fetch_domain_rss_feeds() -> list[dict]:
    """Fetch articles from domain-specific RSS feeds defined in config.DOMAIN_NEWS_SOURCES.

    Each article is tagged with a ``domain_hint`` field indicating which domain
    the feed belongs to. This is a hint — the LLM makes the final assignment.
    """
    try:
        import feedparser
    except ImportError:
        logger.warning("feedparser not installed — skipping domain RSS feeds")
        return []

    domain_sources = getattr(config, "DOMAIN_NEWS_SOURCES", None)
    if not domain_sources:
        return []

    articles: list[dict] = []

    for domain_id, sources in domain_sources.items():
        for feed_info in sources.get("rss", []):
            feed_url = feed_info["url"]
            feed_name = feed_info["name"]
            try:
                feed = feedparser.parse(feed_url)
            except Exception as exc:
                logger.warning("Failed to parse domain RSS feed %s (%s): %s",
                               feed_name, domain_id, exc)
                continue

            if feed.bozo and not feed.entries:
                logger.warning("Domain RSS feed %s returned no entries (bozo: %s)",
                               feed_name, feed.bozo_exception)
                continue

            for entry in feed.entries[:10]:
                published = ""
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        published = datetime(*entry.published_parsed[:6],
                                             tzinfo=timezone.utc).isoformat()
                    except (TypeError, ValueError):
                        pass
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    try:
                        published = datetime(*entry.updated_parsed[:6],
                                             tzinfo=timezone.utc).isoformat()
                    except (TypeError, ValueError):
                        pass

                articles.append({
                    "title": entry.get("title", ""),
                    "description": entry.get("summary", ""),
                    "source": feed_name,
                    "url": entry.get("link", ""),
                    "published_at": published,
                    "category": "tech_news",
                    "domain_hint": domain_id,
                    "metadata": {"feed_url": feed_url},
                })

        logger.info("Domain RSS (%s): fetched from %d feeds",
                     domain_id, len(sources.get("rss", [])))

    logger.info("Domain RSS feeds total: %d articles", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Source 4: Reddit (OAuth API)
# ---------------------------------------------------------------------------

def _build_subreddit_domain_map() -> dict[str, str | None]:
    """Build a mapping of subreddit name → domain_id (or None for general).

    Merges the general _REDDIT_SUBREDDITS list with domain-specific subreddits
    from DOMAIN_NEWS_SOURCES, deduplicating by subreddit name (case-insensitive).
    """
    sub_map: dict[str, str | None] = {}

    # General subreddits have no domain hint
    for sub in _REDDIT_SUBREDDITS:
        sub_map[sub.lower()] = None

    # Domain-specific subreddits
    domain_sources = getattr(config, "DOMAIN_NEWS_SOURCES", None) or {}
    for domain_id, sources in domain_sources.items():
        for sub in sources.get("subreddits", []):
            key = sub.lower()
            if key not in sub_map:
                sub_map[key] = domain_id

    return sub_map


def fetch_reddit() -> list[dict]:
    """Fetch top posts from technology/startup subreddits via Reddit OAuth.

    Includes domain-specific subreddits from DOMAIN_NEWS_SOURCES, tagged with
    ``domain_hint`` for domain-categorised analysis.
    """
    client_id = getattr(config, "REDDIT_CLIENT_ID", None)
    client_secret = getattr(config, "REDDIT_CLIENT_SECRET", None)
    username = getattr(config, "REDDIT_USERNAME", None)
    password = getattr(config, "REDDIT_PASSWORD", None)

    if not all([client_id, client_secret, username, password]):
        logger.info("Reddit credentials not configured — skipping Reddit")
        return []

    # Obtain OAuth token
    auth_url = "https://www.reddit.com/api/v1/access_token"
    try:
        auth_resp = requests.post(
            auth_url,
            auth=(client_id, client_secret),
            data={
                "grant_type": "password",
                "username": username,
                "password": password,
            },
            headers={"User-Agent": "OpportunityRadar/1.0"},
            timeout=_REQUEST_TIMEOUT,
        )
        auth_resp.raise_for_status()
        token = auth_resp.json().get("access_token")
        if not token:
            logger.error("Reddit OAuth: no access_token in response")
            return []
    except (requests.RequestException, KeyError) as exc:
        logger.error("Reddit OAuth failed: %s", exc)
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "OpportunityRadar/1.0",
    }

    sub_domain_map = _build_subreddit_domain_map()
    all_subreddits = list(sub_domain_map.keys())

    articles: list[dict] = []
    for subreddit in all_subreddits:
        api_url = f"https://oauth.reddit.com/r/{subreddit}/hot"
        try:
            resp = _request_with_retry(
                "GET", api_url,
                params={"limit": _REDDIT_LIMIT},
                headers=headers,
            )
        except (requests.RequestException, requests.HTTPError) as exc:
            logger.warning("Failed to fetch r/%s: %s", subreddit, exc)
            continue

        domain_hint = sub_domain_map.get(subreddit.lower())

        data = resp.json().get("data", {}).get("children", [])
        for post in data:
            p = post.get("data", {})
            if p.get("stickied"):
                continue

            created_utc = p.get("created_utc")
            published_at = (
                datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()
                if created_utc else ""
            )

            articles.append({
                "title": p.get("title", ""),
                "description": (p.get("selftext") or "")[:500],
                "source": f"r/{subreddit}",
                "url": p.get("url") or f"https://reddit.com{p.get('permalink', '')}",
                "published_at": published_at,
                "category": "reddit",
                "domain_hint": domain_hint,
                "metadata": {
                    "score": p.get("score", 0),
                    "comments": p.get("num_comments", 0),
                    "subreddit": subreddit,
                },
            })

    logger.info("Reddit: %d articles from %d subreddits",
                len(articles), len(all_subreddits))
    return articles


# ---------------------------------------------------------------------------
# Source 5: Federal Register (public API)
# ---------------------------------------------------------------------------

def fetch_federal_register() -> list[dict]:
    """Fetch recent documents from the Federal Register API."""
    articles: list[dict] = []

    params = {
        "per_page": 10,
        "order": "newest",
        "conditions[type][]": "RULE",
    }

    try:
        resp = _request_with_retry("GET", _FEDERAL_REGISTER_API, params=params)
    except (requests.RequestException, requests.HTTPError) as exc:
        logger.error("Failed to fetch Federal Register: %s", exc)
        return []

    results = resp.json().get("results", [])
    for doc in results:
        # Filter by relevant categories
        agencies = [a.get("name", "") for a in doc.get("agencies", [])]
        topics = doc.get("topics", []) or []

        articles.append({
            "title": doc.get("title", ""),
            "description": doc.get("abstract") or "",
            "source": "Federal Register",
            "url": doc.get("html_url") or "",
            "published_at": doc.get("publication_date") or "",
            "category": "regulation",
            "metadata": {
                "document_type": doc.get("type", ""),
                "agencies": agencies,
                "topics": topics,
                "document_number": doc.get("document_number", ""),
            },
        })

    logger.info("Federal Register: %d documents", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Source 6: Google Trends (via pytrends)
# ---------------------------------------------------------------------------

def fetch_google_trends() -> list[dict]:
    """Fetch today's trending searches from Google Trends."""
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.warning("pytrends not installed — skipping Google Trends")
        return []

    articles: list[dict] = []

    try:
        pytrends = TrendReq(hl="en-US", tz=360)
        trending = pytrends.trending_searches(pn="united_states")
    except Exception as exc:
        logger.error("Google Trends request failed: %s", exc)
        return []

    for _, row in trending.head(20).iterrows():
        query = row[0]
        articles.append({
            "title": f"Trending: {query}",
            "description": f"'{query}' is currently trending on Google Search",
            "source": "Google Trends",
            "url": f"https://trends.google.com/trends/explore?q={query}&geo=US",
            "published_at": datetime.now(tz=timezone.utc).isoformat(),
            "category": "trending",
            "metadata": {"trend_query": query},
        })

    logger.info("Google Trends: %d trending searches", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Source 7: GitHub Trending (HTML scraping)
# ---------------------------------------------------------------------------

def fetch_github_trending() -> list[dict]:
    """Scrape today's trending repositories from GitHub."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("beautifulsoup4 not installed — skipping GitHub Trending")
        return []

    articles: list[dict] = []

    try:
        resp = _request_with_retry(
            "GET", _GITHUB_TRENDING_URL,
            headers={"Accept": "text/html"},
        )
    except (requests.RequestException, requests.HTTPError) as exc:
        logger.error("Failed to fetch GitHub Trending: %s", exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    repo_rows = soup.select("article.Box-row")

    for row in repo_rows[:20]:
        # Repo name (e.g. "owner / repo")
        h2 = row.select_one("h2 a")
        if not h2:
            continue
        repo_path = h2.get("href", "").strip("/")
        repo_name = repo_path.replace("/", " / ") if repo_path else ""
        repo_url = f"https://github.com/{repo_path}" if repo_path else ""

        # Description
        desc_el = row.select_one("p")
        description = desc_el.get_text(strip=True) if desc_el else ""

        # Language
        lang_el = row.select_one("[itemprop='programmingLanguage']")
        language = lang_el.get_text(strip=True) if lang_el else ""

        # Stars today
        stars_today = ""
        spans = row.select("span.d-inline-block.float-sm-right")
        if spans:
            stars_today = spans[0].get_text(strip=True)

        articles.append({
            "title": f"Trending: {repo_name}",
            "description": description,
            "source": "GitHub Trending",
            "url": repo_url,
            "published_at": datetime.now(tz=timezone.utc).isoformat(),
            "category": "github_trending",
            "metadata": {
                "repo": repo_path,
                "language": language,
                "stars_today": stars_today,
            },
        })

    logger.info("GitHub Trending: %d repositories", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Combined fetch + deduplication
# ---------------------------------------------------------------------------

def _deduplicate(articles: list[dict]) -> list[dict]:
    """Deduplicate articles by normalized URL and title similarity.

    1. Exact URL match (after normalization) — first occurrence wins.
    2. Title similarity > 80% (SequenceMatcher) — first occurrence wins.
    """
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    unique: list[dict] = []

    for article in articles:
        # URL dedup
        norm_url = _normalize_url(article.get("url", ""))
        if norm_url and norm_url in seen_urls:
            continue

        # Title similarity dedup
        title = article.get("title", "")
        if title and any(_titles_similar(title, t) for t in seen_titles):
            continue

        if norm_url:
            seen_urls.add(norm_url)
        if title:
            seen_titles.append(title)

        unique.append(article)

    return unique


def fetch_all_news() -> list[dict]:
    """Fetch from all sources, combine, and deduplicate.

    Sources are fetched sequentially (each is already fast or parallelised
    internally). Articles are deduplicated by normalized URL and title
    similarity. Domain-specific RSS feeds are included and ``domain_hint``
    fields are preserved through deduplication.
    """
    headlines = fetch_top_headlines()
    everything = fetch_everything()
    hn = fetch_hacker_news()
    rss = fetch_rss_feeds()
    domain_rss = fetch_domain_rss_feeds()
    reddit = fetch_reddit()
    federal = fetch_federal_register()
    trends = fetch_google_trends()
    github = fetch_github_trending()

    combined = (headlines + everything + hn + rss + domain_rss
                + reddit + federal + trends + github)
    unique = _deduplicate(combined)

    # Log per-domain_hint counts
    from collections import Counter
    hint_counts = Counter(a.get("domain_hint") for a in unique)
    general_count = hint_counts.pop(None, 0)
    logger.info(
        "Fetched %d articles total (%d headlines, %d everything, %d HN, "
        "%d RSS, %d domain RSS, %d Reddit, %d Federal Register, %d trends, "
        "%d GitHub) → %d after dedup",
        len(combined), len(headlines), len(everything), len(hn),
        len(rss), len(domain_rss), len(reddit), len(federal),
        len(trends), len(github), len(unique),
    )
    logger.info(
        "Domain hints: %d general, %s",
        general_count,
        ", ".join(f"{k}={v}" for k, v in sorted(hint_counts.items())),
    )
    return unique


def prepare_news_for_analysis(
    news_items: list[dict],
    domain_groups: list[dict],
) -> dict[str, list[dict]]:
    """Organise news items into per-group buckets for analysis.

    Accepts either a list of domain-groups (each with a ``group_id`` and
    ``domains`` list) or the legacy flat list of domain dicts (each with an
    ``id``).  The return dict is keyed by ``group_id`` in group mode and by
    ``domain_id`` in legacy mode.

    For each group:
    - Items whose ``domain_hint`` matches any domain in the group go to that
      group's bucket.
    - General items (no ``domain_hint``) supplement the bucket until it has
      at least 15 items.
    - The bucket is capped at 40 items; domain-specific items take priority.
    """
    _MIN_ITEMS = 15
    _MAX_ITEMS = 40

    # Detect legacy mode: flat list of domains (each has "id", not "group_id")
    if domain_groups and not domain_groups[0].get("group_id"):
        return _prepare_news_legacy(news_items, domain_groups)

    # ----- grouped mode -----
    # Map every domain_id → its parent group_id
    domain_to_group: dict[str, str] = {}
    for group in domain_groups:
        for domain in group["domains"]:
            domain_to_group[domain["id"]] = group["group_id"]

    group_specific: dict[str, list[dict]] = {
        g["group_id"]: [] for g in domain_groups
    }
    general: list[dict] = []

    for item in news_items:
        hint = item.get("domain_hint")
        if hint and hint in domain_to_group:
            group_specific[domain_to_group[hint]].append(item)
        else:
            general.append(item)

    result: dict[str, list[dict]] = {}
    for group in domain_groups:
        gid = group["group_id"]
        gname = group["group_name"]
        specific = group_specific[gid]
        n_specific = len(specific)

        # Supplement with general news to reach _MIN_ITEMS
        if n_specific < _MIN_ITEMS:
            supplemented = specific + general[: _MIN_ITEMS - n_specific]
        else:
            supplemented = specific

        # Cap at _MAX_ITEMS (domain-specific items are already first)
        supplemented = supplemented[:_MAX_ITEMS]

        n_general = max(0, len(supplemented) - n_specific)
        logger.info(
            "Group '%s': %d domain-specific + %d general = %d items",
            gname, n_specific, n_general, len(supplemented),
        )
        result[gid] = supplemented

    return result


def _prepare_news_legacy(
    news_items: list[dict],
    domains: list[dict],
) -> dict[str, list[dict]]:
    """Legacy version: organise items into per-domain buckets (domain_id keys)."""
    domain_ids = {d["id"] for d in domains}
    domain_specific: dict[str, list[dict]] = {did: [] for did in domain_ids}
    general: list[dict] = []

    for item in news_items:
        hint = item.get("domain_hint")
        if hint and hint in domain_ids:
            domain_specific[hint].append(item)
        else:
            general.append(item)

    result: dict[str, list[dict]] = {}
    for domain_id in domain_ids:
        specific = domain_specific[domain_id]
        if len(specific) >= 10:
            result[domain_id] = specific
        else:
            needed = 10 - len(specific)
            result[domain_id] = specific + general[:needed]

    for domain_id, items in result.items():
        logger.info("Domain %s: %d items for analysis", domain_id, len(items))

    return result


# Backward-compatible alias so existing callers keep working.
fetch_articles = fetch_all_news


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    articles = fetch_all_news()
    print(f"\n{'='*60}")
    print(f"Total articles: {len(articles)}")
    print(f"{'='*60}")
    for a in articles[:5]:
        hint = a.get("domain_hint", "general")
        print(f"  [{a['category']}] [{hint}] {a['source']}: {a['title'][:70]}")
    if len(articles) > 5:
        print(f"  ... and {len(articles) - 5} more")
