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
    "low": ("#e6f4ea", "#1e7e34"),
    "medium": ("#fff8e1", "#c77c00"),
    "high": ("#fce8e6", "#c62828"),
}

# ---------------------------------------------------------------------------
# Helpers — detect layout mode
# ---------------------------------------------------------------------------

def _is_grouped_mode(domain_groups: list[dict] | None) -> bool:
    """Return True when domain_groups is the new DOMAIN_GROUPS list (has group_id)."""
    return bool(domain_groups and domain_groups[0].get("group_id"))


def _is_legacy_grouped(opportunities: list[dict]) -> bool:
    """Return True when any idea has group_id (was produced in grouped mode)."""
    return any(o.get("group_id") for o in opportunities)


# ---------------------------------------------------------------------------
# Domain helpers — legacy flat-domain layout
# ---------------------------------------------------------------------------

def _build_domain_lookup(domains: list[dict] | None) -> dict[str, dict]:
    """Build domain_id → domain config dict for quick lookup."""
    if not domains:
        return {}
    return {d["id"]: d for d in domains}


def _build_domain_icons_lookup() -> dict[str, str]:
    """Return DOMAIN_ICONS from config (or empty dict if not defined)."""
    return getattr(config, "DOMAIN_ICONS", {})


def _group_by_domain(
    opportunities: list[dict],
    domains: list[dict] | None,
) -> list[tuple[dict | None, list[dict]]]:
    """Group opportunities by primary_domain, preserving domain order."""
    if not domains:
        return [(None, opportunities)]

    domain_lookup = _build_domain_lookup(domains)
    grouped: dict[str | None, list[dict]] = defaultdict(list)
    for opp in opportunities:
        grouped[opp.get("primary_domain")].append(opp)

    result: list[tuple[dict | None, list[dict]]] = []
    for d in domains:
        if d["id"] in grouped:
            result.append((d, grouped[d["id"]]))
    for key in grouped:
        if key is None or key not in domain_lookup:
            result.append((None, grouped[key]))

    return result


# ---------------------------------------------------------------------------
# Group helpers — new grouped layout
# ---------------------------------------------------------------------------

def _build_group_lookup(domain_groups: list[dict]) -> dict[str, dict]:
    """Return group_id → group config dict."""
    return {g["group_id"]: g for g in domain_groups}


def _group_by_group(
    opportunities: list[dict],
    domain_groups: list[dict],
) -> list[tuple[dict, list[dict]]]:
    """Group opportunities by group_id, following the DOMAIN_GROUPS order."""
    group_lookup = _build_group_lookup(domain_groups)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for opp in opportunities:
        gid = opp.get("group_id", "")
        grouped[gid].append(opp)

    result: list[tuple[dict, list[dict]]] = []
    for group in domain_groups:
        gid = group["group_id"]
        if gid in grouped:
            result.append((group, grouped[gid]))
    # Catch any ideas without a recognised group_id
    unknown = [o for gid, opps in grouped.items()
               if gid not in group_lookup for o in opps]
    if unknown:
        result.append(({"group_id": "other", "group_name": "Other", "icon": ""},
                       unknown))
    return result


def _get_cross_domain_ideas(opportunities: list[dict]) -> list[dict]:
    """Return ideas tagged with 2+ domains."""
    return [o for o in opportunities if len(o.get("domains", [])) >= 2]


# ---------------------------------------------------------------------------
# Plain-text builder
# ---------------------------------------------------------------------------

