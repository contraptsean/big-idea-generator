"""Analyse news articles for business opportunities using Claude."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import anthropic

import config

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

_MODEL = "claude-sonnet-4-6"                 # default / legacy
_EXPAND_MODEL = "claude-haiku-4-5-20251001"  # cheap expansion calls
_MAX_TOKENS = 16_384
_GROUPED_MAX_TOKENS = 4_000
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2  # seconds, doubled each retry
_DOMAIN_WORKERS = 3  # max parallel domain API calls (legacy mode)
_GROUP_WORKERS = 2   # max parallel group API calls (new mode)

# Slim schema — 10 core fields (no build-plan / audience / tech-stack etc.)
SLIM_REQUIRED_FIELDS = {
    "rank", "name", "one_liner", "domains", "primary_domain",
    "the_problem", "revenue_model", "competitive_landscape",
    "complexity", "growth_hook",
}

# Full schema — legacy mode
REQUIRED_FIELDS = {
    "rank", "name", "one_liner", "news_trigger", "the_problem",
    "target_audience", "product_description", "revenue_model",
    "market_signal", "competitive_landscape", "complexity",
    "estimated_build_time", "tech_stack", "build_plan",
    "risks_and_challenges", "growth_hook",
}

_CROSS_DOMAIN_TITLE_SIMILARITY = 0.70

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# Static (cacheable) portion — identical across all 4 group calls.
# cache_control {"type": "ephemeral"} is attached to this block in the API call.
STATIC_SYSTEM_PROMPT = """\
You are an elite product strategist and indie hacker advisor. Your job is \
to analyze today's news and surface concrete, actionable app and software \
business opportunities that a skilled solo full-stack developer could \
realistically build and monetize.

## Your Analytical Framework

For each news item or cluster of related news, think through these lenses:

1. **Pain Point Detection**: What new problem, friction, or unmet need \
does this news reveal or amplify?

2. **Behavioral Shift Spotting**: Is this news signaling a change in how \
people work, buy, learn, or operate?

3. **Regulatory & Policy Arbitrage**: Does this news involve new rules, \
compliance needs, or policy changes?

4. **Existing Market Gaps**: Does this news highlight failing or missing \
software?

5. **Picks-and-Shovels Thinking**: What tools and infrastructure do \
people riding this trend need?

6. **Cross-Domain Opportunities**: Could this news create an opportunity \
that spans multiple domains? If so, tag it with multiple domain IDs.

## Quality Filters

ONLY include opportunities that meet ALL of these criteria:
- Buildable solo in 1-6 weeks as an MVP
- Clear path to $1K-$5K/month within 6 months
- Specific and concrete — not vague category descriptions
- Solves a problem people already know they have
- Technically feasible with current APIs, models, and web tech

## All Valid Domain IDs for Tagging

ai_ml, dev_tools, food_hospitality, health_wellness, real_estate_housing, \
finance_personal_finance, ecommerce_retail, government_compliance, education_edtech

## Output Schema

Return a JSON array of opportunities. Each object must have EXACTLY these fields:

{
  "rank": 1,
  "name": "Short, memorable product name",
  "one_liner": "One sentence: what it does and for whom",
  "domains": ["primary_domain_id", "optional_secondary_domain_id"],
  "primary_domain": "primary_domain_id",
  "the_problem": "2-3 sentences on the specific pain point",
  "revenue_model": "How it makes money with a suggested price point",
  "competitive_landscape": "Closest existing solutions and the gap you exploit",
  "complexity": "low | medium | high",
  "growth_hook": "Specific plan to acquire the first 100 users"
}

IMPORTANT:
- ONLY output the JSON array. No preamble, no markdown fences, no explanation.
- Be brutally honest about complexity.
- When multiple news items point to the same opportunity, synthesize — don't duplicate.\
"""

# Legacy domain-specific prompt template (unchanged from original)
_DOMAIN_SYSTEM_PROMPT_TEMPLATE = """\
You are an elite product strategist and indie hacker advisor specializing in \
the {domain_name} space.

Your job is to analyze today's news and surface concrete, actionable app and \
software business opportunities in the {domain_name} domain that a skilled \
solo full-stack developer could realistically build and monetize.

## Domain Scope
{domain_description}

## Available Domains for Tagging
{domains_list_with_descriptions}

## Your Analytical Framework

For each news item or cluster of related news, think through these lenses:

1. **Pain Point Detection**: What new problem, friction, or unmet need does \
this news reveal or amplify for people in the {domain_name} space?

2. **Behavioral Shift Spotting**: Is this news signaling a change in how \
people in this domain work, buy, learn, or operate?

3. **Regulatory & Policy Arbitrage**: Does this news involve new rules, \
compliance needs, or policy changes affecting this domain?

4. **Existing Market Gaps**: Does this news highlight failing or missing \
software in this domain?

5. **Picks-and-Shovels Thinking**: What tools and infrastructure do people \
riding this trend need?

6. **Cross-Domain Opportunities**: Could this news create an opportunity \
that spans {domain_name} and another domain? If so, tag it with \
multiple domains.

## Quality Filters

ONLY include opportunities that meet ALL of these criteria:
- Buildable solo in 1-6 weeks as an MVP
- Clear path to $1K-$5K/month within 6 months
- Specific and concrete — not vague category descriptions
- Solves a problem people already know they have
- Technically feasible with current APIs, models, and web tech

## Output Format

Return a JSON array of {min_ideas} to {max_ideas} opportunities, ranked \
by combined feasibility and impact. Each object must have:

{{
  "rank": 1,
  "name": "Short, memorable product name",
  "one_liner": "One sentence: what it does and for whom",
  "domains": ["{domain_id}", "other_domain_id_if_applicable"],
  "primary_domain": "{domain_id}",
  "news_trigger": "Which specific news item(s) inspired this",
  "the_problem": "2-3 sentences on the specific pain point",
  "target_audience": "Specific persona(s)",
  "product_description": "3-5 sentences describing the MVP",
  "revenue_model": "How it makes money with suggested price point",
  "market_signal": "Evidence people would pay for this",
  "competitive_landscape": "Closest existing solutions and the gap",
  "complexity": "low | medium | high",
  "estimated_build_time": "e.g., '2 weeks for MVP'",
  "tech_stack": "Recommended technologies",
  "build_plan": ["Step 1: ...", "Step 2: ...", "Step 3: ..."],
  "risks_and_challenges": "What could go wrong",
  "growth_hook": "How to acquire the first 100 users"
}}

IMPORTANT:
- The "domains" field is a list. The primary domain should be "{domain_id}". \
If the idea also fits another domain, include that domain's ID too. \
Valid domain IDs are: {valid_domain_ids}
- ONLY output the JSON array. No other text.
- Generate at least {min_ideas} and at most {max_ideas} ideas.\
"""

