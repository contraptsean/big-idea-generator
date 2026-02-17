"""Build and send opportunity digest emails over SMTP/TLS."""

from __future__ import annotations

import smtplib
from collections import defaultdict
from datetime import date, datetime, timezone
from email.message import EmailMessage
from html import escape

import config

# ---------------------------------------------------------------------------
# Complexity badge colours
# ---------------------------------------------------------------------------

_COMPLEXITY_STYLES = {
    "low": ("#e6f4ea", "#1e7e34"),      # green tint
    "medium": ("#fff8e1", "#c77c00"),    # amber tint
    "high": ("#fce8e6", "#c62828"),      # red tint
}


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------

def _build_domain_lookup(domains: list[dict] | None) -> dict[str, dict]:
    """Build a dict of domain_id → domain config for quick lookup."""
    if not domains:
        return {}
    return {d["id"]: d for d in domains}


def _group_by_domain(
    opportunities: list[dict],
    domains: list[dict] | None,
) -> list[tuple[dict | None, list[dict]]]:
    """Group opportunities by primary_domain, preserving domain order.

    Returns a list of (domain_config_or_None, [opportunities]) tuples.
    Domains with no ideas are omitted. Ideas without a primary_domain
    are grouped under None (shown as "General").
    """
    if not domains:
        return [(None, opportunities)]

    domain_lookup = _build_domain_lookup(domains)
    grouped: dict[str | None, list[dict]] = defaultdict(list)

    for opp in opportunities:
        pd = opp.get("primary_domain")
        grouped[pd].append(opp)

    result: list[tuple[dict | None, list[dict]]] = []
    # Follow the canonical domain order
    for d in domains:
        if d["id"] in grouped:
            result.append((d, grouped[d["id"]]))
    # Add any unrecognised / None domain ideas at the end
    for key in grouped:
        if key is None or key not in domain_lookup:
            result.append((None, grouped[key]))

    return result


def _get_cross_domain_ideas(opportunities: list[dict]) -> list[dict]:
    """Return ideas tagged with 2+ domains."""
    return [o for o in opportunities if len(o.get("domains", [])) >= 2]


# ---------------------------------------------------------------------------
# Plain-text builder
# ---------------------------------------------------------------------------

def _build_plain_text(
    opportunities: list[dict],
    domains: list[dict] | None = None,
) -> str:
    """Render opportunities as readable plain text, grouped by domain."""
    lines: list[str] = []
    today_str = date.today().strftime("%B %d, %Y")
    lines.append(f"OPPORTUNITY RADAR - {today_str}")
    lines.append("=" * 64)

    grouped = _group_by_domain(opportunities, domains)
    n_domains = len(grouped)

    if domains:
        lines.append(f"{len(opportunities)} ideas across {n_domains} domains")
        lines.append("")

        # Cross-domain highlights
        cross = _get_cross_domain_ideas(opportunities)
        if cross:
            domain_lookup = _build_domain_lookup(domains)
            lines.append("CROSS-DOMAIN IDEAS:")
            for opp in cross:
                domain_names = [
                    domain_lookup.get(d, {}).get("name", d)
                    for d in opp.get("domains", [])
                ]
                lines.append(f"  - {opp.get('name', '?')} ({', '.join(domain_names)})")
            lines.append("")

    for domain_cfg, domain_opps in grouped:
        if domain_cfg:
            icon = domain_cfg.get("icon", "")
            name = domain_cfg.get("name", "Unknown")
            lines.append(f"{icon} {name} ({len(domain_opps)} ideas)")
        else:
            lines.append(f"General ({len(domain_opps)} ideas)")
        lines.append("-" * 64)

        for opp in domain_opps:
            rank = opp.get("rank", "?")
            lines.append("")
            lines.append(f"#{rank}  {opp.get('name', 'Untitled')}")
            lines.append(f"     {opp.get('one_liner', '')}")
            if opp.get("domains"):
                lines.append(f"  Domains:       {', '.join(opp['domains'])}")
            lines.append("")
            lines.append(f"  News trigger:  {opp.get('news_trigger', 'N/A')}")
            lines.append(f"  The problem:   {opp.get('the_problem', 'N/A')}")
            lines.append(f"  Audience:      {opp.get('target_audience', 'N/A')}")
            lines.append(f"  Product:       {opp.get('product_description', 'N/A')}")
            lines.append(f"  Revenue:       {opp.get('revenue_model', 'N/A')}")
            lines.append(f"  Market signal: {opp.get('market_signal', 'N/A')}")
            lines.append(f"  Competition:   {opp.get('competitive_landscape', 'N/A')}")
            lines.append(f"  Complexity:    {opp.get('complexity', 'N/A')}")
            lines.append(f"  Build time:    {opp.get('estimated_build_time', 'N/A')}")
            lines.append(f"  Tech stack:    {opp.get('tech_stack', 'N/A')}")
            lines.append(f"  Risks:         {opp.get('risks_and_challenges', 'N/A')}")
            lines.append(f"  Growth hook:   {opp.get('growth_hook', 'N/A')}")
            if opp.get("avg_score") is not None:
                lines.append(
                    f"  Scores:        Feasibility {opp.get('feasibility', '?')}/10  "
                    f"| Demand {opp.get('demand_confidence', '?')}/10  "
                    f"| Uniqueness {opp.get('uniqueness', '?')}/10  "
                    f"(avg {opp['avg_score']})"
                )
            lines.append("")
            lines.append("  Build plan:")
            for step_num, step in enumerate(opp.get("build_plan", []), 1):
                lines.append(f"    {step_num}. {step}")
            lines.append("-" * 64)

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _detail_row(label: str, value: str) -> str:
    """Render a single label/value row for the detail grid."""
    return (
        f'<tr>'
        f'<td style="padding:5px 10px 5px 0;font-size:11px;text-transform:uppercase;'
        f'color:#9ca3af;letter-spacing:0.4px;vertical-align:top;white-space:nowrap;">'
        f'{escape(label)}</td>'
        f'<td style="padding:5px 0;font-size:13px;color:#374151;">'
        f'{escape(value)}</td>'
        f'</tr>'
    )


