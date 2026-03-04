"""
email_report.py
Builds a professional HTML digest email and sends it via SendGrid.

Layout:
  - Dark-navy header (title + date range + case count)
  - Cases grouped by province, each in a card with:
      title (linked to CanLII), court, date, case-type badge,
      AI summary, damages table, notes
  - Footer with AI disclaimer
"""

import logging
from datetime import datetime, timedelta

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from config import (
    SENDGRID_API_KEY,
    FROM_EMAIL,
    FROM_NAME,
    TO_EMAILS,
    EMAIL_SUBJECT,
)
from database import load_cases_since

logger = logging.getLogger(__name__)

# ── Province display config ───────────────────────────────────────────────────

PROVINCE_ORDER = ["NS", "NB", "PE", "NL", "ON"]
PROVINCE_NAMES = {
    "NS": "Nova Scotia",
    "NB": "New Brunswick",
    "PE": "Prince Edward Island",
    "NL": "Newfoundland & Labrador",
    "ON": "Ontario",
}

CASE_TYPE_COLOURS = {
    "MVA":          "#1a73e8",   # blue
    "Slip and Fall":"#e67e00",   # orange
    "Trip and Fall":"#e67e00",
    "Other PI":     "#6b7280",   # grey
    "LTD":          "#0b7c3e",   # green
    "Class Action": "#7b1fa2",   # purple
}


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _badge(case_type: str) -> str:
    colour = CASE_TYPE_COLOURS.get(case_type, "#6b7280")
    label  = case_type or "PI"
    return (
        f'<span style="display:inline-block;background:{colour};color:#fff;'
        f'font-size:11px;font-weight:700;padding:3px 10px;border-radius:12px;'
        f'letter-spacing:0.3px;">{label}</span>'
    )


def _dmg_row(label: str, value: str) -> str:
    if not value:
        return ""
    is_total = "Total" in label
    weight   = "700" if is_total else "400"
    top_border = "border-top:1px solid #ddd;" if is_total else ""
    return (
        f'<tr style="{top_border}">'
        f'<td style="padding:3px 12px 3px 0;color:#555;font-size:12px;">{label}</td>'
        f'<td style="padding:3px 0;font-size:12px;font-weight:{weight};'
        f'color:#1a1a1a;">{value}</td>'
        f'</tr>'
    )


def _damages_table(case: dict) -> str:
    rows = "".join(filter(None, [
        _dmg_row("Non-Pecuniary",         case.get("non_pecuniary")),
        _dmg_row("General Damages",       case.get("general_damages")),
        _dmg_row("Past Income Loss",      case.get("past_income_loss")),
        _dmg_row("Future Income Loss",    case.get("future_income_loss")),
        _dmg_row("Cost of Future Care",   case.get("cost_of_future_care")),
        _dmg_row("Special Damages",       case.get("special_damages")),
        _dmg_row("Aggravated / Punitive", case.get("aggravated_punitive")),
        _dmg_row("<strong>Total</strong>", case.get("total_damages")),
    ]))
    if not rows:
        return ""
    return (
        f'<table cellpadding="0" cellspacing="0" style="margin-top:10px;'
        f'border-collapse:collapse;width:100%;">{rows}</table>'
    )


def _case_card(case: dict) -> str:
    title   = case.get("title", "Unknown")
    url     = case.get("canlii_url", "#")
    court   = case.get("jurisdiction", "")
    date    = case.get("decision_date", "")
    ctype   = case.get("case_type", "")
    summary = case.get("summary", "")
    notes   = case.get("notes", "")

    summary_html = (
        f'<p style="margin:10px 0 0;font-size:13px;color:#333;line-height:1.55;">'
        f'{summary}</p>'
    ) if summary else ""

    notes_html = (
        f'<p style="margin:8px 0 0;font-size:11px;color:#888;font-style:italic;">'
        f'{notes}</p>'
    ) if notes else ""

    return f"""
    <div style="background:#ffffff;border:1px solid #e0e0e0;border-radius:8px;
                padding:16px 18px;margin-bottom:14px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td valign="top">
            <a href="{url}"
               style="font-size:14px;font-weight:700;color:#1a1a1a;text-decoration:none;">
              {title}
            </a>
            <div style="font-size:11px;color:#777;margin-top:3px;">
              {court}&nbsp;&bull;&nbsp;{date}
            </div>
          </td>
          <td align="right" valign="top" style="white-space:nowrap;padding-left:12px;">
            {_badge(ctype)}
          </td>
        </tr>
      </table>
      {summary_html}
      {_damages_table(case)}
      {notes_html}
    </div>"""


def _province_section(province: str, cases: list[dict]) -> str:
    name  = PROVINCE_NAMES.get(province, province)
    count = len(cases)
    label = f"{count} case{'s' if count != 1 else ''}"
    cards = "".join(_case_card(c) for c in cases)
    return f"""
    <h2 style="font-size:15px;font-weight:700;color:#1a1a1a;margin:28px 0 12px;
               padding-bottom:7px;border-bottom:2px solid #1a73e8;">
      {name}
      <span style="font-size:12px;font-weight:400;color:#666;"> — {label}</span>
    </h2>
    {cards}"""


# ── Full email ────────────────────────────────────────────────────────────────