SYSTEM_PROMPT = """\
You are an elite product strategist and indie hacker advisor. Your job is to \
analyze today's news and surface concrete, actionable app and software business \
opportunities that a skilled solo full-stack developer could realistically \
build and monetize.

## Your Analytical Framework

For each news item or cluster of related news, think through these lenses:

1. **Pain Point Detection**: What new problem, friction, or unmet need does \
this news reveal or amplify? Who feels this pain most acutely, and would they \
pay to make it go away?

2. **Behavioral Shift Spotting**: Is this news signaling a change in how \
people work, communicate, buy, learn, or live? What tools or products does \
that new behavior require that don't yet exist (or exist poorly)?

3. **Regulatory & Policy Arbitrage**: Does this news involve new laws, \
regulations, compliance requirements, or policy changes? These often create \
urgent, well-defined software needs overnight.

4. **Existing Market Gaps**: Does this news highlight that an existing \
category of software is failing its users, or that a large platform has made \
a change that leaves users stranded? Disgruntled users of a dominant product \
are goldmines.

5. **Picks-and-Shovels Thinking**: Even if you can't compete in the main \
trend, what tools, infrastructure, or adjacent services do the people riding \
this trend need?

6. **Timing & Urgency**: Is there a window of opportunity here? First-mover \
advantage, seasonal relevance, or a wave about to crest?

## Quality Filters — Apply Ruthlessly

ONLY include opportunities that meet ALL of these criteria:

- **Buildable solo in 1-6 weeks** as an MVP (not a polished product — just \
enough to validate and get first paying users)
- **Clear path to $1K-$5K/month** within 6 months via subscriptions, \
usage-based pricing, one-time purchases, or marketplace commissions
- **Specific and concrete** — not "an AI tool for productivity" but "a Chrome \
extension that automatically summarizes Slack threads into daily digests for \
managers"
- **Solves a problem people already know they have** — or that the news has \
just made them aware of
- **Technically feasible** with current APIs, models, and web technologies — \
no moonshots, no "requires training a custom foundation model"

## Output Format

Return a JSON array of 5-10 opportunities, ranked by a combined score of \
feasibility and potential impact. Each object must have:

{
  "rank": 1,
  "name": "Short, memorable product name",
  "one_liner": "One sentence: what it does and for whom",
  "news_trigger": "Which specific news item(s) inspired this — include \
source names",
  "the_problem": "2-3 sentences on the specific pain point or opportunity, \
written from the user's perspective",
  "target_audience": "Specific persona(s) — not 'businesses' but \
'e-commerce store owners doing $10K-$100K/month on Shopify'",
  "product_description": "3-5 sentences describing what the MVP actually \
does. Be specific about the core user workflow.",
  "revenue_model": "How it makes money. Include a suggested price point \
with reasoning.",
  "market_signal": "What evidence exists (from the news or your knowledge) \
that people would actually pay for this?",
  "competitive_landscape": "Who are the closest existing solutions? What's \
the gap you're exploiting?",
  "complexity": "low | medium | high",
  "estimated_build_time": "e.g., '2 weeks for MVP' — be realistic",
  "tech_stack": "Recommended technologies, APIs, and services to build this",
  "build_plan": [
    "Step 1: ...",
    "Step 2: ...",
    "Step 3: ...",
    "Step 4: ...",
    "Step 5: ..."
  ],
  "risks_and_challenges": "1-2 sentences on what could go wrong or what's \
hardest about this",
  "growth_hook": "How does this product acquire its first 100 users? Be \
specific — not 'marketing' but 'post in r/shopify and offer free onboarding \
for the first 20 users'"
}

## Important Instructions

- ONLY output the JSON array. No preamble, no markdown code fences, no \
explanation outside the JSON.
- Be brutally honest about complexity. If something sounds simple but has \
hidden complexity (payments, real-time sync, compliance), flag it.
- Prefer ideas with natural network effects, viral loops, or compounding \
value over time.
- Prefer ideas where AI/LLMs provide a genuine advantage, but don't force \
AI into every idea — sometimes a well-designed CRUD app wins.
- When multiple news items point to the same opportunity, synthesize them — \
don't create duplicate entries.
- Diversity of ideas matters: include a mix of B2B SaaS, B2C consumer apps, \
developer tools, marketplaces, and AI-powered products across the set.\
"""

