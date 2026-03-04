"""
daily_run.py
Scheduled daily job — run every morning via Windows Task Scheduler.

Three-phase pipeline:
  Phase 0 — Instant pre-filter: title + RSS-summary keyword checks on ALL cases.
             No network I/O — eliminates the bulk of non-PI cases for free.
  Phase 1 — Parallel PDF fetch (FETCH_WORKERS concurrent connections)
             Only the cases that survived Phase 0.
             If 403s trigger a cookie refresh, failed cases are retried
             automatically within the same run (30-second cooldown).
  Phase 2 — Full-text pre-filter + Claude analysis on surviving cases.
             Claude calls are rate-limited with an adaptive delay.

Discovery:
  Uses the CanLII API if CANLII_API_KEY is set in config.py (recommended —
  no DataDome, no cookies required at the discovery stage).
  Falls back to RSS feeds if no API key is present.

Cookie refresh:
  If CanLII returns 3+ consecutive 403s, refresh_cookies.py is launched
  automatically. After the refresh, Phase 1 failures are retried once.
  Cases that still fail are un-marked so tomorrow's run retries them.
"""

import logging
import os
import sys
import time
from datetime import datetime

# Run from the script's own directory regardless of how Task Scheduler calls it
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import DATA_DIR, LOG_FILE, MAX_CASE_CHARS
from database import ensure_data_dir, load_seen_ids, mark_seen, save_case, unmark_seen
from case_fetcher import (
    fetch_case_text, smart_truncate, close_browser,
    needs_cookie_refresh, rebuild_session, reset_403_counter,
)
from case_analyzer import analyze_case
from case_prefilter import prequalify, prequalify_title

import api_collector
import rss_collector


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
logger = logging.getLogger("daily_run")

RETRY_COOLDOWN_SEC = 30    # Pause between session reset and retry pass


# ── Cookie refresh helpers ────────────────────────────────────────────────────

def _trigger_cookie_refresh() -> bool:
    """
    Reset the Playwright browser session.  The next fetch will open a fresh
    Chromium window — if DataDome shows a CAPTCHA, the user can solve it
    in the minimized browser window that appears in the taskbar.
    """
    logger.warning("=" * 65)
    logger.warning("SESSION RESET — rebuilding Playwright browser session")
    logger.warning("If a CAPTCHA appears in the browser, solve it to continue.")
    logger.warning("=" * 65)
    try:
        rebuild_session()
        reset_403_counter()
        logger.info("Browser session reset successfully.")
        return True
    except Exception as exc:
        logger.error("Session reset error: %s", exc)
        return False


def _send_cookie_alert() -> None:
    try:
        from email_report import send_alert_email
        send_alert_email(
            subject="⚠️  PI Law Tracker — Cookie Refresh Required",
            body=(
                "<p>The tracker encountered multiple 403 errors from CanLII "
                "(DataDome cookie expired).</p>"
                "<p><strong>Action:</strong> Double-click "
                "<em>REFRESH COOKIES.bat</em> on the Desktop, solve the slider "
                "if it appears, then click OK.</p>"
                "<p>Missed cases are being retried automatically. If they still "
                "fail, they will be un-marked and retried tomorrow.</p>"
            ),
        )
    except Exception as exc:
        logger.warning("Could not send alert email: %s", exc)


# ── PDF fetch helper (called twice when retry is needed) ──────────────────────

