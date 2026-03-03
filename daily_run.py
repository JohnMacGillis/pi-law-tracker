"""
daily_run.py
Scheduled daily job — run every morning via Windows Task Scheduler.

Two-phase pipeline:
  Phase 1 — Parallel PDF fetch (FETCH_WORKERS concurrent connections)
             Title pre-filter runs before each download to skip obvious
             non-PI cases without touching the network at all.
  Phase 2 — Sequential pre-filter + Claude analysis on surviving cases
             Claude calls are rate-limited with an adaptive delay.

Cookie refresh:
  If CanLII returns 3+ consecutive 403s, refresh_cookies.py is launched
  automatically. Failed cases are un-marked so tomorrow's run retries them.
"""

import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Run from the script's own directory regardless of how Task Scheduler calls it
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import DATA_DIR, LOG_FILE, MAX_CASE_CHARS
from database import ensure_data_dir, load_seen_ids, mark_seen, save_case, unmark_seen
from rss_collector import fetch_new_cases
from case_fetcher import (
    fetch_case_text, smart_truncate,
    needs_cookie_refresh, rebuild_session, reset_403_counter,
)
from case_analyzer import analyze_case
from case_prefilter import prequalify, prequalify_title


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

# Parallel PDF workers — 2 balances speed vs. CanLII rate limits
FETCH_WORKERS = 2


# ── Phase 1 worker ────────────────────────────────────────────────────────────

def _fetch_one(case_meta: dict) -> tuple[dict, str | None, str | None]:
    """
    Title filter + PDF download for one case.
    Runs in a thread-pool worker.

    Returns
    -------
    (case_meta, raw_text, skip_reason)
      raw_text    — PDF text if successful, else None
      skip_reason — human-readable reason if skipped/failed, else None
    """
    title = case_meta["title"]

    # Stage 1 — title filter: instant reject, zero network cost
    title_ok, title_reason = prequalify_title(title)
    if title_ok is False:
        return case_meta, None, f"title: {title_reason}"

    # Stage 2 — RSS summary filter: keyword check on text already in the feed,
    # no PDF download needed.  Only run if the summary is substantial.
    rss_summary = case_meta.get("rss_summary", "")
    if len(rss_summary) > 150:
        is_candidate, reason = prequalify(rss_summary, title)
        if not is_candidate:
            return case_meta, None, f"rss: {reason}"

    # Stage 3 — PDF download: only cases that passed stages 1 & 2
    raw_text = fetch_case_text(case_meta["url"])
    if raw_text is None:
        return case_meta, None, "fetch failed (403 or empty)"

    return case_meta, raw_text, None


# ── Cookie refresh helper ─────────────────────────────────────────────────────

def _trigger_cookie_refresh() -> bool:
    logger.warning("=" * 65)
    logger.warning("COOKIE REFRESH — opening Chrome to CanLII")
    logger.warning("Solve the slider if prompted, then click OK.")
    logger.warning("=" * 65)
    try:
        result = subprocess.run(
            [sys.executable, "refresh_cookies.py"],
            timeout=300,
        )
        if result.returncode == 0:
            logger.info("Cookie refresh completed successfully.")
            return True
        logger.error("refresh_cookies.py exited with code %d", result.returncode)
        return False
    except subprocess.TimeoutExpired:
        logger.error("Cookie refresh timed out (5 min).")
        return False
    except Exception as exc:
        logger.error("Cookie refresh error: %s", exc)
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
                "<p>Missed cases have been un-marked and will retry tomorrow.</p>"
            ),
        )
    except Exception as exc:
        logger.warning("Could not send alert email: %s", exc)


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    start = datetime.now()
    logger.info("=" * 65)
    logger.info("Daily run started: %s", start.strftime("%Y-%m-%d %H:%M"))

    ensure_data_dir()
    seen_ids = load_seen_ids()
    logger.info("Known case IDs: %d", len(seen_ids))

    new_cases = fetch_new_cases(seen_ids)
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
    fetch_failed_ids: set[str] = set()

    # ── Phase 1: Parallel PDF fetch ───────────────────────────────────────────
    logger.info(
        "Phase 1: fetching up to %d PDFs with %d parallel workers …",
        len(new_cases), FETCH_WORKERS,
    )

    fetch_results: list[tuple[dict, str | None, str | None]] = []

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        future_map = {pool.submit(_fetch_one, c): c for c in new_cases}

        for future in as_completed(future_map):
            try:
                case_meta, raw_text, skip_reason = future.result()
                title = case_meta["title"]

                if skip_reason and skip_reason.startswith("title:"):
                    logger.info("─── %s\n    Title filter: skipped — %s",
                                title, skip_reason[7:])
                    skipped += 1

                elif skip_reason and skip_reason.startswith("rss:"):
                    logger.info("─── %s\n    RSS filter: skipped — %s",
                                title, skip_reason[4:])
                    skipped += 1

                elif skip_reason:
                    logger.warning("─── %s\n    %s", title, skip_reason)
                    fetch_failed_ids.add(case_meta["case_id"])
                    errors += 1

                else:
                    logger.info("─── %s\n    PDF: %d chars",
                                title, len(raw_text))
                    fetch_results.append((case_meta, raw_text))

            except Exception as exc:
                logger.error("Unexpected fetch error: %s", exc)
                errors += 1

    # Cookie refresh check after all fetches
    if needs_cookie_refresh():
        _send_cookie_alert()
        if _trigger_cookie_refresh():
            rebuild_session()
            reset_403_counter()
        unmark_seen(fetch_failed_ids)
        logger.info(
            "%d fetch-failed cases un-marked for tomorrow's retry.",
            len(fetch_failed_ids),
        )
        fetch_failed_ids.clear()

    # ── Phase 2: Sequential pre-filter + Claude analysis ─────────────────────
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


if __name__ == "__main__":
    run()