_RESCORE_SYSTEM_PROMPT = """\
You are a ruthlessly honest startup advisor. You will be given a list of \
business opportunities. Rate each one from 1-10 on three dimensions:

- **Feasibility**: Can a solo developer really build this in the stated time? \
Consider hidden complexity, third-party dependencies, and edge cases.
- **Demand confidence**: How strong is the evidence that people would actually \
pay for this? Look for concrete signals, not wishful thinking.
- **Uniqueness**: How differentiated is this from existing solutions? A score \
of 1 means it's a clone of something well-established; 10 means nothing like \
it exists.

Return ONLY a JSON array (no markdown, no commentary). Each element must have:
{
  "rank": <original rank number>,
  "feasibility": <1-10>,
  "demand_confidence": <1-10>,
  "uniqueness": <1-10>
}\
"""

# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent / "data"
_PREVIOUS_IDEAS_FILE = _DATA_DIR / "previous_ideas.json"
_MEMORY_LOOKBACK_DAYS = 7
_MEMORY_PRUNE_DAYS = 30


class DailyMemory:
    """Track previously generated ideas to avoid repetition across runs.

    Stores ``{name, one_liner, date}`` entries in ``data/previous_ideas.json``.
    On load, entries older than 30 days are pruned automatically.
    """

    def __init__(
        self,
        path: Path = _PREVIOUS_IDEAS_FILE,
        lookback_days: int = _MEMORY_LOOKBACK_DAYS,
        prune_days: int = _MEMORY_PRUNE_DAYS,
    ) -> None:
        self._path = path
        self._lookback_days = lookback_days
        self._prune_days = prune_days
        self._entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._entries = []
            return
        try:
            raw = json.loads(self._path.read_text())
            if not isinstance(raw, list):
                raw = []
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read %s, starting fresh: %s", self._path, exc)
            raw = []
        cutoff = (date.today() - timedelta(days=self._prune_days)).isoformat()
        self._entries = [e for e in raw if e.get("date", "") >= cutoff]

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._entries, indent=2, ensure_ascii=False))
        logger.info("Saved %d idea(s) to %s", len(self._entries), self._path)

    def recent_ideas(self) -> list[dict]:
        cutoff = (date.today() - timedelta(days=self._lookback_days)).isoformat()
        return [e for e in self._entries if e.get("date", "") >= cutoff]

    def format_for_prompt(self) -> str:
        recent = self.recent_ideas()
        if not recent:
            return ""
        lines = ["## Previously suggested ideas (DO NOT repeat these)\n"]
        for entry in recent:
            lines.append(f"- {entry.get('name', '?')}: {entry.get('one_liner', '')}")
        return "\n".join(lines)

    def record(self, opportunities: list[dict]) -> None:
        today = date.today().isoformat()
        for opp in opportunities:
            self._entries.append({
                "name": opp.get("name", ""),
                "one_liner": opp.get("one_liner", ""),
                "date": today,
            })
        self.save()


# ---------------------------------------------------------------------------
# Token usage tracking
# ---------------------------------------------------------------------------

_total_input_tokens = 0
_total_output_tokens = 0
_total_cache_creation_tokens = 0
_total_cache_read_tokens = 0
_group_usages: list[dict] = []
_usage_lock = threading.Lock()


def get_token_usage() -> dict[str, int]:
    """Return cumulative token usage across all API calls this session."""
    return {
        "input_tokens": _total_input_tokens,
        "output_tokens": _total_output_tokens,
        "total_tokens": _total_input_tokens + _total_output_tokens,
        "cache_creation_tokens": _total_cache_creation_tokens,
        "cache_read_tokens": _total_cache_read_tokens,
    }


def get_group_usages() -> list[dict]:
    """Return per-group token usage records collected during the session."""
    with _usage_lock:
        return list(_group_usages)


def _reset_usage() -> None:
    """Reset all usage counters (useful for testing)."""
    global _total_input_tokens, _total_output_tokens
    global _total_cache_creation_tokens, _total_cache_read_tokens
    global _group_usages
    _total_input_tokens = 0
    _total_output_tokens = 0
    _total_cache_creation_tokens = 0
    _total_cache_read_tokens = 0
    with _usage_lock:
        _group_usages = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_articles(articles: list[dict]) -> str:
    """Render articles into a plain-text block for the prompt."""
    return "\n\n".join(
        f"Title: {a['title']}\n"
        f"Source: {a.get('source', '')}\n"
        f"Category: {a.get('category', 'tech_news')}\n"
        f"Description: {a['description']}\n"
        f"URL: {a['url']}\n"
        f"Published: {a['published_at']}"
        for a in articles
    )


def _extract_json(text: str) -> list[dict]:
    """Extract a JSON array from model output."""
    text = text.strip()

    # 1 — direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # 2 — fenced code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(1).strip())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # 3 — outermost [ … ]
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start : end + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    raise ValueError("Could not extract JSON array from model response")


