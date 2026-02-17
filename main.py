"""Opportunity Radar – fetch news, analyse for opportunities, send digest."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from news_fetcher import fetch_all_news, prepare_news_for_analysis
from analyzer import analyze_opportunities, get_token_usage
from digest_sender import send_opportunities_digest, _build_plain_text
from config import DOMAINS

logger = logging.getLogger("opportunity_radar")

DIGESTS_DIR = Path(__file__).resolve().parent / "digests"


def _configure_logging() -> None:
    """Set up root logging with a readable console format."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


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
        "--save",
        action="store_true",
        help="Save raw JSON output to digests/<date>/.",
    )
    parser.add_argument(
        "--resend",
        type=str,
        nargs="?",
        const="latest",
        metavar="FILE",
        help="Send email from a saved digest JSON (skips fetch & analysis). "
             "Pass a path or date (e.g. 2026-02-14); defaults to the most "
             "recent file in digests/. Combine with --dry-run to preview.",
    )
    parser.add_argument(
        "--domains",
        type=str,
        default=None,
        metavar="IDS",
        help="Comma-separated list of domain IDs to analyse. "
             "Example: --domains education_edtech,food_hospitality",
    )
    return parser.parse_args(argv)


def _resolve_domains(domain_arg: str | None) -> list[dict] | None:
    """Resolve the --domains flag to a filtered list of domain configs.

    Returns None if DOMAINS is empty/not set (legacy mode), or the full
    DOMAINS list if --domains is not specified.
    """
    if not DOMAINS:
        return None

    if domain_arg is None:
        return DOMAINS

    requested = {d.strip() for d in domain_arg.split(",") if d.strip()}
    valid_ids = {d["id"] for d in DOMAINS}
    invalid = requested - valid_ids
    if invalid:
        logger.warning("Unknown domain IDs (skipping): %s", ", ".join(sorted(invalid)))

    filtered = [d for d in DOMAINS if d["id"] in requested]
    if not filtered:
        logger.error("No valid domains selected. Available: %s",
                     ", ".join(sorted(valid_ids)))
        sys.exit(1)

    return filtered


def _save_json(opportunities: list[dict], domains: list[dict] | None) -> Path:
    """Write opportunities to digests/<date>/ directory.

    Creates:
      - digests/<date>/all.json — all opportunities
      - digests/<date>/<domain_id>.json — per-domain (if domains are active)

    Returns the directory path.
    """
    today_dir = DIGESTS_DIR / date.today().isoformat()
    today_dir.mkdir(parents=True, exist_ok=True)

    # Write all.json
    all_path = today_dir / "all.json"
    all_path.write_text(json.dumps(opportunities, indent=2, ensure_ascii=False))
    logger.info("Saved all.json (%d opportunities) to %s", len(opportunities), all_path)

    # Write per-domain files
    if domains:
        from collections import defaultdict
        by_domain: dict[str, list[dict]] = defaultdict(list)
        for opp in opportunities:
            pd = opp.get("primary_domain")
            if pd:
                by_domain[pd].append(opp)

        for domain_id, domain_opps in by_domain.items():
            domain_path = today_dir / f"{domain_id}.json"
            domain_path.write_text(
                json.dumps(domain_opps, indent=2, ensure_ascii=False),
            )
            logger.info("Saved %s (%d opportunities)", domain_path.name, len(domain_opps))

    return today_dir


