"""Analyse news articles for business opportunities using Claude."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path

import anthropic

import config

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

_MODEL = "claude-sonnet-4-5-20250929"
_MAX_TOKENS = 16_384
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2  # seconds, doubled each retry

REQUIRED_FIELDS = {
    "rank", "name", "one_liner", "news_trigger", "the_problem",
    "target_audience", "product_description", "revenue_model",
    "market_signal", "competitive_landscape", "complexity",
    "estimated_build_time", "tech_stack", "build_plan",
    "risks_and_challenges", "growth_hook",
}

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

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

## What to Exclude

- Ideas requiring regulatory approval, licenses, or legal review to operate \
(e.g., fintech requiring money transmission licenses, health apps making \
clinical claims)
- Ideas that are purely content businesses with no software component
- Ideas where the primary moat is data that you don't have and can't acquire
- Ideas that have already been saturated by well-funded competitors unless you \
can identify a clear, underserved niche within that space
- Vague ideas — if you can't describe the core user action in one sentence, \
skip it

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


_DATA_DIR = Path(__file__).resolve().parent / "data"
_PREVIOUS_IDEAS_FILE = _DATA_DIR / "previous_ideas.json"
_MEMORY_LOOKBACK_DAYS = 7
_MEMORY_PRUNE_DAYS = 30

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
# DailyMemory — dedup against recent ideas
# ---------------------------------------------------------------------------

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

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        """Load and prune the ideas file."""
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
        """Write the current entries to disk (creates data/ if needed)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._entries, indent=2, ensure_ascii=False),
        )
        logger.info("Saved %d idea(s) to %s", len(self._entries), self._path)

    # -- query / update ------------------------------------------------------

    def recent_ideas(self) -> list[dict]:
        """Return ideas from the last ``lookback_days`` days."""
        cutoff = (date.today() - timedelta(days=self._lookback_days)).isoformat()
        return [e for e in self._entries if e.get("date", "") >= cutoff]

    def format_for_prompt(self) -> str:
        """Render recent ideas as a bullet list for inclusion in the prompt."""
        recent = self.recent_ideas()
        if not recent:
            return ""
        lines = ["## Previously suggested ideas (DO NOT repeat these)\n"]
        for entry in recent:
            lines.append(f"- {entry.get('name', '?')}: {entry.get('one_liner', '')}")
        return "\n".join(lines)

    def record(self, opportunities: list[dict]) -> None:
        """Append today's opportunities to the memory and save."""
        today = date.today().isoformat()
        for opp in opportunities:
            self._entries.append({
                "name": opp.get("name", ""),
                "one_liner": opp.get("one_liner", ""),
                "date": today,
            })
        self.save()


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
    """Extract a JSON array from model output.

    Tries, in order:
      1. Parse the whole response as JSON.
      2. Find a fenced code block and parse its contents.
      3. Find the outermost ``[`` ... ``]`` span and parse that.
    """
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
    """Last-resort fallback: extract individual JSON objects from broken output.

    If the model returned a truncated or malformed array, this finds every
    complete ``{ ... }`` block that looks like an opportunity and returns
    whatever it can salvage.
    """
    objects: list[dict] = []
    for match in re.finditer(r"\{[^{}]*\}", text):
        try:
            obj = json.loads(match.group(0))
            # Only keep objects that look like opportunities (have at least
            # a name and one_liner).
            if isinstance(obj, dict) and "name" in obj and "one_liner" in obj:
                objects.append(obj)
        except json.JSONDecodeError:
            continue

    # Handle nested braces (build_plan arrays inside objects) by trying
    # progressively larger spans.
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
    """Validate and clean up opportunity dicts.

    Logs warnings for missing fields but keeps the opportunity in the list.
    Strips any opportunities that lack both ``name`` and ``one_liner``.
    """
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _call_claude_with_retry(
    *,
    system: str,
    messages: list[dict],
    max_tokens: int = _MAX_TOKENS,
) -> list[dict]:
    """Call Claude and parse a JSON array response, with retry + fallback.

    This is the shared retry/parse engine used by both the main analysis call
    and the re-scoring call.
    """
    delay = _RETRY_BACKOFF
    last_raw: str = ""
    last_exc: Exception | None = None
    prefilled = messages[-1]["role"] == "assistant"

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.info("Calling Claude (%s), attempt %d/%d...",
                        _MODEL, attempt, _MAX_RETRIES)

            response = client.messages.create(
                model=_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )

            raw_text = response.content[0].text
            last_raw = (messages[-1]["content"] + raw_text) if prefilled else raw_text

            return _extract_json(last_raw)

        except anthropic.RateLimitError as exc:
            last_exc = exc
            logger.warning(
                "Rate-limited by Anthropic API (attempt %d/%d), "
                "retrying in %ds...", attempt, _MAX_RETRIES, delay,
            )
        except anthropic.NotFoundError:
            raise  # model ID wrong or resource gone — not transient
        except anthropic.APIStatusError as exc:
            last_exc = exc
            logger.warning(
                "Anthropic API error %d (attempt %d/%d): %s. "
                "Retrying in %ds...",
                exc.status_code, attempt, _MAX_RETRIES, exc.message, delay,
            )
        except anthropic.APIConnectionError as exc:
            last_exc = exc
            logger.warning(
                "Connection error to Anthropic API (attempt %d/%d): %s. "
                "Retrying in %ds...", attempt, _MAX_RETRIES, exc, delay,
            )
        except ValueError:
            last_exc = None
            logger.warning(
                "JSON parsing failed on attempt %d/%d. Raw response "
                "(first 500 chars): %s",
                attempt, _MAX_RETRIES, last_raw[:500],
            )

        if attempt < _MAX_RETRIES:
            time.sleep(delay)
            delay *= 2

    # -- Partial extraction fallback -----------------------------------------
    if last_raw:
        logger.warning(
            "All %d attempts failed for clean JSON. "
            "Attempting partial extraction...", _MAX_RETRIES,
        )
        partial = _extract_partial(last_raw)
        if partial:
            logger.info("Partial extraction recovered %d items.", len(partial))
            return partial
        logger.error("Partial extraction found nothing usable.")

    if last_exc is not None:
        raise last_exc
    raise ValueError(
        f"Could not extract JSON after {_MAX_RETRIES} attempts"
    )


