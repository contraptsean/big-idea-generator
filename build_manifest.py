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


def build():
    # Clean and recreate destination
    if _DIGESTS_DST.exists():
        shutil.rmtree(_DIGESTS_DST)
    _DIGESTS_DST.mkdir(parents=True, exist_ok=True)

    manifest = []
    total_opportunities = 0
    score_sum = 0.0
    score_count = 0
    complexity_breakdown: dict[str, int] = {}

    if not _DIGESTS_SRC.is_dir():
        print("No digests/ directory found — writing empty manifest.")
        (_DIGESTS_DST / "manifest.json").write_text(json.dumps({
            "digests": [],
            "stats": {
                "total_digests": 0,
                "total_opportunities": 0,
                "avg_score": None,
                "complexity_breakdown": {},
                "date_range": {"earliest": None, "latest": None},
            },
        }, indent=2))
        return

    for src_file in sorted(_DIGESTS_SRC.glob("*.json")):
        # Copy file to frontend/digests/
        shutil.copy2(src_file, _DIGESTS_DST / src_file.name)

        try:
            data = json.loads(src_file.read_text())
        except (json.JSONDecodeError, OSError):
            data = []

        count = len(data) if isinstance(data, list) else 0
        manifest.append({"date": src_file.stem, "count": count})

        total_opportunities += count
        if isinstance(data, list):
            for opp in data:
                c = opp.get("complexity", "unknown")
                complexity_breakdown[c] = complexity_breakdown.get(c, 0) + 1
                if opp.get("avg_score") is not None:
                    score_sum += opp["avg_score"]
                    score_count += 1

    # Sort newest first
    manifest.sort(key=lambda d: d["date"], reverse=True)

    dates = [d["date"] for d in manifest]
    output = {
        "digests": manifest,
        "stats": {
            "total_digests": len(manifest),
            "total_opportunities": total_opportunities,
            "avg_score": round(score_sum / score_count, 2) if score_count else None,
            "complexity_breakdown": complexity_breakdown,
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


if __name__ == "__main__":
    build()
