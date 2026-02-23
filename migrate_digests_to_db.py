"""One-time migration script: import existing digests/ directory into Neon PostgreSQL.

Usage:
    source venv/bin/activate
    python migrate_digests_to_db.py

Requires NEON_DATABASE_URL in .env or environment.
Safe to re-run — uses ON CONFLICT DO UPDATE (upsert).

After verifying the migration, remove the directory from git:
    git rm -r digests/
    echo "digests/" >> .gitignore
    git add .gitignore
    git commit -m "chore: remove digests/ directory, now stored in Neon PostgreSQL"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import db  # noqa: E402  (after load_dotenv so NEON_DATABASE_URL is available)

_DIGESTS_DIR = Path(__file__).resolve().parent / "digests"

# Artifacts stored in separate files — not treated as per-group data
_SPECIAL_ARTIFACTS = {"all", "usage", "news_by_group"}


def migrate() -> None:
    if not _DIGESTS_DIR.is_dir():
        print(f"No digests/ directory found at {_DIGESTS_DIR}. Nothing to migrate.")
        sys.exit(0)

    date_dirs = sorted(
        d for d in _DIGESTS_DIR.iterdir()
        if d.is_dir() and _is_date(d.name)
    )

    if not date_dirs:
        print("No dated subdirectories found in digests/.")
        sys.exit(0)

    print(f"Migrating {len(date_dirs)} digest directories to Neon...\n")
    total_artifacts = 0
    errors = 0

    for date_dir in date_dirs:
        run_date = date_dir.name
        saved: list[str] = []

        # all.json → artifact='all'
        all_path = date_dir / "all.json"
        if all_path.is_file():
            try:
                data = json.loads(all_path.read_text())
                _upsert(run_date, "all", data)
                saved.append("all")
            except Exception as exc:
                print(f"  ERROR {run_date}/all.json: {exc}")
                errors += 1

        # usage.json → artifact='usage'
        usage_path = date_dir / "usage.json"
        if usage_path.is_file():
            try:
                data = json.loads(usage_path.read_text())
                _upsert(run_date, "usage", data)
                saved.append("usage")
            except Exception as exc:
                print(f"  ERROR {run_date}/usage.json: {exc}")
                errors += 1

        # news_by_group.json → artifact='news'
        news_path = date_dir / "news_by_group.json"
        if news_path.is_file():
            try:
                data = json.loads(news_path.read_text())
                _upsert(run_date, "news", data)
                saved.append("news")
            except Exception as exc:
                print(f"  ERROR {run_date}/news_by_group.json: {exc}")
                errors += 1

        # Per-group / per-domain .json files → artifact=<stem>
        for json_file in sorted(date_dir.glob("*.json")):
            stem = json_file.stem
            if stem in _SPECIAL_ARTIFACTS:
                continue  # already handled above
            try:
                data = json.loads(json_file.read_text())
                _upsert(run_date, stem, data)
                saved.append(stem)
            except Exception as exc:
                print(f"  ERROR {run_date}/{json_file.name}: {exc}")
                errors += 1

        total_artifacts += len(saved)
        print(f"  {run_date}  →  [{', '.join(saved)}]")

    # Commit everything
    try:
        conn = db._get_conn()
        conn.commit()
    except Exception as exc:
        print(f"\nFailed to commit: {exc}")
        sys.exit(1)

    print(f"\nMigration complete: {len(date_dirs)} dates, {total_artifacts} artifacts saved.")
    if errors:
        print(f"  {errors} error(s) encountered — check output above.")
    print()
    print("Next steps:")
    print("  1. Verify with: python main.py --resend latest --dry-run")
    print("  2. Then remove from git:")
    print("       git rm -r digests/")
    print('       echo "digests/" >> .gitignore')
    print("       git add .gitignore")
    print('       git commit -m "chore: remove digests/ directory, now stored in Neon PostgreSQL"')


def _is_date(name: str) -> bool:
    """Check if a directory name looks like YYYY-MM-DD."""
    import re
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", name))


def _upsert(run_date: str, artifact: str, data: list | dict) -> None:
    """Upsert a single artifact row via db module."""
    conn = db._get_conn()
    with conn.cursor() as cur:
        cur.execute(db._UPSERT_SQL, (run_date, artifact, json.dumps(data)))


if __name__ == "__main__":
    migrate()