def _rescore(opportunities: list[dict]) -> list[dict]:
    """Call Claude a second time to independently score each opportunity.

    Merges ``feasibility``, ``demand_confidence``, ``uniqueness``, and
    ``avg_score`` into each opportunity dict, then re-sorts descending by
    ``avg_score``.
    """
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
        {
            "role": "user",
            "content": (
                "Here are the opportunities to score:\n\n" + summary
            ),
        },
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

    # Build a lookup: original rank → scores
    score_map: dict[int, dict] = {}
    for entry in scores:
        if isinstance(entry, dict) and "rank" in entry:
            score_map[entry["rank"]] = entry

    # Merge scores into opportunities
    for opp in opportunities:
        rank = opp.get("rank")
        s = score_map.get(rank, {})
        opp["feasibility"] = s.get("feasibility")
        opp["demand_confidence"] = s.get("demand_confidence")
        opp["uniqueness"] = s.get("uniqueness")

        vals = [v for v in (opp["feasibility"], opp["demand_confidence"],
                            opp["uniqueness"]) if isinstance(v, (int, float))]
        opp["avg_score"] = round(sum(vals) / len(vals), 1) if vals else None

    scored = [o for o in opportunities if o.get("avg_score") is not None]
    unscored = [o for o in opportunities if o.get("avg_score") is None]

    scored.sort(key=lambda o: o["avg_score"], reverse=True)

    # Re-assign ranks after sorting
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


def analyze_opportunities(news_items: list[dict]) -> list[dict]:
    """Analyse news articles and return structured business opportunities.

    Pipeline:
      1. Load DailyMemory and inject recent ideas as a "do not repeat" list.
      2. Call Claude with the strategist system prompt (with retries).
      3. Validate results.
      4. Re-score each opportunity on feasibility / demand / uniqueness.
      5. Record today's ideas in memory and save.
    """
    memory = DailyMemory()

    # -- Build prompt --------------------------------------------------------
    articles_text = _format_articles(news_items)

    dedup_block = memory.format_for_prompt()
    system = SYSTEM_PROMPT
    if dedup_block:
        system = system + "\n\n" + dedup_block
        logger.info("Injected %d previous ideas into prompt for dedup.",
                     len(memory.recent_ideas()))

    user_message = (
        f"Here are {len(news_items)} recent news articles. Identify the best "
        f"app/SaaS business opportunities.\n\n{articles_text}"
    )

    messages = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": "["},
    ]

    # -- Call Claude ---------------------------------------------------------
    opportunities = _call_claude_with_retry(
        system=system, messages=messages,
    )
    validated = _validate(opportunities)
    logger.info("Parsed %d opportunities (%d valid).",
                len(opportunities), len(validated))

    if not validated:
        return []

    # -- Re-score ------------------------------------------------------------
    validated = _rescore(validated)

    # -- Remember today's ideas ----------------------------------------------
    memory.record(validated)

    return validated