def _run_fetch_phase(
    cases: list[dict],
) -> tuple[list[tuple[dict, str]], set[str], int]:
    """
    Fetch case text sequentially.
    Playwright's sync API is not thread-safe, so we iterate rather than
    using a thread pool.  Rate limiting is handled inside fetch_case_text.

    Returns (fetch_results, failed_ids, error_count).
    """
    results:    list[tuple[dict, str]] = []
    failed_ids: set[str]               = set()
    errors      = 0

    for case_meta in cases:
        title = case_meta["title"]
        try:
            raw_text = fetch_case_text(case_meta["url"])
            if raw_text is None:
                logger.warning("─── %s\n    fetch failed (403 or empty)", title)
                failed_ids.add(case_meta["case_id"])
                errors += 1
            else:
                logger.info("─── %s\n    fetched: %d chars", title, len(raw_text))
                results.append((case_meta, raw_text))
        except Exception as exc:
            logger.error("Unexpected fetch error for '%s': %s", title, exc)
            errors += 1

    return results, failed_ids, errors


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    start = datetime.now()
    logger.info("=" * 65)
    logger.info("Daily run started: %s", start.strftime("%Y-%m-%d %H:%M"))

    ensure_data_dir()
    seen_ids = load_seen_ids()
    logger.info("Known case IDs: %d", len(seen_ids))

    # ── Discovery: API (preferred) or RSS fallback ────────────────────────────
    if api_collector.api_available():
        logger.info("Discovery: CanLII API")
        new_cases = api_collector.fetch_new_cases(seen_ids)
    else:
        logger.info("Discovery: RSS feeds (set CANLII_API_KEY in config.py for API)")
        new_cases = rss_collector.fetch_new_cases(seen_ids)

    logger.info("New cases to evaluate: %d", len(new_cases))

    if not new_cases:
        logger.info("Nothing new today. Run complete.")
        logger.info("=" * 65)
        return

    # Mark all as seen immediately for crash safety.
    # Cases that 403 are un-marked at the end so tomorrow retries them.
    for case_meta in new_cases:
        mark_seen(case_meta["case_id"])

    saved   = 0
    skipped = 0
    errors  = 0

    # ── Phase 0: Instant pre-filter (title + RSS summary) ─────────────────────
    # No network I/O — runs on all cases before any PDF is downloaded.
    logger.info("Phase 0: pre-filtering %d cases by title + RSS summary …", len(new_cases))

    candidates: list[dict] = []

    for case_meta in new_cases:
        title = case_meta["title"]

        # Title check — fastest possible reject (criminal, family, etc.)
        title_ok, title_reason = prequalify_title(title)
        if title_ok is False:
            logger.info("─── %s\n    Title: skipped — %s", title, title_reason)
            skipped += 1
            continue

        # RSS summary check — keyword scan on text already in the feed.
        # API discovery doesn't include summaries so this only runs for RSS.
        rss_summary = case_meta.get("rss_summary", "")
        if len(rss_summary) > 150:
            is_candidate, reason = prequalify(rss_summary, title)
            if not is_candidate:
                logger.info("─── %s\n    RSS: skipped — %s", title, reason)
                skipped += 1
                continue

        candidates.append(case_meta)

    logger.info(
        "Phase 0 complete: %d/%d proceed to PDF download (%d skipped instantly)",
        len(candidates), len(new_cases), skipped,
    )

    if not candidates:
        logger.info("No candidates after pre-filter. Run complete.")
        logger.info("=" * 65)
        return

    # ── Phase 1: Parallel PDF fetch (candidates only) ─────────────────────────
    logger.info("Phase 1: fetching %d cases sequentially …", len(candidates))

    fetch_results, fetch_failed_ids, phase1_errors = _run_fetch_phase(candidates)
    errors += phase1_errors

    # ── Cookie refresh + same-run retry ───────────────────────────────────────
    cookie_refreshed = False

    if needs_cookie_refresh():
        _send_cookie_alert()
        cookie_refreshed = _trigger_cookie_refresh()
        if cookie_refreshed:
            rebuild_session()
            reset_403_counter()

    if cookie_refreshed and fetch_failed_ids:
        retry_cases = [c for c in candidates if c["case_id"] in fetch_failed_ids]
        logger.info(
            "Retrying %d cases after cookie refresh (%ds cooldown) …",
            len(retry_cases), RETRY_COOLDOWN_SEC,
        )
        time.sleep(RETRY_COOLDOWN_SEC)

        # Subtract the error count for cases we're retrying
        errors -= len(retry_cases)

        retry_results, still_failed, retry_errors = _run_fetch_phase(retry_cases)
        fetch_results.extend(retry_results)
        fetch_failed_ids = still_failed
        errors += retry_errors

    # Un-mark cases that STILL failed so tomorrow's run picks them up
    if fetch_failed_ids:
        unmark_seen(fetch_failed_ids)
        logger.info(
            "%d case(s) still failing — un-marked for tomorrow's retry.",
            len(fetch_failed_ids),
        )

    # ── Phase 2: Full-text pre-filter + Claude analysis ───────────────────────
    logger.info(
        "Phase 2: analysing %d fetched cases …", len(fetch_results)
    )

    for case_meta, raw_text in fetch_results:
        title = case_meta["title"]

        # Full-text keyword filter — free, no API call
        is_candidate, reason = prequalify(raw_text, title)
        if not is_candidate:
            logger.info("    [%s] Pre-filter: skipped — %s", title, reason)
            skipped += 1
            continue

        logger.info("    [%s] Pre-filter: passed — %s", title, reason)

        text     = smart_truncate(raw_text, MAX_CASE_CHARS)
        analysis = analyze_case(
            text=text,
            title=title,
            court=case_meta.get("court_name", ""),
            province=case_meta.get("province", ""),
        )

        if analysis is None:
            logger.warning("    [%s] Analysis failed — skipping", title)
            errors += 1
            continue

        if not analysis.get("is_relevant"):
            logger.info("    [%s] Not PI — skipping", title)
            skipped += 1
            continue

        save_case(case_meta, analysis)
        saved += 1
        logger.info(
            "    [%s] SAVED — %s | total: %s",
            title,
            analysis.get("case_type", "?"),
            (analysis.get("damages") or {}).get("total") or "N/A",
        )

    elapsed = (datetime.now() - start).seconds
    logger.info(
        "Run complete in %ds — saved: %d | skipped: %d | errors: %d",
        elapsed, saved, skipped, errors,
    )
    logger.info("=" * 65)

    # Close the browser and persist the session for tomorrow's run
    close_browser()


if __name__ == "__main__":
    run()