def _build_card_html(opp: dict, domain_lookup: dict[str, dict]) -> str:
    """Render a single opportunity card as HTML."""
    complexity = opp.get("complexity", "medium").lower()
    bg, fg = _COMPLEXITY_STYLES.get(complexity, _COMPLEXITY_STYLES["medium"])

    # Build plan as numbered list
    steps_html = ""
    for step in opp.get("build_plan", []):
        steps_html += (
            f'<li style="margin-bottom:6px;color:#374151;">'
            f'{escape(str(step))}</li>'
        )

    # Score pills (only rendered when re-scoring has run)
    scores_html = ""
    if opp.get("avg_score") is not None:
        def _pill(label: str, value: object) -> str:
            return (
                f'<span style="display:inline-block;padding:3px 8px;'
                f'margin-right:6px;font-size:12px;border-radius:6px;'
                f'background:#eef2ff;color:#4338ca;">'
                f'{label} <b>{value}</b>/10</span>'
            )
        scores_html = (
            '<div style="margin-bottom:16px;">'
            + _pill("Feasibility", opp.get("feasibility", "?"))
            + _pill("Demand", opp.get("demand_confidence", "?"))
            + _pill("Uniqueness", opp.get("uniqueness", "?"))
            + f'<span style="display:inline-block;padding:3px 8px;'
              f'font-size:12px;font-weight:700;border-radius:6px;'
              f'background:#111827;color:#fff;">'
              f'Avg {opp["avg_score"]}</span>'
            + '</div>'
        )

    # Domain tags
    domain_tags_html = ""
    opp_domains = opp.get("domains", [])
    if opp_domains:
        tags = []
        for did in opp_domains:
            d = domain_lookup.get(did)
            if d:
                tags.append(
                    f'<span style="display:inline-block;padding:2px 8px;'
                    f'margin-right:4px;font-size:11px;border-radius:4px;'
                    f'background:#f0f9ff;color:#0369a1;">'
                    f'{d.get("icon", "")} {escape(d["name"])}</span>'
                )
        if tags:
            domain_tags_html = (
                f'<div style="margin-bottom:12px;">{"".join(tags)}</div>'
            )

    # Detail rows
    details = ""
    details += _detail_row("News trigger", opp.get("news_trigger", ""))
    details += _detail_row("The problem", opp.get("the_problem", ""))
    details += _detail_row("Product", opp.get("product_description", ""))
    details += _detail_row("Market signal", opp.get("market_signal", ""))
    details += _detail_row("Competition", opp.get("competitive_landscape", ""))
    details += _detail_row("Risks", opp.get("risks_and_challenges", ""))
    details += _detail_row("Growth hook", opp.get("growth_hook", ""))

    rank = opp.get("rank", "?")

    return f"""\
    <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;padding:28px 24px;margin-bottom:20px;">

      <div style="margin-bottom:14px;">
        <span style="display:inline-block;width:28px;height:28px;line-height:28px;text-align:center;border-radius:50%;background:#111827;color:#fff;font-size:13px;font-weight:700;vertical-align:middle;">{rank}</span>
        <span style="margin-left:10px;font-size:20px;font-weight:700;color:#111827;vertical-align:middle;">{escape(opp.get("name", "Untitled"))}</span>
      </div>
      <p style="margin:0 0 16px;font-size:15px;color:#6b7280;font-style:italic;">{escape(opp.get("one_liner", ""))}</p>

      {domain_tags_html}

      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px;">
        <tr>
          <td style="padding:8px 12px;background:#f9fafb;border-radius:6px;vertical-align:top;width:33%;">
            <span style="font-size:11px;text-transform:uppercase;color:#9ca3af;letter-spacing:0.5px;">Audience</span><br/>
            <span style="font-size:13px;color:#374151;">{escape(opp.get("target_audience", "N/A"))}</span>
          </td>
          <td style="width:8px;"></td>
          <td style="padding:8px 12px;background:#f9fafb;border-radius:6px;vertical-align:top;width:33%;">
            <span style="font-size:11px;text-transform:uppercase;color:#9ca3af;letter-spacing:0.5px;">Revenue</span><br/>
            <span style="font-size:13px;color:#374151;">{escape(opp.get("revenue_model", "N/A"))}</span>
          </td>
          <td style="width:8px;"></td>
          <td style="padding:8px 12px;background:#f9fafb;border-radius:6px;vertical-align:top;width:33%;">
            <span style="font-size:11px;text-transform:uppercase;color:#9ca3af;letter-spacing:0.5px;">Build time</span><br/>
            <span style="font-size:13px;color:#374151;">{escape(opp.get("estimated_build_time", "N/A"))}</span>
          </td>
        </tr>
      </table>

      <div style="margin-bottom:16px;">
        <span style="display:inline-block;padding:3px 10px;font-size:12px;font-weight:600;border-radius:99px;background:{bg};color:{fg};text-transform:uppercase;letter-spacing:0.4px;">{escape(complexity)}</span>
        <span style="margin-left:10px;font-size:13px;color:#6b7280;">{escape(opp.get("tech_stack", ""))}</span>
      </div>

      {scores_html}

      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px;">
        {details}
      </table>

      <div style="background:#f9fafb;border-radius:8px;padding:14px 18px;">
        <p style="margin:0 0 8px;font-size:12px;text-transform:uppercase;color:#9ca3af;letter-spacing:0.5px;">Build Plan</p>
        <ol style="margin:0;padding-left:18px;font-size:14px;line-height:1.7;">
          {steps_html}
        </ol>
      </div>
    </div>
"""


