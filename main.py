"""Opportunity Radar – fetch news, analyse for opportunities, send digest."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

import db
from news_fetcher import fetch_all_news, prepare_news_for_analysis
from analyzer import analyze_opportunities, expand_idea, get_token_usage, get_group_usages
from digest_sender import send_opportunities_digest, _build_plain_text
from config import DOMAIN_GROUPS, DOMAINS

logger = logging.getLogger("opportunity_radar")

# ---------------------------------------------------------------------------
# Cost estimation rates (per token)
# ---------------------------------------------------------------------------

_COST_RATES: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5-20250514": {
        "input": 3.00 / 1_000_000,
        "output": 15.00 / 1_000_000,
        "cached": 3.00 / 1_000_000 * 0.10,  # 90 % discount
    },
    "claude-haiku-4-5-20251001": {
        "input": 0.80 / 1_000_000,
        "output": 4.00 / 1_000_000,
        "cached": 0.80 / 1_000_000 * 0.10,
    },
}
_DEFAULT_RATES = _COST_RATES["claude-sonnet-4-5-20250514"]


def _estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Estimate API cost for one call."""
    rates = _COST_RATES.get(model, _DEFAULT_RATES)
    regular_input = max(0, input_tokens - cache_read_tokens)
    return (
        regular_input * rates["input"]
        + cache_read_tokens * rates["cached"]
        + output_tokens * rates["output"]
    )