def _extract_partial(text: str) -> list[dict]:
    """Last-resort fallback: extract individual JSON objects from broken output."""
    objects: list[dict] = []
    for match in re.finditer(r"\{[^{}]*\}", text):
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict) and "name" in obj and "one_liner" in obj:
                objects.append(obj)
        except json.JSONDecodeError:
            continue

    if not objects:
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict) and "name" in obj:
                            objects.append(obj)
                    except json.JSONDecodeError:
                        pass
                    start = -1

    return objects


def _validate(opportunities: list[dict]) -> list[dict]:
    """Validate full-schema opportunity dicts (legacy mode)."""
    valid: list[dict] = []
    for opp in opportunities:
        if not isinstance(opp, dict):
            logger.warning("Skipping non-dict entry: %r", opp)
            continue
        if not opp.get("name") and not opp.get("one_liner"):
            logger.warning("Skipping opportunity with no name or one_liner")
            continue
        missing = REQUIRED_FIELDS - opp.keys()
        if missing:
            logger.warning(
                "Opportunity %r missing fields: %s", opp.get("name"), missing,
            )
        valid.append(opp)
    return valid


def _validate_slim(opportunities: list[dict]) -> list[dict]:
    """Validate slim-schema opportunity dicts (grouped mode)."""
    valid: list[dict] = []
    for opp in opportunities:
        if not isinstance(opp, dict):
            logger.warning("Skipping non-dict entry: %r", opp)
            continue
        if not opp.get("name") and not opp.get("one_liner"):
            logger.warning("Skipping slim opportunity with no name or one_liner")
            continue
        missing = SLIM_REQUIRED_FIELDS - opp.keys()
        if missing:
            logger.warning(
                "Slim opportunity %r missing fields: %s", opp.get("name"), missing,
            )
        valid.append(opp)
    return valid


# ---------------------------------------------------------------------------
# Core API callers
# ---------------------------------------------------------------------------

def _call_claude_with_retry(
    *,
    system: str,
    messages: list[dict],
    model: str = _MODEL,
    max_tokens: int = _MAX_TOKENS,
) -> list[dict]:
    """Call Claude with a string system prompt and parse a JSON array response."""
    global _total_input_tokens, _total_output_tokens

    delay = _RETRY_BACKOFF
    last_raw: str = ""
    last_exc: Exception | None = None
    prefilled = messages[-1]["role"] == "assistant"

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.info(
                "Calling Claude (%s), attempt %d/%d...", model, attempt, _MAX_RETRIES,
            )
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
            usage = response.usage
            _total_input_tokens += usage.input_tokens
            _total_output_tokens += usage.output_tokens

            raw_text = response.content[0].text
            last_raw = (messages[-1]["content"] + raw_text) if prefilled else raw_text
            return _extract_json(last_raw)

        except anthropic.RateLimitError as exc:
            last_exc = exc
            logger.warning(
                "Rate-limited by Anthropic API (attempt %d/%d), retrying in %ds...",
                attempt, _MAX_RETRIES, delay,
            )
        except anthropic.NotFoundError:
            raise
        except anthropic.APIStatusError as exc:
            last_exc = exc
            logger.warning(
                "Anthropic API error %d (attempt %d/%d): %s. Retrying in %ds...",
                exc.status_code, attempt, _MAX_RETRIES, exc.message, delay,
            )
        except anthropic.APIConnectionError as exc:
            last_exc = exc
            logger.warning(
                "Connection error to Anthropic API (attempt %d/%d): %s. Retrying in %ds...",
                attempt, _MAX_RETRIES, exc, delay,
            )
        except ValueError:
            last_exc = None
            logger.warning(
                "JSON parsing failed on attempt %d/%d. Raw (first 500 chars): %s",
                attempt, _MAX_RETRIES, last_raw[:500],
            )

        if attempt < _MAX_RETRIES:
            time.sleep(delay)
            delay *= 2

    if last_raw:
        logger.warning(
            "All %d attempts failed for clean JSON. Attempting partial extraction...",
            _MAX_RETRIES,
        )
        partial = _extract_partial(last_raw)
        if partial:
            logger.info("Partial extraction recovered %d items.", len(partial))
            return partial
        logger.error("Partial extraction found nothing usable.")

    if last_exc is not None:
        raise last_exc
    raise ValueError(f"Could not extract JSON after {_MAX_RETRIES} attempts")