def _build_plain_text(
    opportunities: list[dict],
    domain_groups: list[dict] | None = None,
) -> str:
    """Render opportunities as readable plain text.

    Uses group-based layout when domain_groups has group_id elements;
    falls back to domain-based layout for legacy data.
    """
    lines: list[str] = []
    today_str = date.today().strftime("%B %d, %Y")
    lines.append(f"OPPORTUNITY RADAR - {today_str}")
    lines.append("=" * 64)

    use_groups = _is_grouped_mode(domain_groups)

    if use_groups:
        grouped_sections = _group_by_group(opportunities, domain_groups)
        n_groups = len(grouped_sections)
        lines.append(f"{len(opportunities)} ideas across {n_groups} groups")
        lines.append("")

        # Cross-domain highlights
        cross = _get_cross_domain_ideas(opportunities)
        if cross:
            domain_icons = _build_domain_icons_lookup()
            lines.append("CROSS-DOMAIN IDEAS:")
            for opp in cross:
                domain_labels = [
                    f"{domain_icons.get(d, '')} {d}" for d in opp.get("domains", [])
                ]
                lines.append(f"  - {opp.get('name', '?')} ({', '.join(domain_labels)})")
            lines.append("")

        for group, group_opps in grouped_sections:
            icon = group.get("icon", "")
            name = group.get("group_name", "Unknown")
            model = group.get("model", "")
            model_badge = " [Sonnet]" if "sonnet" in model.lower() else ""
            lines.append(f"{icon} {name}{model_badge} ({len(group_opps)} ideas)")
            lines.append("-" * 64)

            # Domain legend for this group
            domains_in_group = group.get("domains", [])
            if domains_in_group:
                domain_icons = _build_domain_icons_lookup()
                legend = ", ".join(
                    f"{domain_icons.get(d['id'], '')} {d['name']}"
                    for d in domains_in_group
                )
                lines.append(f"  Covers: {legend}")
                lines.append("")

            for opp in group_opps:
                rank = opp.get("rank", "?")
                domain_icons = _build_domain_icons_lookup()
                domain_pills = " ".join(
                    domain_icons.get(d, d) for d in opp.get("domains", [])
                )
                lines.append("")
                lines.append(f"#{rank}  {domain_pills} {opp.get('name', 'Untitled')}")
                lines.append(f"     {opp.get('one_liner', '')}")
                lines.append("")
                lines.append(f"  The problem:  {opp.get('the_problem', 'N/A')}")
                lines.append(f"  Revenue:      {opp.get('revenue_model', 'N/A')}")
                lines.append(f"  Competition:  {opp.get('competitive_landscape', 'N/A')}")
                lines.append(f"  Complexity:   {opp.get('complexity', 'N/A')}")
                lines.append(f"  First 100:    {opp.get('growth_hook', 'N/A')}")
                # Extra fields if present (from expand_idea or legacy)
                if opp.get("build_plan"):
                    lines.append("")
                    lines.append("  Build plan:")
                    for step in opp["build_plan"]:
                        lines.append(f"    • {step}")
            lines.append("-" * 64)
            lines.append("")

    else:
        # Legacy domain layout
        flat_domains = domain_groups if domain_groups else None
        grouped_sections_legacy = _group_by_domain(opportunities, flat_domains)
        n_domains = len(grouped_sections_legacy)

        if flat_domains:
            lines.append(f"{len(opportunities)} ideas across {n_domains} domains")
            lines.append("")

            cross = _get_cross_domain_ideas(opportunities)
            if cross:
                domain_lookup = _build_domain_lookup(flat_domains)
                lines.append("CROSS-DOMAIN IDEAS:")
                for opp in cross:
                    domain_names = [
                        domain_lookup.get(d, {}).get("name", d)
                        for d in opp.get("domains", [])
                    ]
                    lines.append(
                        f"  - {opp.get('name', '?')} ({', '.join(domain_names)})"
                    )
                lines.append("")

        for domain_cfg, domain_opps in grouped_sections_legacy:
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
                        f"  Scores:        Feasibility {opp.get('feasibility', '?')}/10"
                        f"  | Demand {opp.get('demand_confidence', '?')}/10"
                        f"  | Uniqueness {opp.get('uniqueness', '?')}/10"
                        f"  (avg {opp['avg_score']})"
                    )
                lines.append("")
                lines.append("  Build plan:")
                for step_num, step in enumerate(opp.get("build_plan", []), 1):
                    lines.append(f"    {step_num}. {step}")
                lines.append("-" * 64)
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _detail_row(label: str, value: str) -> str:
    return (
        f'<tr>'
        f'<td style="padding:5px 10px 5px 0;font-size:11px;text-transform:uppercase;'
        f'color:#9ca3af;letter-spacing:0.4px;vertical-align:top;white-space:nowrap;">'
        f'{escape(label)}</td>'
        f'<td style="padding:5px 0;font-size:13px;color:#374151;">'
        f'{escape(value)}</td>'
        f'</tr>'
    )


