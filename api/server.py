"""Read-only FastAPI dashboard API for browsing saved digests."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

_DIGESTS_DIR = Path(__file__).resolve().parent.parent / "digests"

app = FastAPI(title="Opportunity Radar API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _load_digest(date: str) -> list[dict]:
    """Load a digest JSON file by date string (YYYY-MM-DD)."""
    path = _DIGESTS_DIR / f"{date}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"No digest found for {date}")
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read digest %s: %s", date, exc)
        raise HTTPException(status_code=500, detail="Failed to read digest file")


def _available_dates() -> list[str]:
    """Return sorted list of available digest dates (newest first)."""
    if not _DIGESTS_DIR.is_dir():
        return []
    dates = sorted(
        (p.stem for p in _DIGESTS_DIR.glob("*.json")),
        reverse=True,
    )
    return dates


@app.get("/api/digests")
def list_digests():
    """List available digest dates with opportunity counts."""
    results = []
    for date in _available_dates():
        try:
            data = json.loads((_DIGESTS_DIR / f"{date}.json").read_text())
            count = len(data) if isinstance(data, list) else 0
        except (json.JSONDecodeError, OSError):
            count = 0
        results.append({"date": date, "count": count})
    return results


@app.get("/api/digests/latest/opportunities")
def latest_digest():
    """Return the most recent digest's opportunities."""
    dates = _available_dates()
    if not dates:
        raise HTTPException(status_code=404, detail="No digests available")
    return _load_digest(dates[0])


@app.get("/api/digests/{date}")
def get_digest(date: str):
    """Return opportunities for a specific date (YYYY-MM-DD)."""
    return _load_digest(date)


@app.get("/api/stats")
def get_stats():
    """Aggregate stats across all digests."""
    dates = _available_dates()
    total_opportunities = 0
    all_complexities: dict[str, int] = {}
    score_sum = 0.0
    score_count = 0

    for date in dates:
        try:
            data = json.loads((_DIGESTS_DIR / f"{date}.json").read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, list):
            continue

        total_opportunities += len(data)
        for opp in data:
            complexity = opp.get("complexity", "unknown")
            all_complexities[complexity] = all_complexities.get(complexity, 0) + 1
            if "avg_score" in opp and opp["avg_score"] is not None:
                score_sum += opp["avg_score"]
                score_count += 1

    return {
        "total_digests": len(dates),
        "total_opportunities": total_opportunities,
        "avg_score": round(score_sum / score_count, 2) if score_count else None,
        "complexity_breakdown": all_complexities,
        "date_range": {
            "earliest": dates[-1] if dates else None,
            "latest": dates[0] if dates else None,
        },
    }