def _call_claude_cached(
    *,
    group: dict,
    static_prompt: str,
    variable_prompt: str,
    messages: list[dict],
    max_tokens: int = _GROUPED_MAX_TOKENS,
) -> list[dict]:
    """Call Claude with a cached static system prompt block.

    The static portion has ``cache_control: {type: ephemeral}`` so it is
    reused across all 4 group calls within the same 5-minute TTL window.
    The variable portion (group focus + dedup list) is appended uncached.
    """
    global _total_input_tokens, _total_output_tokens
    global _total_cache_creation_tokens, _total_cache_read_tokens

    model = group["model"]
    group_name = group["group_name"]
    group_id = group["group_id"]

    system_blocks = [
        {
            "type": "text",
            "text": static_prompt,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": variable_prompt,
        },
    ]

    delay = _RETRY_BACKOFF
    last_raw: str = ""
    last_exc: Exception | None = None
    prefilled = messages[-1]["role"] == "assistant"

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.info(
                "Calling Claude (%s) for group '%s', attempt %d/%d...",
                model, group_name, attempt, _MAX_RETRIES,
            )
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=messages,
            )

            usage = response.usage
            in_tok = usage.input_tokens
            out_tok = usage.output_tokens
            cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

            _total_input_tokens += in_tok
            _total_output_tokens += out_tok
            _total_cache_creation_tokens += cache_creation
            _total_cache_read_tokens += cache_read

            logger.info(
                "Group '%s' | Model: %s | Cache: %d/%d input tokens cached",
                group_name, model, cache_read, in_tok,
            )

            # Record per-group usage (thread-safe)
            with _usage_lock:
                _group_usages.append({
                    "group_id": group_id,
                    "model": model,
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "cache_read_tokens": cache_read,
                    "cache_creation_tokens": cache_creation,
                })

            raw_text = response.content[0].text
            last_raw = (messages[-1]["content"] + raw_text) if prefilled else raw_text
            return _extract_json(last_raw)

        except anthropic.RateLimitError as exc:
            last_exc = exc
            logger.warning(
                "Rate-limited (group '%s', attempt %d/%d), retrying in %ds...",
                group_name, attempt, _MAX_RETRIES, delay,
            )
        except anthropic.NotFoundError:
            raise
        except anthropic.APIStatusError as exc:
            last_exc = exc
            logger.warning(
                "Anthropic API error %d (group '%s', attempt %d/%d): %s. Retrying in %ds...",
                exc.status_code, group_name, attempt, _MAX_RETRIES, exc.message, delay,
            )
        except anthropic.APIConnectionError as exc:
            last_exc = exc
            logger.warning(
                "Connection error (group '%s', attempt %d/%d): %s. Retrying in %ds...",
                group_name, attempt, _MAX_RETRIES, exc, delay,
            )
        except ValueError:
            last_exc = None
            logger.warning(
                "JSON parsing failed for group '%s' (attempt %d/%d). Raw: %s",
                group_name, attempt, _MAX_RETRIES, last_raw[:500],
            )

        if attempt < _MAX_RETRIES:
            time.sleep(delay)
            delay *= 2

    if last_raw:
        logger.warning(
            "All %d attempts failed for group '%s'. Attempting partial extraction...",
            _MAX_RETRIES, group_name,
        )
        partial = _extract_partial(last_raw)
        if partial:
            logger.info(
                "Partial extraction recovered %d items for group '%s'.",
                len(partial), group_name,
            )
            return partial
        logger.error("Partial extraction found nothing usable for group '%s'.", group_name)

    if last_exc is not None:
        raise last_exc
    raise ValueError(f"Could not extract JSON for group '{group_name}' after {_MAX_RETRIES} attempts")


# ---------------------------------------------------------------------------
# Re-scoring (legacy mode only)
# ---------------------------------------------------------------------------

def _rescore(opportunities: list[dict]) -> list[dict]:
    """Call Claude a second time to independently score each opportunity."""
    summary = json.dumps(
        [
            {
                "rank": opp.get("rank"),
                "name": opp.get("name"),
                "one_liner": opp.get("one_liner"),
                "complexity": opp.get("complexity"),
                "estimated_build_time": opp.get("estimated_build_time"),
                "competitive_landscape": opp.get("competitive_landscape"),
                "market_signal": opp.get("market_signal"),
            }
            for opp in opportunities
        ],
        indent=2,
    )

    messages = [
        {"role": "user", "content": "Here are the opportunities to score:\n\n" + summary},
        {"role": "assistant", "content": "["},
    ]

    logger.info("Re-scoring %d opportunities...", len(opportunities))

    try:
        scores = _call_claude_with_retry(
            system=_RESCORE_SYSTEM_PROMPT,
            messages=messages,
            max_tokens=2048,
        )
    except (anthropic.APIError, ValueError) as exc:
        logger.warning("Re-scoring failed (%s), keeping original order.", exc)
        return opportunities

    score_map: dict[int, dict] = {}
    for entry in scores:
        if isinstance(entry, dict) and "rank" in entry:
            score_map[entry["rank"]] = entry

    for opp in opportunities:
        rank = opp.get("rank")
        s = score_map.get(rank, {})
        opp["feasibility"] = s.get("feasibility")
        opp["demand_confidence"] = s.get("demand_confidence")
        opp["uniqueness"] = s.get("uniqueness")
        vals = [
            v for v in (
                opp["feasibility"], opp["demand_confidence"], opp["uniqueness"]
            )
            if isinstance(v, (int, float))
        ]
        opp["avg_score"] = round(sum(vals) / len(vals), 1) if vals else None

    scored = [o for o in opportunities if o.get("avg_score") is not None]
    unscored = [o for o in opportunities if o.get("avg_score") is None]
    scored.sort(key=lambda o: o["avg_score"], reverse=True)

    merged = scored + unscored
    for i, opp in enumerate(merged, 1):
        opp["rank"] = i

    logger.info(
        "Re-scored %d/%d opportunities. Top: %s (%.1f), Bottom: %s (%.1f)",
        len(scored), len(opportunities),
        scored[0].get("name") if scored else "?",
        scored[0].get("avg_score", 0) if scored else 0,
        scored[-1].get("name") if scored else "?",
        scored[-1].get("avg_score", 0) if scored else 0,
    )
    return merged


# ---------------------------------------------------------------------------
# Grouped-mode prompt building
# ---------------------------------------------------------------------------

