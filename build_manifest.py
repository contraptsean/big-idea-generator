"""Build script: copies digests into frontend/ and generates manifest.json.

Run before deploying, or set as the Netlify build command:
    python build_manifest.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_DIGESTS_SRC = _ROOT / "digests"
_FRONTEND = _ROOT / "frontend"
_DIGESTS_DST = _FRONTEND / "digests"


def _load_domains() -> list[dict]:
    """Import DOMAINS from config, returning [] on failure."""
    try:
        import config
        return getattr(config, "DOMAINS", []) or []
    except Exception:
        return []


def build():
    # Clean and recreate destination
    if _DIGESTS_DST.exists():
        shutil.rmtree(_DIGESTS_DST)
    _DIGESTS_DST.mkdir(parents=True, exist_ok=True)

    domains = _load_domains()
    # Build a serialisable list with just what the frontend needs
    domains_meta = [
        {"id": d["id"], "name": d["name"], "icon": d["icon"]}
        for d in domains
    ]

    manifest = []
    total_opportunities = 0
    score_sum = 0.0
    score_count = 0
    complexity_breakdown: dict[str, int] = {}
    domain_breakdown: dict[str, int] = {}

    if not _DIGESTS_SRC.is_dir():
        print("No digests/ directory found — writing empty manifest.")
        (_DIGESTS_DST / "manifest.json").write_text(json.dumps({
            "digests": [],
            "domains": domains_meta,
            "stats": {
                "total_digests": 0,
                "total_opportunities": 0,
                "avg_score": None,
                "complexity_breakdown": {},
                "domain_breakdown": {},
                "date_range": {"earliest": None, "latest": None},
            },
        }, indent=2))
        return

    # Collect digest dates from both directory-based (new) and flat (old) layouts
    seen_dates: set[str] = set()

    # New layout: digests/<date>/all.json
    for date_dir in sorted(_DIGESTS_SRC.iterdir()):
        if not date_dir.is_dir():
            continue
        all_json = date_dir / "all.json"
        if not all_json.is_file():
            continue
        date_str = date_dir.name
        if date_str in seen_dates:
            continue
        seen_dates.add(date_str)

        # Copy as flat file for frontend consumption
        dst = _DIGESTS_DST / f"{date_str}.json"
        shutil.copy2(all_json, dst)

        try:
            data = json.loads(all_json.read_text())
        except (json.JSONDecodeError, OSError):
            data = []

        count = len(data) if isinstance(data, list) else 0
        manifest.append({"date": date_str, "count": count})

        total_opportunities += count
        if isinstance(data, list):
            for opp in data:
                c = opp.get("complexity", "unknown")
                complexity_breakdown[c] = complexity_breakdown.get(c, 0) + 1
                pd = opp.get("primary_domain")
                if pd:
                    domain_breakdown[pd] = domain_breakdown.get(pd, 0) + 1
                if opp.get("avg_score") is not None:
                    score_sum += opp["avg_score"]
                    score_count += 1

    # Old layout: digests/<date>.json (flat files)
    for src_file in sorted(_DIGESTS_SRC.glob("*.json")):
        date_str = src_file.stem
        if date_str in seen_dates:
            continue
        seen_dates.add(date_str)

        shutil.copy2(src_file, _DIGESTS_DST / src_file.name)

        try:
            data = json.loads(src_file.read_text())
        except (json.JSONDecodeError, OSError):
            data = []

        count = len(data) if isinstance(data, list) else 0
        manifest.append({"date": date_str, "count": count})

        total_opportunities += count
        if isinstance(data, list):
            for opp in data:
                c = opp.get("complexity", "unknown")
                complexity_breakdown[c] = complexity_breakdown.get(c, 0) + 1
                pd = opp.get("primary_domain")
                if pd:
                    domain_breakdown[pd] = domain_breakdown.get(pd, 0) + 1
                if opp.get("avg_score") is not None:
                    score_sum += opp["avg_score"]
                    score_count += 1

    # Sort newest first
    manifest.sort(key=lambda d: d["date"], reverse=True)

    dates = [d["date"] for d in manifest]
    output = {
        "digests": manifest,
        "domains": domains_meta,
        "stats": {
            "total_digests": len(manifest),
            "total_opportunities": total_opportunities,
            "avg_score": round(score_sum / score_count, 2) if score_count else None,
            "complexity_breakdown": complexity_breakdown,
            "domain_breakdown": domain_breakdown,
            "date_range": {
                "earliest": dates[-1] if dates else None,
                "latest": dates[0] if dates else None,
            },
        },
    }

    out_path = _DIGESTS_DST / "manifest.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Manifest written to {out_path}")
    print(f"  {len(manifest)} digest(s), {total_opportunities} total opportunities")
    if domains_meta:
        print(f"  {len(domains_meta)} domains configured")


if __name__ == "__main__":
    build()