def _build_slim_card_html(
    opp: dict,
    domain_icons: dict[str, str],
) -> str:
    """Render a compact slim-schema idea card (no build plan, no scores)."""
    complexity = opp.get("complexity", "medium").lower()
    bg, fg = _COMPLEXITY_STYLES.get(complexity, _COMPLEXITY_STYLES["medium"])

    complexity_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(complexity, "🟡")

    # Domain icon pills
    domain_pills = ""
    for did in opp.get("domains", []):
        icon = domain_icons.get(did, "")
        if icon:
            domain_pills += (
                f'<span style="display:inline-block;font-size:18px;margin-right:2px;">'
                f'{icon}</span>'
            )

    rank = opp.get("rank", "?")

    # Build plan (if present from expansion)
    build_plan_html = ""
    if opp.get("build_plan"):
        steps = "".join(
            f'<li style="margin-bottom:4px;color:#374151;">{escape(str(s))}</li>'
            for s in opp["build_plan"]
        )
        build_plan_html = (
            f'<div style="background:#f9fafb;border-radius:8px;padding:12px 16px;'
            f'margin-top:12px;">'
            f'<p style="margin:0 0 6px;font-size:11px;text-transform:uppercase;'
            f'color:#9ca3af;letter-spacing:0.5px;">Build Plan</p>'
            f'<ol style="margin:0;padding-left:18px;font-size:13px;line-height:1.6;">'
            f'{steps}</ol></div>'
        )

    return f"""\
    <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;
    padding:20px 22px;margin-bottom:16px;">
      <div style="margin-bottom:10px;">
        <span style="display:inline-block;width:24px;height:24px;line-height:24px;
        text-align:center;border-radius:50%;background:#111827;color:#fff;
        font-size:12px;font-weight:700;vertical-align:middle;">{rank}</span>
        <span style="margin-left:6px;vertical-align:middle;">{domain_pills}</span>
        <span style="margin-left:6px;font-size:18px;font-weight:700;color:#111827;
        vertical-align:middle;">{escape(opp.get("name", "Untitled"))}</span>
      </div>
      <p style="margin:0 0 14px;font-size:14px;color:#6b7280;font-style:italic;">
        {escape(opp.get("one_liner", ""))}</p>

      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:10px;">
        <tr>
          <td style="padding:2px 0;font-size:12px;color:#9ca3af;
          text-transform:uppercase;letter-spacing:0.3px;width:100px;">Problem</td>
          <td style="padding:2px 0;font-size:13px;color:#374151;">
            {escape(opp.get("the_problem", ""))}</td>
        </tr>
        <tr>
          <td style="padding:6px 0 2px;font-size:12px;color:#9ca3af;
          text-transform:uppercase;letter-spacing:0.3px;">Revenue</td>
          <td style="padding:6px 0 2px;font-size:13px;color:#374151;">
            {escape(opp.get("revenue_model", ""))}</td>
        </tr>
        <tr>
          <td style="padding:6px 0 2px;font-size:12px;color:#9ca3af;
          text-transform:uppercase;letter-spacing:0.3px;">Competition</td>
          <td style="padding:6px 0 2px;font-size:13px;color:#374151;">
            {escape(opp.get("competitive_landscape", ""))}</td>
        </tr>
      </table>

      <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">
        <span style="display:inline-block;padding:3px 10px;font-size:12px;
        font-weight:600;border-radius:99px;background:{bg};color:{fg};
        text-transform:uppercase;letter-spacing:0.4px;">
          {complexity_emoji} {escape(complexity)}</span>
      </div>

      <div style="font-size:12px;color:#6b7280;">
        <span style="font-size:11px;text-transform:uppercase;color:#9ca3af;
        letter-spacing:0.3px;">First 100 users: </span>
        {escape(opp.get("growth_hook", ""))}
      </div>
      {build_plan_html}
    </div>
"""