def _build_variable_prompt(group: dict, dedup_block: str) -> str:
    """Build the per-group variable portion of the system prompt (not cached)."""
    domain_ids = ", ".join(d["id"] for d in group["domains"])
    domain_details = "\n".join(
        f'- **{d["name"]}** ({d["id"]}): {d["description"]}'
        for d in group["domains"]
    )
    prompt = (
        f"## Your Focus for This Analysis\n\n"
        f"You are analyzing news for the {group['group_name']} group, "
        f"which covers these domains:\n\n"
        f"{domain_details}\n\n"
        f"Generate {group['min_ideas']} to {group['max_ideas']} ideas total "
        f"across these domains. Ensure each domain in the group has at least "
        f"2 ideas. The best ideas in each domain should be ranked highest.\n\n"
        f"Tag each idea with its primary_domain set to one of: {domain_ids}\n"
        f"If an idea also fits a domain from another group, include that domain "
        f"ID in the \"domains\" list as a secondary tag."
    )
    if dedup_block:
        prompt = prompt + "\n\n" + dedup_block
    return prompt


# ---------------------------------------------------------------------------
# Domain-aware prompt building (legacy)
# ---------------------------------------------------------------------------

def _build_domain_system_prompt(
    domain: dict,
    all_domains: list[dict],
    dedup_block: str,
) -> str:
    """Build a domain-specific system prompt from the legacy template."""
    domains_list = "\n".join(
        f"- **{d['id']}**: {d['name']} — {d['description']}"
        for d in all_domains
    )
    valid_ids = ", ".join(d["id"] for d in all_domains)

    prompt = _DOMAIN_SYSTEM_PROMPT_TEMPLATE.format(
        domain_name=domain["name"],
        domain_description=domain["description"],
        domains_list_with_descriptions=domains_list,
        domain_id=domain["id"],
        valid_domain_ids=valid_ids,
        min_ideas=domain["min_ideas"],
        max_ideas=domain["max_ideas"],
    )

    if dedup_block:
        prompt = prompt + "\n\n" + dedup_block

    return prompt


# ---------------------------------------------------------------------------
# Single-group analysis (called from ThreadPoolExecutor)
# ---------------------------------------------------------------------------

def _analyze_single_group(
    group: dict,
    news_items: list[dict],
    dedup_block: str,
) -> list[dict]:
    """Analyse news for one group. Runs in a thread pool."""
    articles_text = _format_articles(news_items)
    variable_prompt = _build_variable_prompt(group, dedup_block)

    user_message = (
        f"Here are {len(news_items)} recent news articles relevant to the "
        f"{group['group_name']} group. Identify the best app/SaaS business "
        f"opportunities across these domains.\n\n{articles_text}"
    )
    messages = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": "["},
    ]

    try:
        opportunities = _call_claude_cached(
            group=group,
            static_prompt=STATIC_SYSTEM_PROMPT,
            variable_prompt=variable_prompt,
            messages=messages,
        )
    except (anthropic.APIError, ValueError) as exc:
        logger.error("Failed to analyze group %s: %s", group["group_id"], exc)
        return []

    # Tag with group_id and ensure domain fields are set
    for opp in opportunities:
        opp["group_id"] = group["group_id"]
        opp["_source_model"] = group["model"]
        if "domains" not in opp or not opp["domains"]:
            opp["domains"] = []
        if "primary_domain" not in opp and group["domains"]:
            opp["primary_domain"] = group["domains"][0]["id"]
        # Ensure primary_domain is in the domains list
        pd = opp.get("primary_domain")
        if pd and pd not in opp["domains"]:
            opp["domains"].insert(0, pd)

    validated = _validate_slim(opportunities)
    logger.info(
        "Group '%s': parsed %d opportunities (%d valid).",
        group["group_id"], len(opportunities), len(validated),
    )
    return validated


# ---------------------------------------------------------------------------
# Single-domain analysis (legacy, called from ThreadPoolExecutor)
# ---------------------------------------------------------------------------

def _analyze_single_domain(
    domain: dict,
    news_items: list[dict],
    all_domains: list[dict],
    dedup_block: str,
    domain_index: int,
    total_domains: int,
) -> list[dict]:
    """Analyse news for a single domain (legacy mode)."""
    logger.info(
        "Analyzing %s... (%d/%d)",
        domain["name"], domain_index, total_domains,
    )

    system = _build_domain_system_prompt(domain, all_domains, dedup_block)
    articles_text = _format_articles(news_items)

    user_message = (
        f"Here are {len(news_items)} recent news articles relevant to the "
        f"{domain['name']} space. Identify the best app/SaaS business "
        f"opportunities in this domain.\n\n{articles_text}"
    )
    messages = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": "["},
    ]

    try:
        opportunities = _call_claude_with_retry(system=system, messages=messages)
    except (anthropic.APIError, ValueError) as exc:
        logger.error("Failed to analyze domain %s: %s", domain["id"], exc)
        return []

    for opp in opportunities:
        if "domains" not in opp:
            opp["domains"] = [domain["id"]]
        if "primary_domain" not in opp:
            opp["primary_domain"] = domain["id"]
        if domain["id"] not in opp.get("domains", []):
            opp["domains"].insert(0, domain["id"])

    validated = _validate(opportunities)
    logger.info(
        "Domain %s: parsed %d opportunities (%d valid).",
        domain["id"], len(opportunities), len(validated),
    )
    return validated