def _build_html(
    opportunities: list[dict],
    domains: list[dict] | None = None,
    news_count: int | None = None,
) -> str:
    """Render opportunities as a styled HTML email body, grouped by domain."""
    today = date.today().strftime("%B %d, %Y")
    domain_lookup = _build_domain_lookup(domains)
    grouped = _group_by_domain(opportunities, domains)
    n_domains = len(grouped)
    total_ideas = len(opportunities)

    # -- Subtitle --
    if domains:
        subtitle = f"{total_ideas} ideas across {n_domains} domains"
    else:
        subtitle = f"{total_ideas} opportunities identified"

    # -- Table of Contents --
    toc_html = ""
    if domains and n_domains > 1:
        toc_items = ""
        for domain_cfg, domain_opps in grouped:
            if domain_cfg:
                icon = domain_cfg.get("icon", "")
                name = domain_cfg.get("name", "Unknown")
                anchor = domain_cfg["id"]
            else:
                icon = ""
                name = "General"
                anchor = "general"
            toc_items += (
                f'<a href="#{anchor}" style="display:inline-block;padding:6px 14px;'
                f'margin:4px;font-size:13px;color:#374151;background:#f3f4f6;'
                f'border-radius:6px;text-decoration:none;">'
                f'{icon} {escape(name)} ({len(domain_opps)})</a>'
            )
        toc_html = (
            f'<div style="text-align:center;margin-bottom:24px;">{toc_items}</div>'
        )

    # -- Cross-domain highlights --
    cross_html = ""
    if domains:
        cross = _get_cross_domain_ideas(opportunities)
        if cross:
            cross_items = ""
            for opp in cross:
                domain_names = [
                    f'{domain_lookup.get(d, {}).get("icon", "")} '
                    f'{escape(domain_lookup.get(d, {}).get("name", d))}'
                    for d in opp.get("domains", [])
                ]
                cross_items += (
                    f'<div style="padding:6px 0;border-bottom:1px solid #e5e7eb;">'
                    f'<span style="font-weight:600;color:#111827;">'
                    f'{escape(opp.get("name", "?"))}</span>'
                    f'<span style="margin-left:8px;font-size:12px;color:#6b7280;">'
                    f'{" + ".join(domain_names)}</span></div>'
                )
            cross_html = (
                f'<div style="background:#fffbeb;border:1px solid #fde68a;'
                f'border-radius:8px;padding:16px 20px;margin-bottom:24px;">'
                f'<p style="margin:0 0 10px;font-size:14px;font-weight:700;'
                f'color:#92400e;">Cross-Domain Ideas</p>'
                f'{cross_items}</div>'
            )

    # -- Domain sections --
    sections_html = ""
    for domain_cfg, domain_opps in grouped:
        if domain_cfg:
            icon = domain_cfg.get("icon", "")
            name = domain_cfg.get("name", "Unknown")
            anchor = domain_cfg["id"]
        else:
            icon = ""
            name = "General"
            anchor = "general"

        cards = ""
        for opp in domain_opps:
            cards += _build_card_html(opp, domain_lookup)

        sections_html += f"""\
    <div id="{anchor}" style="margin-bottom:32px;">
      <h2 style="margin:0 0 16px;font-size:20px;color:#111827;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">
        {icon} {escape(name)}
        <span style="font-size:14px;font-weight:400;color:#9ca3af;margin-left:8px;">{len(domain_opps)} ideas</span>
      </h2>
      {cards}
    </div>
"""

    # -- Footer --
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    footer_parts = [f"Generated {timestamp}"]
    if news_count is not None:
        footer_parts.append(f"{news_count} news items processed")
    footer_text = " &middot; ".join(footer_parts)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"/></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:640px;margin:0 auto;padding:32px 16px;">
    <div style="text-align:center;margin-bottom:28px;">
      <h1 style="margin:0;font-size:26px;color:#111827;">Opportunity Radar</h1>
      <p style="margin:6px 0 0;font-size:14px;color:#9ca3af;">{today} &middot; {subtitle}</p>
    </div>

