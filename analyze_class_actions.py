"""
analyze_class_actions.py
Take the raw class action search results, filter them, fetch full text,
run AI summaries, and send an email digest.

Reads:   data/class_actions.csv       (output of search_class_actions.py)
Writes:  data/class_action_summaries.csv  (one row per summarised case)

Usage:
    python analyze_class_actions.py                        # default: last 365 days, max 50
    python analyze_class_actions.py --days 180 --max 100   # last 6 months, 100 cases
    python analyze_class_actions.py --province ON          # Ontario only
    python analyze_class_actions.py --keyword "product"    # title contains "product"
    python analyze_class_actions.py --dry-run              # preview without fetching
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import anthropic

from config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_DELAY_SECONDS,
    OPENAI_API_KEY, OPENAI_MODEL,
    SENDGRID_API_KEY, FROM_EMAIL, FROM_NAME, TO_EMAILS,
    DATA_DIR, MAX_CASE_CHARS, LOG_FILE,
)

# Run from the script's own directory regardless of how it's launched
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("analyze_class_actions")


# ── Paths ─────────────────────────────────────────────────────────────────────
_INPUT_CSV      = os.path.join(DATA_DIR, "class_actions.csv")
_SUMMARIES_CSV  = os.path.join(DATA_DIR, "class_action_summaries.csv")
_SUMMARY_FIELDS = [
    "decision_date", "title", "citation", "province", "status",
    "summary", "amount", "class_size", "url",
]


# ── AI prompts ────────────────────────────────────────────────────────────────

_SYSTEM = """You are a legal research assistant for a Canadian plaintiff-side law firm.
Summarize class action court decisions in plain language.
Respond ONLY with valid JSON — no explanation, no markdown code fences."""

_USER_TEMPLATE = """Case title: {title}
Citation: {citation}
Decision date: {decision_date}

---BEGIN DECISION---
{text}
---END DECISION---

Provide a plain-language summary of this class action decision.

Return ONLY this JSON object:

{{
  "summary": "<2-3 sentence plain-language summary: who sued whom, what the claim was about, and what the court decided. Include any dollar amounts awarded or settlement values if mentioned.>",
  "status": "certification granted" | "certification denied" | "settlement approved" | "common issues trial" | "appeal" | "procedural" | "other",
  "class_size": "<approximate number of class members if mentioned, or null>",
  "amount": "<total settlement or damages amount if mentioned, e.g. '$15 million', or null>",
  "province": "<two-letter province code, e.g. 'ON', 'BC', 'QC', or null if unclear>"
}}
"""

# ── AI clients ────────────────────────────────────────────────────────────────

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, max_retries=0)
_openai_client = None
_last_call_time: float = 0.0


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        if not OPENAI_API_KEY:
            return None
        import openai as _openai_module
        _openai_client = _openai_module.OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


def _summarize_with_openai(prompt: str) -> dict | None:
    client = _get_openai_client()
    if client is None:
        logger.warning("    OpenAI fallback not configured")
        return None
    try:
        logger.info("    Trying OpenAI fallback (%s) …", OPENAI_MODEL)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=1024,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": prompt},
            ],
        )
        raw = response.choices[0].message.content.strip()
        result = json.loads(raw)
        logger.info("    OpenAI fallback succeeded")
        return result
    except Exception as exc:
        logger.error("    OpenAI fallback failed: %s", exc)
        return None


def _summarize_case(text: str, title: str, citation: str,
                    decision_date: str) -> dict | None:
    """Send case text to Claude for a class-action summary. Returns dict or None."""
    prompt = _USER_TEMPLATE.format(
        title=title, citation=citation,
        decision_date=decision_date, text=text,
    )

    global _last_call_time
    elapsed = time.time() - _last_call_time
    wait = max(0.0, CLAUDE_DELAY_SECONDS - elapsed)
    if wait > 0.5:
        logger.info("    Rate-limit pause: %.1fs", wait)
        time.sleep(wait)
    _last_call_time = time.time()

    try:
        message = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) >= 2 else raw
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()

        return json.loads(raw)

    except json.JSONDecodeError as exc:
        logger.error("JSON parse error for '%s': %s", title, exc)
        return None
    except Exception as exc:
        logger.warning("    Anthropic failed for '%s': %s — trying OpenAI …", title, exc)
        return _summarize_with_openai(prompt)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _province_from_url(url: str) -> str:
    """Extract two-letter province code from a CanLII URL."""
    try:
        parts = url.rstrip("/").split("/")
        if len(parts) >= 5:
            return parts[4].upper()
    except Exception:
        pass
    return ""


def _load_already_done() -> set:
    """Return set of URLs already in the summaries CSV."""
    done = set()
    if not os.path.exists(_SUMMARIES_CSV):
        return done
    with open(_SUMMARIES_CSV, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = row.get("url", "")
            if url:
                done.add(url)
    return done


def _save_summary(row: dict) -> None:
    """Append one summary row to the summaries CSV (create header if new)."""
    exists = os.path.exists(_SUMMARIES_CSV)
    with open(_SUMMARIES_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_SUMMARY_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in _SUMMARY_FIELDS})


def _estimate(count: int) -> tuple[str, str]:
    secs_per_case = 10 + CLAUDE_DELAY_SECONDS + 2
    total_secs = count * secs_per_case
    mins = total_secs // 60
    time_str = f"~{mins} min" if mins > 1 else f"~{total_secs}s"
    cost_str = f"~${count * 0.01:.2f}"
    return time_str, cost_str


# ── Email builder ─────────────────────────────────────────────────────────────

_STATUS_COLOURS = {
    "certification granted":  "#059669",
    "certification denied":   "#dc2626",
    "settlement approved":    "#2563eb",
    "common issues trial":    "#7c3aed",
    "appeal":                 "#ea580c",
    "procedural":             "#6b7280",
    "other":                  "#6b7280",
}


def _status_badge(status: str) -> str:
    colour = _STATUS_COLOURS.get(status, "#6b7280")
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:3px;'
        f'font-size:11px;font-weight:700;color:#fff;background:{colour};'
        f'text-transform:uppercase;letter-spacing:0.5px;">{status}</span>'
    )


def _case_card(s: dict) -> str:
    badge = _status_badge(s.get("status", "other"))
    prov = s.get("province", "") or ""
    date = s.get("decision_date", "") or ""
    meta = f"{prov} &bull; {date}" if prov and date else (prov or date)

    title = s.get("title", "Untitled")
    url = s.get("url", "")
    title_html = f'<a href="{url}" style="color:#1d4ed8;text-decoration:none;">{title}</a>' if url else title

    summary = s.get("summary", "No summary available.")

    extras = []
    if s.get("amount"):
        extras.append(f"<strong>Amount:</strong> {s['amount']}")
    if s.get("class_size"):
        extras.append(f"<strong>Class size:</strong> {s['class_size']}")
    extra_html = " &nbsp;|&nbsp; ".join(extras)

    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="margin-bottom:16px;border-bottom:1px solid #e5e7eb;padding-bottom:14px;">'
        f'<tr><td style="padding-bottom:4px;">{badge} '
        f'<span style="font-size:12px;color:#6b7280;margin-left:6px;">{meta}</span></td></tr>'
        f'<tr><td style="font-size:15px;font-weight:600;padding-bottom:4px;">{title_html}</td></tr>'
        f'<tr><td style="font-size:13px;color:#374151;line-height:1.5;padding-bottom:6px;">{summary}</td></tr>'
        + (f'<tr><td style="font-size:12px;color:#6b7280;">{extra_html}</td></tr>' if extra_html else "")
        + f'</table>'
    )


def _build_email_html(summaries: list[dict]) -> str:
    cards = "\n".join(_case_card(s) for s in summaries)
    today = datetime.now().strftime("%B %d, %Y")
    return f"""
    <table cellpadding="0" cellspacing="0" border="0" width="100%"
           style="max-width:600px;margin:0 auto;font-family:Arial,Helvetica,sans-serif;">
      <tr><td style="padding:24px 16px 12px;">
        <h1 style="font-size:20px;font-weight:700;color:#111827;margin:0;">
          Class Action Report</h1>
        <p style="font-size:13px;color:#6b7280;margin:4px 0 0;">
          {len(summaries)} cases &bull; {today}</p>
      </td></tr>
      <tr><td style="padding:0 16px;">
        <hr style="border:none;border-top:2px solid #e5e7eb;margin:0 0 16px;">
        {cards}
      </td></tr>
      <tr><td style="padding:16px;font-size:11px;color:#9ca3af;text-align:center;">
        Cases sourced from CanLII &bull; Summaries generated by AI — verify against original decisions
      </td></tr>
    </table>"""


def _send_email(html: str, count: int) -> bool:
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email=(FROM_EMAIL, FROM_NAME),
            to_emails=TO_EMAILS,
            subject=f"Class Action Report — {count} cases",
            html_content=html,
        )
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        resp = sg.send(message)
        logger.info("Email sent — status %s", resp.status_code)
        return True
    except Exception as exc:
        logger.error("Email send failed: %s", exc)
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="Fetch, summarize, and email class action cases from CanLII.",
    )
    p.add_argument("--days", type=int, default=365,
                   help="Only cases within N days (default: 365)")
    p.add_argument("--province", type=str, default="",
                   help="Filter by province code, e.g. ON, BC, QC")
    p.add_argument("--keyword", type=str, default="",
                   help="Filter by keyword in title")
    p.add_argument("--max", type=int, default=50,
                   help="Max cases to process (default: 50)")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be processed, don't fetch or analyze")
    p.add_argument("--no-email", action="store_true",
                   help="Save CSV only, skip sending email")
    p.add_argument("--input", type=str, default=_INPUT_CSV,
                   help="Path to input CSV (default: data/class_actions.csv)")
    return p.parse_args()


# ── Main ──────────────────────────────────────────────────────────────────────

def _date_in_range(date_str: str, cutoff_year: int) -> bool:
    """
    Check if a decision date is within range.
    Handles full dates ("2025-03-09"), year-only ("2025"), or empty.
    """
    d = date_str.strip()
    if not d:
        return False
    # Full date: compare as string (YYYY-MM-DD sorts correctly)
    if len(d) >= 10:
        cutoff_date = f"{cutoff_year}-01-01"
        return d >= cutoff_date
    # Year only: just compare the year
    try:
        return int(d[:4]) >= cutoff_year
    except (ValueError, IndexError):
        return False


def main():
    args = _parse_args()
    cutoff_year = (datetime.now() - timedelta(days=args.days)).year

    # ── Phase 0: Load and filter ──────────────────────────────────────────────
    if not os.path.exists(args.input):
        print(f"\n  ERROR: {args.input} not found.")
        print("  Run search_class_actions.py first to generate it.\n")
        return

    with open(args.input, "r", encoding="utf-8") as f:
        all_cases = list(csv.DictReader(f))

    print(f"\n  Loaded {len(all_cases)} cases from {args.input}")

    # Date filter — handles both "2025-03-09" and "2025" formats
    dated = [c for c in all_cases
             if _date_in_range(c.get("decision_date", ""), cutoff_year)]
    print(f"  After date filter ({args.days} days):  {len(dated)}")

    # Province filter
    if args.province:
        prov = args.province.upper()
        dated = [c for c in dated
                 if _province_from_url(c.get("url", "")) == prov]
        print(f"  After province filter ({prov}):       {len(dated)}")

    # Keyword filter
    if args.keyword:
        kw = args.keyword.lower()
        dated = [c for c in dated if kw in c.get("title", "").lower()]
        print(f"  After keyword filter (\"{args.keyword}\"): {len(dated)}")

    # Dedup against already-done
    already_done = _load_already_done()
    to_process = [c for c in dated if c.get("url", "") not in already_done]
    if len(to_process) < len(dated):
        print(f"  Already summarized:               {len(dated) - len(to_process)}")

    # Cap
    to_process = to_process[:args.max]
    time_est, cost_est = _estimate(len(to_process))

    print(f"\n  → {len(to_process)} cases to process (capped at --max {args.max})")
    print(f"    Estimated time: {time_est}  |  AI cost: {cost_est}")

    if not to_process:
        print("  Nothing to do.\n")
        return

    if args.dry_run:
        print(f"\n  {'─' * 60}")
        for i, c in enumerate(to_process, 1):
            print(f"  {i:3d}. {c.get('title', '?')[:75]}")
            print(f"       {c.get('citation', '')}  |  {c.get('decision_date', '')}")
        print(f"\n  (dry run — no fetching or analysis)\n")
        return

    # Confirm
    answer = input("\n  Proceed? [Y/n] ").strip().lower()
    if answer and answer != "y":
        print("  Cancelled.\n")
        return

    # ── Phase 1: Fetch case text ──────────────────────────────────────────────
    from case_fetcher import fetch_case_text, smart_truncate, warmup, close_browser

    print(f"\n  Phase 1: Fetching {len(to_process)} cases …\n")
    warmup()

    fetched: list[tuple[dict, str]] = []
    try:
        for i, case in enumerate(to_process, 1):
            title = case.get("title", "?")[:70]
            print(f"  [{i}/{len(to_process)}] Fetching: {title} …", end=" ", flush=True)

            raw = fetch_case_text(case.get("url", ""))
            if raw is None:
                print("FAILED")
                continue

            text = smart_truncate(raw, MAX_CASE_CHARS)
            fetched.append((case, text))
            print(f"OK ({len(raw):,} chars)")

    finally:
        close_browser()

    print(f"\n  Fetched {len(fetched)}/{len(to_process)} cases\n")

    if not fetched:
        print("  No cases fetched successfully. Check the log.\n")
        return

    # ── Phase 2: AI summarization ─────────────────────────────────────────────
    print(f"  Phase 2: Summarizing {len(fetched)} cases …\n")

    summaries: list[dict] = []
    for i, (case, text) in enumerate(fetched, 1):
        title = case.get("title", "?")[:70]
        print(f"  [{i}/{len(fetched)}] Analyzing: {title} …", end=" ", flush=True)

        result = _summarize_case(
            text=text,
            title=case.get("title", ""),
            citation=case.get("citation", ""),
            decision_date=case.get("decision_date", ""),
        )

        if result is None:
            print("FAILED")
            continue

        row = {
            "decision_date": case.get("decision_date", ""),
            "title":         case.get("title", ""),
            "citation":      case.get("citation", ""),
            "url":           case.get("url", ""),
            "province":      result.get("province", "") or _province_from_url(case.get("url", "")),
            "status":        result.get("status", "other"),
            "summary":       result.get("summary", ""),
            "amount":        result.get("amount", "") or "",
            "class_size":    result.get("class_size", "") or "",
        }

        _save_summary(row)
        summaries.append(row)

        status = result.get("status", "?")
        amt = result.get("amount", "")
        tag = f"{status}, {amt}" if amt else status
        print(f"done ({tag})")

    print(f"\n  Summarized {len(summaries)} cases → {_SUMMARIES_CSV}")

    if not summaries:
        print("  No summaries generated.\n")
        return

    # ── Phase 3: Email ────────────────────────────────────────────────────────
    if args.no_email:
        print("  (--no-email: skipping email)\n")
        return

    # Sort newest first
    summaries.sort(key=lambda s: s.get("decision_date", ""), reverse=True)

    html = _build_email_html(summaries)
    print(f"\n  Sending email with {len(summaries)} case summaries …")
    _send_email(html, len(summaries))
    print("  Done.\n")


if __name__ == "__main__":
    main()