# ---------------------------------------------------------------------------
# Cross-domain deduplication (shared)
# ---------------------------------------------------------------------------

def _deduplicate_across_domains(opportunities: list[dict]) -> list[dict]:
    """Deduplicate by title similarity; merge domain tags, keep first occurrence."""
    unique: list[dict] = []
    seen_titles: list[str] = []

    for opp in opportunities:
        title = opp.get("name", "")
        if not title:
            unique.append(opp)
            continue

        merged = False
        for i, seen in enumerate(seen_titles):
            ratio = SequenceMatcher(None, title.lower(), seen.lower()).ratio()
            if ratio > _CROSS_DOMAIN_TITLE_SIMILARITY:
                existing = unique[i]
                for d in opp.get("domains", []):
                    if d not in existing.get("domains", []):
                        existing.setdefault("domains", []).append(d)
                merged = True
                break

        if not merged:
            seen_titles.append(title)
            unique.append(opp)

    if len(opportunities) != len(unique):
        logger.info(
            "Cross-domain dedup: %d → %d opportunities",
            len(opportunities), len(unique),
        )

    return unique


# ---------------------------------------------------------------------------
# Grouped analysis pipeline
# ---------------------------------------------------------------------------

def _analyze_grouped(
    news_by_group: dict[str, list[dict]],
    domain_groups: list[dict],
    memory: DailyMemory,
    dedup_block: str,
) -> list[dict]:
    """Run 4 grouped Claude calls (Sonnet first, then Haiku) with prompt caching."""
    all_opportunities: list[dict] = []

    # Order: Sonnet groups first to maximise cache hit within the 5-min TTL
    sonnet_groups = [g for g in domain_groups if "sonnet" in g["model"].lower()]
    haiku_groups = [g for g in domain_groups if "sonnet" not in g["model"].lower()]
    ordered = sonnet_groups + haiku_groups

    with ThreadPoolExecutor(max_workers=_GROUP_WORKERS) as executor:
        futures = {}
        for group in ordered:
            group_news = news_by_group.get(group["group_id"], [])
            if not group_news:
                logger.warning(
                    "No news items for group '%s' — skipping.", group["group_id"],
                )
                continue
            future = executor.submit(
                _analyze_single_group, group, group_news, dedup_block,
            )
            futures[future] = group

        for future in as_completed(futures):
            group = futures[future]
            try:
                result = future.result()
                all_opportunities.extend(result)
            except Exception as exc:
                logger.error("Group '%s' failed: %s", group["group_id"], exc)

    if not all_opportunities:
        return []

    # Before dedup: sort so Sonnet ideas appear first (will be kept over Haiku dupes)
    all_opportunities.sort(
        key=lambda o: (0 if "sonnet" in o.get("_source_model", "").lower() else 1)
    )

    all_opportunities = _deduplicate_across_domains(all_opportunities)

    # Remove internal tracking field
    for opp in all_opportunities:
        opp.pop("_source_model", None)

    # Sort by group order then rank
    group_order = {g["group_id"]: i for i, g in enumerate(domain_groups)}
    all_opportunities.sort(
        key=lambda o: (
            group_order.get(o.get("group_id", ""), 999),
            o.get("rank", 999),
        )
    )

    usage = get_token_usage()
    logger.info(
        "Total token usage: %d input, %d output, %d total "
        "(cache: %d created, %d read)",
        usage["input_tokens"], usage["output_tokens"], usage["total_tokens"],
        usage["cache_creation_tokens"], usage["cache_read_tokens"],
    )

    memory.record(all_opportunities)
    return all_opportunities


# ---------------------------------------------------------------------------
# Legacy domain-aware pipeline
# ---------------------------------------------------------------------------

def _analyze_domain_aware(
    news_by_domain: dict[str, list[dict]],
    domains: list[dict],
    memory: DailyMemory,
    dedup_block: str,
) -> list[dict]:
    """Original per-domain parallel analysis (7 domains, 3 workers)."""
    all_opportunities: list[dict] = []
    total = len(domains)

    with ThreadPoolExecutor(max_workers=_DOMAIN_WORKERS) as executor:
        futures = {}
        for idx, domain in enumerate(domains, 1):
            domain_news = news_by_domain.get(domain["id"], [])
            if not domain_news:
                logger.warning(
                    "No news items for domain %s — skipping.", domain["id"],
                )
                continue
            future = executor.submit(
                _analyze_single_domain,
                domain, domain_news, domains, dedup_block, idx, total,
            )
            futures[future] = domain

        for future in as_completed(futures):
            domain = futures[future]
            try:
                result = future.result()
                all_opportunities.extend(result)
            except Exception as exc:
                logger.error("Domain %s failed: %s", domain["id"], exc)

    if not all_opportunities:
        return []

    all_opportunities = _deduplicate_across_domains(all_opportunities)
    all_opportunities.sort(
        key=lambda o: (o.get("primary_domain", ""), o.get("rank", 999))
    )
    all_opportunities = _rescore(all_opportunities)

    usage = get_token_usage()
    logger.info(
        "Total token usage: %d input, %d output, %d total",
        usage["input_tokens"], usage["output_tokens"], usage["total_tokens"],
    )

    memory.record(all_opportunities)
    return all_opportunities


# ---------------------------------------------------------------------------
# Legacy single-prompt pipeline
# ---------------------------------------------------------------------------

