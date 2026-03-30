"""
email_report.py
Builds a professional HTML digest email and sends it via SendGrid.

Design principles:
  - Table-based layout only (email clients strip divs)
  - All CSS inline (Gmail strips <style> blocks)
  - Responsive: 100% width on mobile, max 560px on desktop
  - Card layout: title + badge → damages → summary box → notes
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

PROVINCE_ORDER = ["NS", "NB", "PE", "NL", "ON", "BC", "AB", "SK", "MB", "QC", "NT", "NU", "YT", "CA"]
PROVINCE_NAMES = {
    "NS": "Nova Scotia",
    "NB": "New Brunswick",
    "PE": "Prince Edward Island",
    "NL": "Newfoundland & Labrador",
    "ON": "Ontario",
    "BC": "British Columbia",
    "AB": "Alberta",
    "SK": "Saskatchewan",
    "MB": "Manitoba",
    "QC": "Quebec",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "YT": "Yukon",
    "CA": "Federal",
}

CASE_TYPE_COLOURS = {
    "MVA Damages":        "#2563eb",
    "MVA Liability":      "#1d4ed8",
    "Occupiers Liability":"#ea580c",
    "Other PI":           "#6b7280",
    "LTD":                "#059669",
    "Class Action":       "#7c3aed",
    # Legacy types (for old CSV rows)
    "MVA":                "#2563eb",
    "Slip and Fall":      "#ea580c",
    "Trip and Fall":      "#ea580c",
}

_F = "Arial,Helvetica,sans-serif"


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _badge(case_type: str) -> str:
    colour = CASE_TYPE_COLOURS.get(case_type, "#6b7280")
    label  = case_type or "PI"
    return (
        f'<span style="display:inline-block;background-color:{colour};color:#ffffff;'
        f'font-size:11px;font-weight:700;padding:3px 10px;border-radius:12px;'
        f'letter-spacing:0.3px;font-family:{_F};">{label}</span>'
    )


def _dmg_row(label: str, value: str, is_total: bool = False) -> str:
    if not value:
        return ""
    border = 'border-top:2px solid #e5e7eb;' if is_total else 'border-top:1px solid #f3f4f6;'
    lbl_wt = '700' if is_total else '400'
    val_wt = '700' if is_total else '500'
    lbl_cl = '#111827' if is_total else '#6b7280'
    val_sz = '14px' if is_total else '13px'
    pad    = '10px' if is_total else '7px'
    return (
        f'<tr>'
        f'<td style="{border}padding:{pad} 0 7px 0;font-size:13px;'
        f'font-weight:{lbl_wt};color:{lbl_cl};font-family:{_F};">{label}</td>'
        f'<td align="right" style="{border}padding:{pad} 0 7px 0;'
        f'font-size:{val_sz};font-weight:{val_wt};color:#111827;'
        f'font-family:{_F};">{value}</td>'
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
        _dmg_row("Total",                 case.get("total_damages"), is_total=True),
    ]))
    if not rows:
        return ""
    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="margin-top:14px;table-layout:fixed;">'
        f'{rows}</table>'
    )


def _case_card(case: dict) -> str:
    title   = case.get("title", "Unknown")
    url     = case.get("canlii_url", "#")
    court   = case.get("jurisdiction", "")
    date    = case.get("decision_date", "")
    ctype   = case.get("case_type", "")
    summary = case.get("summary", "")
    notes   = case.get("notes", "")

    # Damages first
    damages_html = ""
    dmg_table = _damages_table(case)
    if dmg_table:
        damages_html = f'<tr><td colspan="2" style="padding:0;">{dmg_table}</td></tr>'

    # Summary — no label, just text
    summary_html = ""
    if summary:
        summary_html = (
            f'<tr><td colspan="2" style="padding:12px 0 0 0;'
            f'font-size:13px;color:#374151;line-height:1.65;'
            f'font-family:{_F};">{summary}</td></tr>'
        )

    # Notes callout
    notes_html = ""
    if notes:
        notes_html = (
            f'<tr><td colspan="2" style="padding:10px 0 0 0;">'
            f'<table cellpadding="0" cellspacing="0" border="0" width="100%"><tr>'
            f'<td style="border-left:3px solid #d1d5db;'
            f'padding:8px 12px;font-size:12px;color:#6b7280;line-height:1.5;'
            f'font-style:italic;font-family:{_F};">{notes}</td>'
            f'</tr></table></td></tr>'
        )

    return f"""
    <tr><td style="padding:0 0 12px 0;">
      <table cellpadding="0" cellspacing="0" border="0" width="100%"
             style="background-color:#ffffff;border:1px solid #e5e7eb;border-radius:8px;">
        <tr><td style="padding:16px;">
          <table cellpadding="0" cellspacing="0" border="0" width="100%"
                 style="table-layout:fixed;word-wrap:break-word;">
            <!-- Badge + Court + Date -->
            <tr><td style="padding:0 0 8px 0;font-size:0;">
              {_badge(ctype)}
              <span style="display:inline-block;font-size:11px;color:#9ca3af;
                           font-family:{_F};padding-left:8px;vertical-align:middle;">
                {court} &bull; {date}</span>
            </td></tr>
            <!-- Title -->
            <tr><td style="padding:0;">
              <a href="{url}"
                 style="font-size:14px;font-weight:700;color:#111827;text-decoration:none;
                        font-family:{_F};line-height:1.4;word-wrap:break-word;">{title}</a>
            </td></tr>
            {damages_html}
            {summary_html}
            {notes_html}
          </table>
        </td></tr>
      </table>
    </td></tr>"""


def _province_section(province: str, cases: list[dict]) -> str:
    name  = PROVINCE_NAMES.get(province, province)
    count = len(cases)
    label = f"{count} case{'s' if count != 1 else ''}"
    cards = "".join(_case_card(c) for c in cases)
    return f"""
    <tr><td style="padding:20px 0 10px 0;">
      <table cellpadding="0" cellspacing="0" border="0" width="100%">
        <tr><td style="font-size:14px;font-weight:700;color:#1e293b;
                       font-family:{_F};padding:0 0 8px 0;
                       border-bottom:1px solid #e2e8f0;">
          {name}<span style="font-size:12px;font-weight:400;color:#94a3b8;
                             margin-left:6px;">&mdash; {label}</span>
        </td></tr>
      </table>
    </td></tr>
    {cards}"""


# ── Full email ────────────────────────────────────────────────────────────────

def build_html(cases: list[dict], week_start: datetime, week_end: datetime,
               heading: str = "PI Damages Weekly",
               header_color: str = "#0f172a") -> str:
    total      = len(cases)
    date_range = (
        f"{week_start.strftime('%B %d')} &ndash; {week_end.strftime('%B %d, %Y')}"
    )

    if total == 0:
        body_html = (
            '<tr><td style="padding:40px 0;text-align:center;font-size:14px;'
            f'color:#9ca3af;font-family:{_F};">'
            'Nothing new this week.</td></tr>'
        )
    else:
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
        for prov, pcases in by_prov.items():
            if prov not in PROVINCE_ORDER:
                sections.append(_province_section(prov, pcases))

        body_html = "".join(sections)

    plural     = "cases" if total != 1 else "case"
    prov_count = len(set(c.get("province", "") for c in cases)) if cases else 0
    prov_note  = (f" across {prov_count} province{'s' if prov_count != 1 else ''}"
                  if prov_count > 1 else "")

    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>{EMAIL_SUBJECT}</title>
  <!--[if mso]>
  <style>table,td {{font-family:Arial,Helvetica,sans-serif !important;}}</style>
  <![endif]-->
</head>
<body style="margin:0;padding:0;background-color:#f3f4f6;
             font-family:{_F};-webkit-font-smoothing:antialiased;">

  <!-- Outer wrapper -->
  <table cellpadding="0" cellspacing="0" border="0" width="100%" bgcolor="#f3f4f6"
         style="background-color:#f3f4f6;">
    <tr><td align="center" style="padding:24px 12px 40px 12px;">

      <!-- Inner container — 480px max, shrinks on mobile -->
      <table cellpadding="0" cellspacing="0" border="0" width="480"
             style="width:480px;max-width:100%;border-radius:12px;overflow:hidden;
                    box-shadow:0 1px 3px rgba(0,0,0,0.06),0 2px 12px rgba(0,0,0,0.04);">
      <!--[if !mso]><!-->
      <!--<![endif]-->

        <!-- HEADER -->
        <tr><td bgcolor="{header_color}" style="background-color:{header_color};padding:24px 24px 20px 24px;">
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            <tr><td style="font-size:20px;font-weight:700;color:#ffffff;line-height:1.3;
                          padding:0 0 8px 0;font-family:{_F};">
              {heading}</td></tr>
            <tr><td style="font-size:13px;color:#94a3b8;line-height:1.4;
                          font-family:{_F};">
              {date_range}
              &nbsp;&bull;&nbsp;
              <strong style="color:#e2e8f0;">{total} new {plural}{prov_note}</strong>
            </td></tr>
          </table>
        </td></tr>

        <!-- BODY -->
        <tr><td bgcolor="#f3f4f6" style="background-color:#f3f4f6;padding:4px 20px 24px 20px;">
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            {body_html}
          </table>
        </td></tr>

        <!-- FOOTER -->
        <tr><td bgcolor="#f8fafc" style="background-color:#f8fafc;
                       border-top:1px solid #e5e7eb;padding:14px 24px;">
          <table cellpadding="0" cellspacing="0" border="0" width="100%">
            <tr><td align="center" style="font-size:11px;color:#b0b7c3;line-height:1.5;
                                          font-family:{_F};">
              Cases sourced from
              <a href="https://www.canlii.org" style="color:#9ca3af;text-decoration:none;">CanLII</a>
              &nbsp;&bull;&nbsp; Verify all figures against the original decision
            </td></tr>
          </table>
        </td></tr>

      </table>

    </td></tr>
  </table>

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
<body style="font-family:{_F};background:#f3f4f6;margin:0;padding:0;">
  <table cellpadding="0" cellspacing="0" border="0" width="100%" bgcolor="#f3f4f6">
    <tr><td align="center" style="padding:24px 12px;">
      <table cellpadding="0" cellspacing="0" border="0" width="420"
             style="width:420px;max-width:100%;border-radius:10px;overflow:hidden;
                    box-shadow:0 1px 3px rgba(0,0,0,0.06);">
        <tr><td bgcolor="#dc2626" style="background-color:#dc2626;padding:18px 24px;">
          <table cellpadding="0" cellspacing="0" border="0"><tr>
            <td style="font-size:17px;font-weight:700;color:#ffffff;
                       font-family:{_F};">PI Law Tracker &mdash; Alert</td>
          </tr></table>
        </td></tr>
        <tr><td bgcolor="#ffffff" style="background-color:#ffffff;
                       padding:20px 24px;font-size:14px;color:#374151;line-height:1.65;
                       font-family:{_F};">{body}</td></tr>
        <tr><td bgcolor="#f9fafb" style="background-color:#f9fafb;
                       border-top:1px solid #e5e7eb;padding:12px 24px;
                       font-size:11px;color:#9ca3af;font-family:{_F};">
          Sent automatically by PI Law Tracker
        </td></tr>
      </table>
    </td></tr>
  </table>
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


_CLASS_ACTION_TYPES = {"Class Action"}

# MVA: Atlantic + Ontario only.  LTD + Occupiers Liability + Other PI: national.
_MVA_TYPES          = {"MVA Damages", "MVA Liability", "MVA"}   # includes legacy "MVA"
_REGIONAL_PROVINCES = {"NS", "NB", "PE", "NL", "ON"}           # Atlantic Canada + Ontario
_NATIONAL_TYPES     = {"LTD", "Occupiers Liability", "Other PI",
                       "Slip and Fall", "Trip and Fall"}         # includes legacy types

# All PI types combined (for external import)
_PI_TYPES           = _MVA_TYPES | _NATIONAL_TYPES
_PI_PROVINCES       = _REGIONAL_PROVINCES  # kept for backward compat


def _send_digest(cases: list[dict], week_start: datetime, week_end: datetime,
                 heading: str, subject_prefix: str,
                 header_color: str = "#0f172a") -> bool:
    """Build and send a single digest email. Returns True on success."""
    if not cases:
        logger.info("No cases for '%s' — skipping email.", heading)
        return True

    html = build_html(cases, week_start, week_end, heading=heading,
                      header_color=header_color)
    date_str = (
        f"{week_start.strftime('%b %d')}–{week_end.strftime('%b %d, %Y')}"
    )
    subject = (
        f"{subject_prefix} — {date_str} "
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
        logger.info("'%s' email sent. Status: %s", heading, response.status_code)
        return True
    except Exception as exc:
        logger.error("Failed to send '%s' email: %s", heading, exc)
        return False


def send_weekly_report() -> bool:
    """
    Collect cases from the last 7 days and send TWO digest emails:
      1. MVA / Slip & Fall / LTD — personal injury cases
      2. Class Actions
    Returns True if both succeed.
    """
    week_end   = datetime.now()
    week_start = week_end - timedelta(days=7)

    all_cases = load_cases_since(week_start.strftime("%Y-%m-%d"))
    logger.info(
        "Building weekly digests: %d case(s) from %s to %s",
        len(all_cases),
        week_start.strftime("%Y-%m-%d"),
        week_end.strftime("%Y-%m-%d"),
    )

    ca_cases = [c for c in all_cases
                if c.get("case_type", "") in _CLASS_ACTION_TYPES]

    # PI filter: MVA types limited to Atlantic + ON; LTD/Occupiers/Other PI are national
    pi_cases = []
    for c in all_cases:
        ct = c.get("case_type", "")
        prov = c.get("province", "")
        if ct in _MVA_TYPES and prov in _REGIONAL_PROVINCES:
            pi_cases.append(c)
        elif ct in _NATIONAL_TYPES:
            pi_cases.append(c)

    ok1 = _send_digest(
        pi_cases, week_start, week_end,
        heading="PI Damages Weekly",
        subject_prefix="PI Law Tracker — PI Damages",
        header_color="#0f172a",
    )
    ok2 = _send_digest(
        ca_cases, week_start, week_end,
        heading="Class Actions Weekly",
        subject_prefix="PI Law Tracker — Class Actions",
        header_color="#312e81",
    )
    return ok1 and ok2
