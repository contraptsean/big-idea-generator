"""Build and send opportunity digest emails over SMTP/TLS."""

from __future__ import annotations

import smtplib
from datetime import date
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
# Plain-text builder
# ---------------------------------------------------------------------------

def _build_plain_text(opportunities: list[dict]) -> str:
    """Render opportunities as readable plain text."""
    lines: list[str] = []
    lines.append(f"OPPORTUNITY RADAR - {date.today().strftime('%B %d, %Y')}")
    lines.append("=" * 64)

    for opp in opportunities:
        rank = opp.get("rank", "?")
        lines.append("")
        lines.append(f"#{rank}  {opp.get('name', 'Untitled')}")
        lines.append(f"     {opp.get('one_liner', '')}")
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


def _build_html(opportunities: list[dict]) -> str:
    """Render opportunities as a styled HTML email body."""
    today = date.today().strftime("%B %d, %Y")

    cards_html = ""
    for opp in opportunities:
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

        cards_html += f"""\
    <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;padding:28px 24px;margin-bottom:20px;">

      <div style="margin-bottom:14px;">
        <span style="display:inline-block;width:28px;height:28px;line-height:28px;text-align:center;border-radius:50%;background:#111827;color:#fff;font-size:13px;font-weight:700;vertical-align:middle;">{rank}</span>
        <span style="margin-left:10px;font-size:20px;font-weight:700;color:#111827;vertical-align:middle;">{escape(opp.get("name", "Untitled"))}</span>
      </div>
      <p style="margin:0 0 16px;font-size:15px;color:#6b7280;font-style:italic;">{escape(opp.get("one_liner", ""))}</p>

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

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"/></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:640px;margin:0 auto;padding:32px 16px;">
    <div style="text-align:center;margin-bottom:28px;">
      <h1 style="margin:0;font-size:26px;color:#111827;">Opportunity Radar</h1>
      <p style="margin:6px 0 0;font-size:14px;color:#9ca3af;">{today} &middot; {len(opportunities)} opportunities identified</p>
    </div>

{cards_html}

    <p style="text-align:center;font-size:12px;color:#9ca3af;margin-top:24px;">
      Generated by Opportunity Radar
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


def send_opportunities_digest(opportunities: list[dict]) -> None:
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
    msg.set_content(_build_plain_text(opportunities))

    # HTML part (preferred by most clients).
    msg.add_alternative(_build_html(opportunities), subtype="html")

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(msg)