def _analyze_legacy(
    news_items: list[dict],
    memory: DailyMemory,
    dedup_block: str,
) -> list[dict]:
    """Original single-prompt analysis (no domains)."""
    articles_text = _format_articles(news_items)
    system = SYSTEM_PROMPT
    if dedup_block:
        system = system + "\n\n" + dedup_block

    user_message = (
        f"Here are {len(news_items)} recent news articles. Identify the best "
        f"app/SaaS business opportunities.\n\n{articles_text}"
    )
    messages = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": "["},
    ]

    opportunities = _call_claude_with_retry(system=system, messages=messages)
    validated = _validate(opportunities)
    logger.info(
        "Parsed %d opportunities (%d valid).", len(opportunities), len(validated),
    )

    if not validated:
        return []

    validated = _rescore(validated)

    usage = get_token_usage()
    logger.info(
        "Total token usage: %d input, %d output, %d total",
        usage["input_tokens"], usage["output_tokens"], usage["total_tokens"],
    )

    memory.record(validated)
    return validated


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_opportunities(
    news_items: list[dict] | dict[str, list[dict]],
    domains: list[dict] | None = None,
) -> list[dict]:
    """Analyse news articles and return structured business opportunities.

    Supports three modes:
    - **Grouped** (new): ``news_items`` is a dict keyed by group_id,
      ``domains`` is the DOMAIN_GROUPS list (each element has ``group_id``).
      Uses 4 grouped calls with prompt caching.
    - **Domain-aware** (legacy): ``news_items`` is a dict keyed by domain_id,
      ``domains`` is the flat DOMAINS list (each element has ``id``).
    - **Legacy** (no domains): ``news_items`` is a flat list, single LLM call.
    """
    memory = DailyMemory()
    dedup_block = memory.format_for_prompt()
    if dedup_block:
        logger.info(
            "Injected %d previous ideas into prompt for dedup.",
            len(memory.recent_ideas()),
        )

    # --- Grouped mode (DOMAIN_GROUPS with group_id) ---
    if (
        domains
        and isinstance(domains, list)
        and domains[0].get("group_id")
        and isinstance(news_items, dict)
    ):
        return _analyze_grouped(news_items, domains, memory, dedup_block)

    # --- Legacy domain-aware mode (flat domains with id) ---
    if domains and isinstance(news_items, dict):
        return _analyze_domain_aware(news_items, domains, memory, dedup_block)

    # --- Legacy single-prompt mode ---
    items = news_items if isinstance(news_items, list) else []
    return _analyze_legacy(items, memory, dedup_block)


# ---------------------------------------------------------------------------
# On-demand idea expansion
# ---------------------------------------------------------------------------

def expand_idea(idea: dict, news_context: list[dict]) -> dict:
    """Expand a slim idea dict into the full schema.

    Makes a single Haiku call to generate the fields that were omitted from
    the slim daily schema:
      news_trigger, target_audience, product_description, market_signal,
      estimated_build_time, tech_stack, build_plan, risks_and_challenges

    Returns the original idea merged with the expansion fields. On failure,
    returns the original idea unchanged.
    """
    global _total_input_tokens, _total_output_tokens

    articles_text = (
        _format_articles(news_context[:20])
        if news_context
        else "No news context available."
    )

    expand_system = """\
You are a product strategist. You will be given a slim business opportunity \
and the news articles it was based on. Expand it with detailed analysis.

Return a JSON object with EXACTLY these fields:
{
  "news_trigger": "Which specific news item(s) inspired this — include source names",
  "target_audience": "Specific persona(s) — not 'businesses' but a specific role/segment",
  "product_description": "3-5 sentences describing what the MVP actually does and the core user workflow",
  "market_signal": "What evidence exists that people would actually pay for this?",
  "estimated_build_time": "e.g., '2 weeks for MVP' — be realistic",
  "tech_stack": "Recommended technologies, APIs, and services to build this",
  "build_plan": [
    "Step 1: ...",
    "Step 2: ...",
    "Step 3: ...",
    "Step 4: ...",
    "Step 5: ..."
  ],
  "risks_and_challenges": "1-2 sentences on what could go wrong or what's hardest about this"
}

ONLY output the JSON object. No preamble, no markdown fences, no explanation.\
"""

    user_message = (
        f"Here is the business opportunity to expand:\n\n"
        f"{json.dumps(idea, indent=2, ensure_ascii=False)}\n\n"
        f"Here are the relevant news articles:\n\n{articles_text}"
    )

    try:
        logger.info("Expanding idea '%s' with %s...", idea.get("name"), _EXPAND_MODEL)
        response = client.messages.create(
            model=_EXPAND_MODEL,
            max_tokens=2000,
            system=expand_system,
            messages=[{"role": "user", "content": user_message}],
        )
        _total_input_tokens += response.usage.input_tokens
        _total_output_tokens += response.usage.output_tokens

        text = response.content[0].text.strip()

        # Extract JSON object
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                result = json.loads(text[start : end + 1])
            else:
                raise ValueError("No JSON object found in expansion response")

        if not isinstance(result, dict):
            raise TypeError(f"Expected dict, got {type(result).__name__}")

        expanded = {**idea, **result}
        logger.info("Successfully expanded idea '%s'.", idea.get("name"))
        return expanded

    except Exception as exc:
        logger.error("Failed to expand idea '%s': %s", idea.get("name"), exc)
        return idea