{toc_html}
{cross_html}
{sections_html}

    <p style="text-align:center;font-size:12px;color:#9ca3af;margin-top:24px;">
      {footer_text}
    </p>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------

def send_digest(digest: str) -> None:
    """Send the plain-text opportunity digest via email."""
    msg = EmailMessage()
    msg["Subject"] = "Opportunity Radar \u2013 Daily Digest"
    msg["From"] = config.SMTP_USER
    msg["To"] = config.DIGEST_RECIPIENT
    msg.set_content(digest)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(msg)


def send_opportunities_digest(
    opportunities: list[dict],
    domains: list[dict] | None = None,
    news_count: int | None = None,
) -> None:
    """Send a formatted HTML email with a plain-text fallback.

    *opportunities* is the list of dicts returned by
    ``analyzer.analyze_opportunities()``.
    """
    today = date.today().strftime("%B %d, %Y")

    msg = EmailMessage()
    msg["Subject"] = f"Opportunity Radar \u2013 {today}"
    msg["From"] = config.SMTP_USER
    msg["To"] = config.DIGEST_RECIPIENT

    # Plain-text part (shown by clients that can't render HTML).
    msg.set_content(_build_plain_text(opportunities, domains))

    # HTML part (preferred by most clients).
    msg.add_alternative(
        _build_html(opportunities, domains, news_count), subtype="html",
    )

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(msg)