def _resolve_digest_path(ref: str) -> Path:
    """Turn a --resend value into a concrete file path.

    Accepts:
      - "latest"        → newest all.json in digests/<date>/ or newest .json
      - a date string   → digests/<date>/all.json or digests/<date>.json
      - a file path     → used as-is
    """
    if ref == "latest":
        # Try new directory structure first
        date_dirs = sorted(
            [d for d in DIGESTS_DIR.iterdir() if d.is_dir()],
        ) if DIGESTS_DIR.exists() else []
        if date_dirs:
            all_json = date_dirs[-1] / "all.json"
            if all_json.is_file():
                return all_json

        # Fall back to old flat structure
        files = sorted(DIGESTS_DIR.glob("*.json")) if DIGESTS_DIR.exists() else []
        if not files:
            raise FileNotFoundError(f"No digest files found in {DIGESTS_DIR}")
        return files[-1]

    as_path = Path(ref)
    if as_path.is_file():
        return as_path

    # Try new directory structure: digests/<date>/all.json
    candidate_dir = DIGESTS_DIR / ref
    if candidate_dir.is_dir():
        all_json = candidate_dir / "all.json"
        if all_json.is_file():
            return all_json

    # Fall back to old flat structure: digests/<date>.json
    candidate = DIGESTS_DIR / f"{ref}.json"
    if candidate.is_file():
        return candidate

    raise FileNotFoundError(
        f"Cannot find digest: tried {as_path}, {candidate_dir}/all.json, and {candidate}",
    )


def resend_from_file(path: Path, *, dry_run: bool = False) -> None:
    """Load a saved digest JSON and send (or preview) the email."""
    opportunities = json.loads(path.read_text())
    logger.info("Loaded %d opportunities from %s", len(opportunities), path)

    # Infer domains if opportunities have domain tags
    has_domains = any(o.get("primary_domain") for o in opportunities)
    domains = DOMAINS if has_domains else None

    if dry_run:
        print()
        print(_build_plain_text(opportunities, domains))
    else:
        logger.info("Sending digest email to %s...",
                     __import__("config").DIGEST_RECIPIENT)
        send_opportunities_digest(opportunities, domains)
        logger.info("Digest email sent.")


def main(argv: list[str] | None = None) -> None:
    _configure_logging()
    args = _parse_args(argv)

    # --- Resend from saved digest (skips fetch & analysis) ---
    if args.resend is not None:
        path = _resolve_digest_path(args.resend)
        resend_from_file(path, dry_run=args.dry_run)
        logger.info("Done.")
        return

    # --- Resolve domains ---
    domains = _resolve_domains(args.domains)
    if domains:
        logger.info("Running with %d domains: %s",
                     len(domains), ", ".join(d["id"] for d in domains))

    # --- Fetch ---
    logger.info("Fetching news from all sources...")
    articles = fetch_all_news()
    logger.info("Collected %d articles after deduplication.", len(articles))

    if not articles:
        logger.warning("No articles found – nothing to analyse. Exiting.")
        return

    # --- Prepare news by domain ---
    if domains:
        news_by_domain = prepare_news_for_analysis(articles, domains)
        logger.info("Sending articles to Claude for domain-categorised analysis...")
        opportunities = analyze_opportunities(news_by_domain, domains)
    else:
        logger.info("Sending %d articles to Claude for opportunity analysis...",
                     len(articles))
        opportunities = analyze_opportunities(articles)

    logger.info("Identified %d opportunities.", len(opportunities))

    if not opportunities:
        logger.warning("Claude returned zero opportunities. Exiting.")
        return

    # --- Log token usage ---
    usage = get_token_usage()
    logger.info(
        "Token usage: %d input + %d output = %d total",
        usage["input_tokens"], usage["output_tokens"], usage["total_tokens"],
    )

    # --- Save (optional) ---
    if args.save:
        save_path = _save_json(opportunities, domains)
        logger.info("Saved raw JSON to %s", save_path)

    # --- Deliver ---
    if args.dry_run:
        logger.info("Dry-run mode – printing digest to console.")
        print()
        print(_build_plain_text(opportunities, domains))
    else:
        logger.info("Sending digest email to %s...",
                     __import__("config").DIGEST_RECIPIENT)
        send_opportunities_digest(opportunities, domains, news_count=len(articles))
        logger.info("Digest email sent.")

    logger.info("Done.")


if __name__ == "__main__":
    main()
