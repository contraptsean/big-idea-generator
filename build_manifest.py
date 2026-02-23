"""Build script: queries Neon DB for digests, writes static JSON files to frontend/.

Run before deploying, or set as the Netlify build command:
    python build_manifest.py

Requires NEON_DATABASE_URL in environment (set in Netlify UI or .env locally).
Deliberately does NOT import config.py to avoid env-var load failures in CI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

try:
    import psycopg2
except ImportError:
    psycopg2 = None  # type: ignore[assignment]

_ROOT = Path(__file__).resolve().parent
_FRONTEND = _ROOT / "frontend"
_DIGESTS_DST = _FRONTEND / "digests"


def _load_domains() -> list[dict]:
    """Load DOMAIN_GROUPS from config.py without triggering env-var loading.

    config.py loads env vars at import time (which fails in CI/Netlify
    where python-dotenv isn't installed and env vars are missing).
    Instead, we parse out just the DOMAIN_GROUPS list using ast.literal_eval.
    """
    import ast
    import re

    config_path = _ROOT / "config.py"
    if not config_path.is_file():
        return []

    try:
        source = config_path.read_text()
    except OSError:
        return []

    # Find the DOMAIN_GROUPS = [...] assignment and extract the list literal
    match = re.search(r"^DOMAIN_GROUPS\s*=\s*(\[.*?^])", source, re.DOTALL | re.MULTILINE)
    if not match:
        # Fall back to legacy DOMAINS list
        match = re.search(r"^DOMAINS\s*=\s*(\[.*?^])", source, re.DOTALL | re.MULTILINE)
    if not match:
        return []

    try:
        result = ast.literal_eval(match.group(1))
        return result if isinstance(result, list) else []
    except (ValueError, SyntaxError):
        return []


def _flatten_domains(groups: list[dict]) -> list[dict]:
    """Flatten DOMAIN_GROUPS into a flat list of domain dicts for display."""
    flat: list[dict] = []
    for group in groups:
        if "domains" in group:
            flat.extend(group["domains"])
        elif "id" in group:
            # Already a flat DOMAINS list
            flat.append(group)
    return flat


def _write_empty_manifest(domains_meta: list[dict]) -> None:
    out_path = _DIGESTS_DST / "manifest.json"
    out_path.write_text(json.dumps({
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


def build() -> None:
    # Clean and recreate destination
    import shutil
    if _DIGESTS_DST.exists():
        shutil.rmtree(_DIGESTS_DST)
    _DIGESTS_DST.mkdir(parents=True, exist_ok=True)

    raw_groups = _load_domains()
    flat_domains = _flatten_domains(raw_groups)
    domains_meta = [
        {"id": d["id"], "name": d["name"], "icon": d.get("icon", "")}
        for d in flat_domains
        if "id" in d and "name" in d
    ]

    database_url = os.environ.get("NEON_DATABASE_URL")
    if not database_url or psycopg2 is None:
        reason = "NEON_DATABASE_URL not set" if not database_url else "psycopg2 not installed"
        print(f"{reason} — writing empty manifest.")
        _write_empty_manifest(domains_meta)
        return

    # Query DB for all 'all' artifact rows
    try:
        conn = psycopg2.connect(database_url, sslmode="require")
        cur = conn.cursor()
        cur.execute(
            "SELECT run_date::text, data FROM digests"
            " WHERE artifact = 'all'"
            " ORDER BY run_date ASC"
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        print(f"DB query failed: {exc} — writing empty manifest.")
        _write_empty_manifest(domains_meta)
        return

    manifest: list[dict] = []
    total_opportunities = 0
    score_sum = 0.0
    score_count = 0
    complexity_breakdown: dict[str, int] = {}
    domain_breakdown: dict[str, int] = {}

    for date_str, opportunities in rows:
        # psycopg2 returns JSONB as Python object; guard against string fallback
        if isinstance(opportunities, str):
            try:
                opportunities = json.loads(opportunities)
            except json.JSONDecodeError:
                opportunities = []

        if not isinstance(opportunities, list):
            opportunities = []

        # Write flat date file for frontend consumption
        dst = _DIGESTS_DST / f"{date_str}.json"
        dst.write_text(json.dumps(opportunities, indent=2))

        count = len(opportunities)
        manifest.append({"date": date_str, "count": count})
        total_opportunities += count

        for opp in opportunities:
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
