"""Database module — Neon PostgreSQL persistence for Opportunity Radar digests.

All digest artifacts (opportunities, usage stats, news context) are stored in
a single 'digests' table keyed by (run_date, artifact).

Artifact values:
  'all'            — full opportunities list (replaces all.json)
  '<group_id>'     — per-group opportunities ('tech', 'lifestyle', 'commerce', 'civic')
  'usage'          — token usage and cost data (replaces usage.json)
  'news'           — news_by_group context dict (replaces news_by_group.json)
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict

import psycopg2
import psycopg2.extras

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost estimation (mirrors main.py — kept here to avoid circular imports)
# ---------------------------------------------------------------------------

_COST_RATES: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5-20250514": {
        "input": 3.00 / 1_000_000,
        "output": 15.00 / 1_000_000,
        "cached": 3.00 / 1_000_000 * 0.10,
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
) -> float:
    rates = _COST_RATES.get(model, _DEFAULT_RATES)
    regular_input = max(0, input_tokens - cache_read_tokens)
    return (
        regular_input * rates["input"]
        + cache_read_tokens * rates["cached"]
        + output_tokens * rates["output"]
    )


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_conn: psycopg2.extensions.connection | None = None
_schema_ensured = False

_DDL = """
CREATE TABLE IF NOT EXISTS digests (
    run_date    DATE         NOT NULL,
    artifact    TEXT         NOT NULL DEFAULT 'all',
    data        JSONB        NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (run_date, artifact)
);
CREATE INDEX IF NOT EXISTS idx_digests_run_date ON digests (run_date DESC);
"""

_UPSERT_SQL = """
INSERT INTO digests (run_date, artifact, data)
VALUES (%s, %s, %s)
ON CONFLICT (run_date, artifact)
DO UPDATE SET data = EXCLUDED.data, created_at = now()
"""


def _ensure_schema(conn: psycopg2.extensions.connection) -> None:
    global _schema_ensured
    if _schema_ensured:
        return
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()
    _schema_ensured = True
    logger.debug("DB schema ensured.")


def _get_conn() -> psycopg2.extensions.connection:
    """Return a cached psycopg2 connection, opening one if needed."""
    global _conn
    if _conn is None or _conn.closed:
        logger.debug("Opening DB connection...")
        _conn = psycopg2.connect(config.NEON_DATABASE_URL, sslmode="require")
        _ensure_schema(_conn)
    return _conn


def _upsert(
    conn: psycopg2.extensions.connection,
    run_date: str,
    artifact: str,
    data_obj: list | dict,
) -> None:
    """Upsert a single (run_date, artifact) row."""
    with conn.cursor() as cur:
        cur.execute(_UPSERT_SQL, (run_date, artifact, json.dumps(data_obj)))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_digest(
    run_date: str,
    opportunities: list[dict],
    domain_groups: list[dict] | None = None,
    group_usages: list[dict] | None = None,
    news_by_group: dict | None = None,
) -> None:
    """Persist all digest artifacts for a given run_date.

    Args:
        run_date:       ISO date string, e.g. '2026-02-23'.
        opportunities:  Full list of opportunity dicts.
        domain_groups:  DOMAIN_GROUPS list (used to identify group_id artifacts).
        group_usages:   Per-group token usage records from get_group_usages().
        news_by_group:  Dict keyed by group_id with news articles (for --expand).
    """
    conn = _get_conn()
    saved: list[str] = []

    # Always save the full list
    try:
        _upsert(conn, run_date, "all", opportunities)
        saved.append("all")
    except Exception as exc:
        logger.error("Failed to save artifact 'all': %s", exc)
        conn.rollback()

    # Per-group artifacts
    if domain_groups and any(g.get("group_id") for g in domain_groups):
        by_group: dict[str, list[dict]] = defaultdict(list)
        for opp in opportunities:
            gid = opp.get("group_id")
            if gid:
                by_group[gid].append(opp)
        for gid, opps in by_group.items():
            try:
                _upsert(conn, run_date, gid, opps)
                saved.append(gid)
            except Exception as exc:
                logger.error("Failed to save artifact '%s': %s", gid, exc)
                conn.rollback()

    # Usage artifact
    if group_usages:
        total_input = sum(g["input_tokens"] for g in group_usages)
        total_output = sum(g["output_tokens"] for g in group_usages)
        estimated_cost = sum(
            _estimate_cost(
                g["model"],
                g["input_tokens"],
                g["output_tokens"],
                g.get("cache_read_tokens", 0),
            )
            for g in group_usages
        )
        usage_data = {
            "date": run_date,
            "groups": group_usages,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "estimated_cost_usd": round(estimated_cost, 6),
        }
        try:
            _upsert(conn, run_date, "usage", usage_data)
            saved.append("usage")
        except Exception as exc:
            logger.error("Failed to save artifact 'usage': %s", exc)
            conn.rollback()

    # News context artifact
    if news_by_group:
        try:
            _upsert(conn, run_date, "news", news_by_group)
            saved.append("news")
        except Exception as exc:
            logger.error("Failed to save artifact 'news': %s", exc)
            conn.rollback()

    try:
        conn.commit()
        logger.info(
            "Saved digest to DB for %s: artifacts=[%s]",
            run_date,
            ", ".join(saved),
        )
    except Exception as exc:
        logger.error("Failed to commit digest transaction: %s", exc)
        conn.rollback()
        raise


def load_digest(
    run_date: str,
    artifact: str = "all",
) -> list[dict] | dict | None:
    """Load a single digest artifact from the DB.

    Returns the parsed JSONB data (list or dict), or None if not found.
    """
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT data FROM digests WHERE run_date = %s AND artifact = %s",
            (run_date, artifact),
        )
        row = cur.fetchone()
    if row is None:
        return None
    data = row[0]
    # psycopg2 returns JSONB as a Python object when using default cursor
    if isinstance(data, str):
        return json.loads(data)
    return data


def get_latest_run_date() -> str | None:
    """Return the most recent run_date as an ISO string, or None."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(run_date)::text FROM digests WHERE artifact = 'all'"
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return row[0]


def list_all_digests() -> list[tuple[str, list[dict]]]:
    """Return all 'all' rows as (date_str, opportunities) tuples, newest first."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT run_date::text, data FROM digests WHERE artifact = 'all'"
            " ORDER BY run_date DESC"
        )
        rows = cur.fetchall()
    result = []
    for run_date, data in rows:
        if isinstance(data, str):
            data = json.loads(data)
        result.append((run_date, data))
    return result


def list_usage_history(days: int = 7) -> list[tuple[str, dict]]:
    """Return usage rows for the last N days as (date_str, usage_dict) tuples."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT run_date::text, data FROM digests"
            " WHERE artifact = 'usage'"
            " AND run_date >= CURRENT_DATE - interval '%s days'"
            " ORDER BY run_date ASC",
            (days,),
        )
        rows = cur.fetchall()
    result = []
    for run_date, data in rows:
        if isinstance(data, str):
            data = json.loads(data)
        result.append((run_date, data))
    return result


def close() -> None:
    """Close the cached DB connection if open."""
    global _conn
    if _conn is not None and not _conn.closed:
        _conn.close()
        logger.debug("DB connection closed.")
    _conn = None