def build_html(cases: list[dict], week_start: datetime, week_end: datetime) -> str:
    total      = len(cases)
    date_range = (
        f"{week_start.strftime('%B %d')} – {week_end.strftime('%B %d, %Y')}"
    )

    if total == 0:
        body_html = (
            '<p style="color:#555;font-size:14px;text-align:center;padding:30px 0;">'
            "No new personal injury, LTD, or class action decisions were identified this week."
            "</p>"
        )
    else:
        # Group by province in display order; sort each group newest-first
        by_prov: dict[str, list] = {}
        for c in cases:
            by_prov.setdefault(c.get("province", "Other"), []).append(c)

        for prov in by_prov:
            by_prov[prov].sort(
                key=lambda c: c.get("decision_date") or c.get("date_fetched", ""),
                reverse=True,
            )

        sections = []
        for prov in PROVINCE_ORDER:
            if prov in by_prov:
                sections.append(_province_section(prov, by_prov[prov]))
        # Any unexpected provinces at the end
        for prov, pcases in by_prov.items():
            if prov not in PROVINCE_ORDER:
                sections.append(_province_section(prov, pcases))

        body_html = "".join(sections)

    plural = "cases" if total != 1 else "case"
    prov_count = len(set(c.get("province", "") for c in cases)) if cases else 0
    prov_note  = f" across {prov_count} province{'s' if prov_count != 1 else ''}" if prov_count > 1 else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{EMAIL_SUBJECT}</title>
</head>
<body style="margin:0;padding:0;background:#f0f2f5;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">

  <div style="max-width:660px;margin:28px auto 40px;">

    <!-- Header -->
    <div style="background:#0f1b35;border-radius:10px 10px 0 0;padding:26px 30px;">
      <p style="margin:0 0 4px;font-size:11px;font-weight:600;color:#7a8fb5;
                letter-spacing:1px;text-transform:uppercase;">Weekly Digest</p>
      <h1 style="margin:0;font-size:22px;font-weight:700;color:#ffffff;">
        PI · LTD · Class Action Report
      </h1>
      <p style="margin:6px 0 0;font-size:13px;color:#9dafc8;">
        {date_range} &nbsp;·&nbsp;
        <strong style="color:#e8edf5;">{total} new {plural}{prov_note}</strong>
      </p>
    </div>

    <!-- Body -->
    <div style="background:#f0f2f5;padding:10px 30px 24px;">
      {body_html}
    </div>

    <!-- Footer -->
    <div style="background:#e4e7ec;border-radius:0 0 10px 10px;
                padding:14px 30px;text-align:center;">
      <p style="margin:0;font-size:11px;color:#8a94a6;line-height:1.5;">
        Generated by PI Law Tracker &nbsp;·&nbsp; Cases sourced from CanLII<br>
        <em>AI-generated summaries are for research purposes only and must be
        verified against the original decision before reliance.</em>
      </p>
    </div>

  </div>
</body>
</html>"""


# ── Send ──────────────────────────────────────────────────────────────────────

def send_alert_email(subject: str, body: str) -> bool:
    """
    Send a plain operational alert to the TO_EMAILS list.
    Used by daily_run.py to notify when cookie refresh is needed.
    Returns True on success.
    """
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
             background:#f0f2f5;margin:0;padding:0;">
  <div style="max-width:560px;margin:28px auto;background:#fff;border:1px solid #ddd;
              border-radius:8px;overflow:hidden;">
    <div style="background:#b91c1c;padding:18px 24px;">
      <h2 style="margin:0;color:#fff;font-size:17px;">PI Law Tracker — Alert</h2>
    </div>
    <div style="padding:20px 24px;font-size:14px;color:#333;line-height:1.6;">
      {body}
    </div>
    <div style="background:#f8f8f8;padding:10px 24px;font-size:11px;color:#888;">
      Sent automatically by PI Law Tracker
    </div>
  </div>
</body>
</html>"""

    message = Mail(
        from_email=(FROM_EMAIL, FROM_NAME),
        to_emails=TO_EMAILS,
        subject=subject,
        html_content=html,
    )
    try:
        sg       = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        logger.info("Alert email sent. Status: %s", response.status_code)
        return True
    except Exception as exc:
        logger.error("Failed to send alert email: %s", exc)
        return False


def send_weekly_report() -> bool:
    """
    Collect cases from the last 7 days, build the HTML digest, and send it.
    Returns True on success, False on failure.
    """
    week_end   = datetime.now()
    week_start = week_end - timedelta(days=7)

    cases = load_cases_since(week_start.strftime("%Y-%m-%d"))
    logger.info(
        "Building weekly digest: %d case(s) from %s to %s",
        len(cases),
        week_start.strftime("%Y-%m-%d"),
        week_end.strftime("%Y-%m-%d"),
    )

    html    = build_html(cases, week_start, week_end)
    subject = (
        f"{EMAIL_SUBJECT} — "
        f"{week_start.strftime('%b %d')}–{week_end.strftime('%b %d, %Y')} "
        f"({len(cases)} case{'s' if len(cases) != 1 else ''})"
    )

    message = Mail(
        from_email=(FROM_EMAIL, FROM_NAME),
        to_emails=TO_EMAILS,
        subject=subject,
        html_content=html,
    )

    try:
        sg       = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        logger.info("Email sent successfully. Status: %s", response.status_code)
        return True
    except Exception as exc:
        logger.error("SendGrid error: %s", exc)
        return False