def _estimate_total_cost(group_usages: list[dict]) -> float:
    return sum(
        _estimate_cost(
            g["model"],
            g["input_tokens"],
            g["output_tokens"],
            g.get("cache_read_tokens", 0),
            g.get("cache_creation_tokens", 0),
        )
        for g in group_usages
    )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch news, identify business opportunities, send a digest.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the digest to the console instead of emailing it.",
    )
    parser.add_argument(
        "--resend",
        type=str,
        nargs="?",
        const="latest",
        metavar="DATE",
        help=(
            "Send email from a stored digest (skips fetch & analysis). "
            "Pass 'latest' or a date (e.g. 2026-02-14); defaults to 'latest'. "
            "Combine with --dry-run to preview."
        ),
    )
    parser.add_argument(
        "--domains",
        type=str,
        default=None,
        metavar="IDS",
        help=(
            "Comma-separated group IDs or domain IDs to analyse. "
            "Examples: --domains tech,civic  or  --domains ai_ml,education_edtech"
        ),
    )
    parser.add_argument(
        "--expand",
        type=str,
        default=None,
        metavar="IDEA",
        help=(
            "Expand a slim idea into the full schema. "
            "Pass idea name (fuzzy match) or rank number from the latest digest. "
            "Example: --expand 'RentFlow'  or  --expand 3"
        ),
    )
    parser.add_argument(
        "--cost",
        action="store_true",
        help="Show a cost summary for the last 7 days of runs and exit.",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Domain / group resolution
# ---------------------------------------------------------------------------

def _resolve_groups(domain_arg: str | None) -> list[dict] | None:
    """Resolve the --domains flag to a filtered list of group configs.

    Accepts group_ids (e.g. 'tech,civic') or domain_ids (e.g. 'ai_ml,food_hospitality').
    Returns None if DOMAIN_GROUPS is empty (triggers legacy mode).
    Returns the full DOMAIN_GROUPS list when --domains is not specified.
    """
    if not DOMAIN_GROUPS:
        return None

    if domain_arg is None:
        return DOMAIN_GROUPS

    requested = {d.strip() for d in domain_arg.split(",") if d.strip()}

    # Build sets for fast lookup
    all_group_ids = {g["group_id"] for g in DOMAIN_GROUPS}
    all_domain_ids = {d["id"] for g in DOMAIN_GROUPS for d in g["domains"]}

    # Determine which groups to include
    include_group_ids: set[str] = set()
    invalid: set[str] = set()

    for token in requested:
        if token in all_group_ids:
            include_group_ids.add(token)
        elif token in all_domain_ids:
            # Find parent group
            for g in DOMAIN_GROUPS:
                if any(d["id"] == token for d in g["domains"]):
                    include_group_ids.add(g["group_id"])
                    break
        else:
            invalid.add(token)

    if invalid:
        logger.warning("Unknown group/domain IDs (skipping): %s", ", ".join(sorted(invalid)))

    filtered = [g for g in DOMAIN_GROUPS if g["group_id"] in include_group_ids]
    if not filtered:
        logger.error(
            "No valid groups selected. Available group IDs: %s",
            ", ".join(sorted(all_group_ids)),
        )
        sys.exit(1)

    return filtered


# ---------------------------------------------------------------------------
# Resend (DB-backed)
# ---------------------------------------------------------------------------

def resend_from_db(ref: str, *, dry_run: bool = False) -> None:
    """Load a stored digest from the DB and send (or preview) the email.

    Args:
        ref: 'latest', a date string like '2026-02-21', or nothing (defaults
             to 'latest' via the argparse const).
    """
    if ref == "latest":
        date_str = db.get_latest_run_date()
        if date_str is None:
            logger.error("No digests found in the database.")
            sys.exit(1)
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", ref):
        date_str = ref
    else:
        logger.error(
            "Invalid --resend value %r. Use 'latest' or a date like 2026-02-21.", ref,
        )
        sys.exit(1)

    opportunities = db.load_digest(date_str)
    if opportunities is None:
        logger.error("No digest found in DB for %s.", date_str)
        sys.exit(1)

    logger.info("Loaded %d opportunities from DB for %s.", len(opportunities), date_str)

    # Detect layout: if ideas have group_id use DOMAIN_GROUPS, else legacy DOMAINS
    has_groups = any(o.get("group_id") for o in opportunities)
    domain_groups = DOMAIN_GROUPS if has_groups else (DOMAINS if any(
        o.get("primary_domain") for o in opportunities
    ) else None)

    if dry_run:
        print()
        print(_build_plain_text(opportunities, domain_groups))
    else:
        logger.info(
            "Sending digest email to %s...",
            __import__("config").DIGEST_RECIPIENT,
        )
        send_opportunities_digest(opportunities, domain_groups)
        logger.info("Digest email sent.")


# ---------------------------------------------------------------------------
# --expand
# ---------------------------------------------------------------------------

def _find_idea(opportunities: list[dict], query: str) -> dict | None:
    """Find an idea by rank number or fuzzy name match."""
    # Try rank number first
    try:
        rank = int(query)
        for opp in opportunities:
            if opp.get("rank") == rank:
                return opp
        logger.warning("No idea with rank %d found.", rank)
        return None
    except ValueError:
        pass

    # Fuzzy name match
    query_lower = query.lower()
    best: dict | None = None
    best_ratio = 0.0
    from difflib import SequenceMatcher
    for opp in opportunities:
        name = opp.get("name", "").lower()
        ratio = SequenceMatcher(None, query_lower, name).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = opp

    if best and best_ratio >= 0.5:
        logger.info(
            "Matched '%s' to idea '%s' (similarity %.2f).",
            query, best.get("name"), best_ratio,
        )
        return best

    logger.warning("Could not find idea matching '%s'.", query)
    return None


def handle_expand(query: str) -> None:
    """--expand: load latest digest from DB, find idea, expand with Haiku, print & save."""
    date_str = db.get_latest_run_date()
    if date_str is None:
        logger.error("No digests found in the database.")
        sys.exit(1)

    opportunities = db.load_digest(date_str)
    if not opportunities:
        logger.error("No opportunities found in DB for %s.", date_str)
        sys.exit(1)

    logger.info("Loaded %d opportunities from DB for %s.", len(opportunities), date_str)

    idea = _find_idea(opportunities, query)
    if idea is None:
        logger.error("Idea not found. Use --resend --dry-run to see available ideas.")
        sys.exit(1)

    logger.info("Expanding '%s'...", idea.get("name"))

    # Load saved news context from DB
    news_context: list[dict] = []
    news_by_group = db.load_digest(date_str, artifact="news")
    if news_by_group and isinstance(news_by_group, dict):
        gid = idea.get("group_id", "")
        news_context = news_by_group.get(gid, [])
        if news_context:
            logger.info(
                "Loaded %d news items for group '%s' from DB.",
                len(news_context), gid,
            )
    else:
        logger.info("No saved news context found in DB — expanding without news articles.")

    expanded = expand_idea(idea, news_context)

    # Print to console
    print()
    print("=" * 64)
    print(f"EXPANDED: {expanded.get('name', 'Unknown')}")
    print("=" * 64)
    for key, val in expanded.items():
        if key.startswith("_"):
            continue
        if isinstance(val, list):
            print(f"\n{key.upper().replace('_', ' ')}:")
            for item in val:
                print(f"  • {item}")
        else:
            print(f"\n{key.upper().replace('_', ' ')}:\n  {val}")

    # Save expanded idea locally (data/expanded/)
    slug = re.sub(r"[^a-z0-9]+", "-", expanded.get("name", "idea").lower()).strip("-")
    expanded_dir = Path(__file__).resolve().parent / "data" / "expanded"
    expanded_dir.mkdir(parents=True, exist_ok=True)
    out_path = expanded_dir / f"{slug}.json"
    out_path.write_text(json.dumps(expanded, indent=2, ensure_ascii=False))
    logger.info("Saved expanded idea to %s", out_path)


# ---------------------------------------------------------------------------
# --cost
# ---------------------------------------------------------------------------

def handle_cost() -> None:
    """--cost: print a 7-day cost summary from DB usage records."""
    rows = db.list_usage_history(days=7)
    daily_records = [record for _, record in rows]

    if not daily_records:
        print("No usage data found for the last 7 days.")
        return

    print()
    print("OPPORTUNITY RADAR — 7-DAY COST SUMMARY")
    print("=" * 60)

    total_cost = 0.0
    total_input = 0
    total_output = 0
    total_cache_read = 0

    for record in daily_records:
        run_date = record.get("date", "?")
        groups = record.get("groups", [])
        cost = record.get("estimated_cost_usd", 0.0)
        in_tok = record.get("total_input_tokens", 0)
        out_tok = record.get("total_output_tokens", 0)
        cache_read = sum(g.get("cache_read_tokens", 0) for g in groups)

        total_cost += cost
        total_input += in_tok
        total_output += out_tok
        total_cache_read += cache_read

        group_ids = ", ".join(g.get("group_id", "?") for g in groups)
        print(
            f"  {run_date}  |  {in_tok:>7,} in / {out_tok:>6,} out tokens"
            f"  |  ${cost:.4f}  |  groups: {group_ids}"
        )

    n_days = len(daily_records)
    avg_cost = total_cost / n_days if n_days else 0
    total_tokens = total_input + total_output
    cache_hit_rate = (total_cache_read / total_input * 100) if total_input else 0

    print("-" * 60)
    print(f"  7-day total:  ${total_cost:.4f}  |  {total_tokens:,} tokens")
    print(f"  Daily average: ${avg_cost:.4f}")
    print(f"  Cache hit rate: {cache_hit_rate:.1f}%")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    _configure_logging()
    args = _parse_args(argv)

    # --- Cost report ---
    if args.cost:
        handle_cost()
        return

    # --- Expand a saved idea ---
    if args.expand is not None:
        handle_expand(args.expand)
        return

    # --- Resend from DB ---
    if args.resend is not None:
        resend_from_db(args.resend, dry_run=args.dry_run)
        logger.info("Done.")
        return

    # --- Resolve groups ---
    domain_groups = _resolve_groups(args.domains)
    if domain_groups:
        group_names = ", ".join(g["group_id"] for g in domain_groups)
        logger.info(
            "Running with %d groups: %s", len(domain_groups), group_names,
        )

    # --- Fetch ---
    logger.info("Fetching news from all sources...")
    articles = fetch_all_news()
    logger.info("Collected %d articles after deduplication.", len(articles))

    if not articles:
        logger.warning("No articles found – nothing to analyse. Exiting.")
        return

    # --- Prepare ---
    if domain_groups:
        news_by_group = prepare_news_for_analysis(articles, domain_groups)
        logger.info("Sending articles to Claude for grouped analysis...")
        opportunities = analyze_opportunities(news_by_group, domain_groups)
    else:
        logger.info(
            "Sending %d articles to Claude for opportunity analysis...", len(articles),
        )
        opportunities = analyze_opportunities(articles)
        news_by_group = None

    logger.info("Identified %d opportunities.", len(opportunities))

    if not opportunities:
        logger.warning("Claude returned zero opportunities. Exiting.")
        return

    # --- Log token usage ---
    usage = get_token_usage()
    group_usages = get_group_usages()
    logger.info(
        "Token usage: %d input + %d output = %d total"
        " (cache: %d created, %d read)",
        usage["input_tokens"], usage["output_tokens"], usage["total_tokens"],
        usage["cache_creation_tokens"], usage["cache_read_tokens"],
    )

    estimated_cost: float | None = None
    if group_usages:
        estimated_cost = _estimate_total_cost(group_usages)
        logger.info("Estimated cost: $%.4f", estimated_cost)

    # Build usage summary dict for the email footer
    usage_summary: dict | None = None
    if group_usages or usage["input_tokens"]:
        usage_summary = {
            **usage,
            "estimated_cost_usd": estimated_cost,
        }

    # --- Save to DB (always) ---
    try:
        db.save_digest(
            run_date=date.today().isoformat(),
            opportunities=opportunities,
            domain_groups=domain_groups,
            group_usages=group_usages if group_usages else None,
            news_by_group=news_by_group if news_by_group else None,
        )
    except Exception as exc:
        logger.error("Failed to save digest to DB: %s", exc)
        # non-fatal — continue to deliver email

    # --- Deliver ---
    if args.dry_run:
        logger.info("Dry-run mode – printing digest to console.")
        print()
        print(_build_plain_text(opportunities, domain_groups))
    else:
        logger.info(
            "Sending digest email to %s...",
            __import__("config").DIGEST_RECIPIENT,
        )
        send_opportunities_digest(
            opportunities,
            domain_groups,
            news_count=len(articles),
            usage_summary=usage_summary,
        )
        logger.info("Digest email sent.")

    logger.info("Done.")


if __name__ == "__main__":
    main()