def _build_card_html(opp: dict, domain_lookup: dict[str, dict]) -> str:
    """Render a full-schema opportunity card (legacy mode)."""
    complexity = opp.get("complexity", "medium").lower()
    bg, fg = _COMPLEXITY_STYLES.get(complexity, _COMPLEXITY_STYLES["medium"])

    steps_html = "".join(
        f'<li style="margin-bottom:6px;color:#374151;">{escape(str(step))}</li>'
        for step in opp.get("build_plan", [])
    )

    scores_html = ""
    if opp.get("avg_score") is not None:
        def _pill(label: str, value: object) -> str:
            return (
                f'<span style="display:inline-block;padding:3px 8px;margin-right:6px;'
                f'font-size:12px;border-radius:6px;background:#eef2ff;color:#4338ca;">'
                f'{label} <b>{value}</b>/10</span>'
            )
        scores_html = (
            '<div style="margin-bottom:16px;">'
            + _pill("Feasibility", opp.get("feasibility", "?"))
            + _pill("Demand", opp.get("demand_confidence", "?"))
            + _pill("Uniqueness", opp.get("uniqueness", "?"))
            + f'<span style="display:inline-block;padding:3px 8px;font-size:12px;'
              f'font-weight:700;border-radius:6px;background:#111827;color:#fff;">'
              f'Avg {opp["avg_score"]}</span>'
            + '</div>'
        )

    domain_tags_html = ""
    for did in opp.get("domains", []):
        d = domain_lookup.get(did)
        if d:
            domain_tags_html += (
                f'<span style="display:inline-block;padding:2px 8px;margin-right:4px;'
                f'font-size:11px;border-radius:4px;background:#f0f9ff;color:#0369a1;">'
                f'{d.get("icon", "")} {escape(d["name"])}</span>'
            )
    if domain_tags_html:
        domain_tags_html = (
            f'<div style="margin-bottom:12px;">{domain_tags_html}</div>'
        )

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
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px;">{details}</table>
      <div style="background:#f9fafb;border-radius:8px;padding:14px 18px;">
        <p style="margin:0 0 8px;font-size:12px;text-transform:uppercase;color:#9ca3af;letter-spacing:0.5px;">Build Plan</p>
        <ol style="margin:0;padding-left:18px;font-size:14px;line-height:1.7;">{steps_html}</ol>
      </div>
    </div>
"""


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _build_html(
    opportunities: list[dict],
    domain_groups: list[dict] | None = None,
    news_count: int | None = None,
    usage_summary: dict | None = None,
) -> str:
    """Render opportunities as a styled HTML email body."""
    today = date.today().strftime("%B %d, %Y")
    use_groups = _is_grouped_mode(domain_groups)

    if use_groups:
        grouped_sections = _group_by_group(opportunities, domain_groups)
        n_sections = len(grouped_sections)
        subtitle = (
            f"{len(opportunities)} ideas across {n_sections} categories"
        )
    else:
        flat_domains = domain_groups  # legacy flat list
        domain_lookup = _build_domain_lookup(flat_domains)
        grouped_sections_legacy = _group_by_domain(opportunities, flat_domains)
        n_sections = len(grouped_sections_legacy)
        subtitle = (
            f"{len(opportunities)} ideas across {n_sections} domains"
            if flat_domains
            else f"{len(opportunities)} opportunities identified"
        )

    # -- Table of Contents --
    toc_html = ""
    if domain_groups and n_sections > 1:
        toc_items = ""
        if use_groups:
            for group, opps in grouped_sections:
                gid = group["group_id"]
                icon = group.get("icon", "")
                name = group.get("group_name", "Unknown")
                model = group.get("model", "")
                badge = " ✨" if "sonnet" in model.lower() else ""
                toc_items += (
                    f'<a href="#{gid}" style="display:inline-block;padding:6px 14px;'
                    f'margin:4px;font-size:13px;color:#374151;background:#f3f4f6;'
                    f'border-radius:6px;text-decoration:none;">'
                    f'{icon} {escape(name)}{badge} ({len(opps)})</a>'
                )
        else:
            for domain_cfg, domain_opps in grouped_sections_legacy:
                if domain_cfg:
                    icon = domain_cfg.get("icon", "")
                    name = domain_cfg.get("name", "Unknown")
                    anchor = domain_cfg["id"]
                else:
                    icon, name, anchor = "", "General", "general"
                toc_items += (
                    f'<a href="#{anchor}" style="display:inline-block;padding:6px 14px;'
                    f'margin:4px;font-size:13px;color:#374151;background:#f3f4f6;'
                    f'border-radius:6px;text-decoration:none;">'
                    f'{icon} {escape(name)} ({len(domain_opps)})</a>'
                )
        toc_html = (
            f'<div style="text-align:center;margin-bottom:24px;">{toc_items}</div>'
        )

    # -- Cross-domain callout --
    cross_html = ""
    if domain_groups:
        cross = _get_cross_domain_ideas(opportunities)
        if cross:
            domain_icons = _build_domain_icons_lookup()
            cross_items = ""
            for opp in cross:
                domain_labels = " + ".join(
                    f'{domain_icons.get(d, "")} {escape(d)}'
                    for d in opp.get("domains", [])
                )
                cross_items += (
                    f'<div style="padding:6px 0;border-bottom:1px solid #e5e7eb;">'
                    f'<span style="font-size:14px;margin-right:6px;">🔀</span>'
                    f'<span style="font-weight:600;color:#111827;">'
                    f'{escape(opp.get("name", "?"))}</span>'
                    f'<span style="margin-left:8px;font-size:12px;color:#6b7280;">'
                    f'spans {domain_labels}</span></div>'
                )
            cross_html = (
                f'<div style="background:#fffbeb;border:1px solid #fde68a;'
                f'border-radius:8px;padding:16px 20px;margin-bottom:24px;">'
                f'<p style="margin:0 0 10px;font-size:14px;font-weight:700;'
                f'color:#92400e;">Cross-Domain Ideas</p>'
                f'{cross_items}</div>'
            )

    # -- Sections --
    sections_html = ""
    if use_groups:
        domain_icons = _build_domain_icons_lookup()
        for group, group_opps in grouped_sections:
            gid = group["group_id"]
            icon = group.get("icon", "")
            name = group.get("group_name", "Unknown")
            model = group.get("model", "")
            badge_html = (
                '<span style="font-size:12px;margin-left:8px;">✨</span>'
                if "sonnet" in model.lower()
                else ""
            )

            # Domain legend for this group
            domains_in_group = group.get("domains", [])
            legend_html = ""
            if domains_in_group:
                pills = "".join(
                    f'<span style="display:inline-block;padding:2px 8px;margin-right:4px;'
                    f'font-size:11px;border-radius:4px;background:#f0f9ff;color:#0369a1;">'
                    f'{domain_icons.get(d["id"], "")} {escape(d["name"])}</span>'
                    for d in domains_in_group
                )
                legend_html = (
                    f'<div style="margin-bottom:16px;">{pills}</div>'
                )

            cards = "".join(
                _build_slim_card_html(opp, domain_icons) for opp in group_opps
            )

            sections_html += f"""\
    <div id="{gid}" style="margin-bottom:32px;">
      <h2 style="margin:0 0 4px;font-size:20px;color:#111827;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">
        {icon} {escape(name)}
        {badge_html}
        <span style="font-size:14px;font-weight:400;color:#9ca3af;margin-left:8px;">{len(group_opps)} ideas</span>
      </h2>
      {legend_html}
      {cards}
    </div>
