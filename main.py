"""Opportunity Radar – fetch news, analyse for opportunities, send digest."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from news_fetcher import fetch_all_news
from analyzer import analyze_opportunities
from digest_sender import send_opportunities_digest, _build_plain_text

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
        help="Save raw JSON output to digests/<date>.json.",
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
    return parser.parse_args(argv)


def _save_json(opportunities: list[dict]) -> Path:
    """Write opportunities to digests/<YYYY-MM-DD>.json and return the path."""
    DIGESTS_DIR.mkdir(exist_ok=True)
    path = DIGESTS_DIR / f"{date.today().isoformat()}.json"
    path.write_text(json.dumps(opportunities, indent=2, ensure_ascii=False))
    return path


def _resolve_digest_path(ref: str) -> Path:
    """Turn a --resend value into a concrete file path.

    Accepts:
      - "latest"        → newest .json in digests/
      - a date string   → digests/<date>.json
      - a file path     → used as-is
    """
    if ref == "latest":
        files = sorted(DIGESTS_DIR.glob("*.json"))
        if not files:
            raise FileNotFoundError(f"No digest files found in {DIGESTS_DIR}")
        return files[-1]

    as_path = Path(ref)
    if as_path.is_file():
        return as_path

    # Treat as a date string, e.g. "2026-02-14"
    candidate = DIGESTS_DIR / f"{ref}.json"
    if candidate.is_file():
        return candidate

    raise FileNotFoundError(f"Cannot find digest: tried {as_path} and {candidate}")


def resend_from_file(path: Path, *, dry_run: bool = False) -> None:
    """Load a saved digest JSON and send (or preview) the email."""
    opportunities = json.loads(path.read_text())
    logger.info("Loaded %d opportunities from %s", len(opportunities), path)

    if dry_run:
        print()
        print(_build_plain_text(opportunities))
    else:
        logger.info("Sending digest email to %s...",
                     __import__("config").DIGEST_RECIPIENT)
        send_opportunities_digest(opportunities)
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

    # --- Fetch ---
    logger.info("Fetching news from all sources...")
    articles = fetch_all_news()
    logger.info("Collected %d articles after deduplication.", len(articles))

    if not articles:
        logger.warning("No articles found – nothing to analyse. Exiting.")
        return

    # --- Analyse ---
    logger.info("Sending %d articles to Claude for opportunity analysis...", len(articles))
    opportunities = analyze_opportunities(articles)
    logger.info("Identified %d opportunities.", len(opportunities))

    if not opportunities:
        logger.warning("Claude returned zero opportunities. Exiting.")
        return

    # --- Save (optional) ---
    if args.save:
        path = _save_json(opportunities)
        logger.info("Saved raw JSON to %s", path)

    # --- Deliver ---
    if args.dry_run:
        logger.info("Dry-run mode – printing digest to console.")
        print()
        print(_build_plain_text(opportunities))
    else:
        logger.info("Sending digest email to %s...",
                     __import__("config").DIGEST_RECIPIENT)
        send_opportunities_digest(opportunities)
        logger.info("Digest email sent.")

    logger.info("Done.")


if __name__ == "__main__":
    main()