"""
    else:
        for domain_cfg, domain_opps in grouped_sections_legacy:
            if domain_cfg:
                icon = domain_cfg.get("icon", "")
                name = domain_cfg.get("name", "Unknown")
                anchor = domain_cfg["id"]
            else:
                icon, name, anchor = "", "General", "general"

            cards = "".join(
                _build_card_html(opp, domain_lookup) for opp in domain_opps
            )
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
    if usage_summary:
        in_tok = usage_summary.get("input_tokens", 0)
        out_tok = usage_summary.get("output_tokens", 0)
        cache_read = usage_summary.get("cache_read_tokens", 0)
        cost = usage_summary.get("estimated_cost_usd")
        tok_str = f"{in_tok:,} in / {out_tok:,} out tokens"
        if cache_read:
            tok_str += f" ({cache_read:,} cached)"
        if cost is not None:
            tok_str += f" · ${cost:.3f}"
        footer_parts.append(tok_str)

    teaser = (
        '<br/><span style="font-style:italic;color:#9ca3af;">'
        'Reply with an idea name to get the full build plan</span>'
    )
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
      {teaser}
    </p>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------

def send_digest(digest: str) -> None:
    """Send the plain-text opportunity digest via email (legacy)."""
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
    domain_groups: list[dict] | None = None,
    news_count: int | None = None,
    usage_summary: dict | None = None,
) -> None:
    """Send a formatted HTML email with a plain-text fallback.

    *domain_groups* accepts either the new DOMAIN_GROUPS list (each element
    has ``group_id``) for grouped layout, or the legacy flat DOMAINS list
    (each element has ``id``) for the old per-domain layout.
    """
    today = date.today().strftime("%B %d, %Y")

    msg = EmailMessage()
    msg["Subject"] = f"Opportunity Radar \u2013 {today}"
    msg["From"] = config.SMTP_USER
    msg["To"] = config.DIGEST_RECIPIENT

    msg.set_content(_build_plain_text(opportunities, domain_groups))
    msg.add_alternative(
        _build_html(opportunities, domain_groups, news_count, usage_summary),
        subtype="html",
    )

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(msg)
