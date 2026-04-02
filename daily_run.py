"""
daily_run.py
Scheduled daily job — run every morning via Windows Task Scheduler.

Three-phase pipeline:
  Phase 0 — Instant pre-filter: title + RSS-summary keyword checks on ALL cases.
             No network I/O — eliminates the bulk of non-PI cases for free.
  Phase 1 — Sequential case text fetch via Playwright browser.
             Only the cases that survived Phase 0.
             If 403s trigger a session reset, failed cases are retried
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

import argparse
import json
import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime

# Run from the script's own directory regardless of how Task Scheduler calls it
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import DATA_DIR, LOG_FILE, MAX_CASE_CHARS

# Checkpoint file — surviving candidates are saved here so a crashed run
# can resume Phase 1/2 without re-discovering and re-filtering.
_PENDING_FILE = os.path.join(DATA_DIR, "pending_cases.json")
from database import ensure_data_dir, load_seen_ids, mark_seen, save_case, unmark_seen
from case_fetcher import (
    fetch_case_text, smart_truncate, close_browser, warmup,
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


# ── Crash-recovery checkpoint ────────────────────────────────────────────────

def _save_pending(candidates: list[dict]) -> None:
    """Save the Phase-0 survivors to disk so a crash doesn't lose them."""
    try:
        with open(_PENDING_FILE, "w", encoding="utf-8") as fh:
            json.dump(candidates, fh)
        logger.debug("Checkpoint: saved %d pending cases", len(candidates))
    except Exception as exc:
        logger.warning("Could not save pending checkpoint: %s", exc)


def _load_pending() -> list[dict]:
    """Load leftover candidates from a previous crashed run, if any."""
    if not os.path.exists(_PENDING_FILE):
        return []
    try:
        with open(_PENDING_FILE, "r", encoding="utf-8") as fh:
            cases = json.load(fh)
        logger.info("Resuming %d pending cases from previous crashed run", len(cases))
        return cases
    except Exception as exc:
        logger.warning("Could not load pending checkpoint: %s", exc)
        return []


def _clear_pending() -> None:
    """Remove the checkpoint file after a successful run."""
    try:
        if os.path.exists(_PENDING_FILE):
            os.remove(_PENDING_FILE)
    except OSError:
        pass


# ── Fetch + analyse helpers ────────────────────────────────────────────────────

_SENTINEL = None  # Marks end of fetch queue

def _run_fetch_phase(
    cases: list[dict],
) -> tuple[list[tuple[dict, str]], set[str], int]:
    """
    Fetch case text sequentially.
    Returns (fetch_results, failed_ids, error_count).
    """
    results:    list[tuple[dict, str]] = []
    failed_ids: set[str]               = set()
    errors      = 0

    for case_meta in cases:
        title = case_meta["title"]
        try:
            raw_text = fetch_case_text(
                case_meta["url"],
                db_id=case_meta.get("db_id", ""),
                citation=case_meta.get("citation", ""),
                title=title,
            )
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


def _analyse_worker(q: queue.Queue, results: dict,
                    results_lock: threading.Lock) -> None:
    """
    Background thread: pull (case_meta, raw_text) from the queue,
    run keyword pre-filter + Claude analysis, and save hits to CSV.
    Playwright stays on the main thread; only API calls happen here.
    """
    while True:
        item = q.get()
        if item is _SENTINEL:
            break
        case_meta, raw_text = item
        title = case_meta["title"]

        # Full-text keyword filter — free, no API call
        is_candidate, reason = prequalify(raw_text, title)
        if not is_candidate:
            logger.info("    [%s] Pre-filter: skipped — %s", title, reason)
            with results_lock:
                results["skipped"] += 1
                results["kw_rejected"] += 1
            continue

        logger.info("    [%s] Pre-filter: passed — %s", title, reason)
        with results_lock:
            results["sent_to_ai"] += 1

        text     = smart_truncate(raw_text, MAX_CASE_CHARS)
        analysis = analyze_case(
            text=text,
            title=title,
            court=case_meta.get("court_name", ""),
            province=case_meta.get("province", ""),
        )

        if analysis is None:
            logger.warning("    [%s] Analysis failed — skipping", title)
            with results_lock:
                results["errors"] += 1
                results["ai_failed"] += 1
            continue

        if not analysis.get("is_relevant"):
            logger.info("    [%s] Not PI — skipping", title)
            with results_lock:
                results["skipped"] += 1
                results["ai_not_pi"] += 1
            continue

        try:
            save_case(case_meta, analysis)
        except Exception as exc:
            logger.error("    [%s] Save failed: %s — continuing", title, exc)
            with results_lock:
                results["errors"] += 1
            continue

        with results_lock:
            results["saved"] += 1
        logger.info(
            "    [%s] SAVED — %s | total: %s",
            title,
            analysis.get("case_type", "?"),
            (analysis.get("damages") or {}).get("total") or "N/A",
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    start = datetime.now()
    logger.info("=" * 65)
    logger.info("Daily run started: %s", start.strftime("%Y-%m-%d %H:%M"))

    ensure_data_dir()
    seen_ids = load_seen_ids()
    logger.info("Known case IDs: %d", len(seen_ids))

    # ── Resume from crash: check for leftover pending cases ────────────────
    pending = _load_pending()

    # ── Discovery: API (preferred) or RSS fallback ────────────────────────────
    feed_health = []
    if api_collector.api_available():
        logger.info("Discovery: CanLII API")
        new_cases = api_collector.fetch_new_cases(seen_ids)
    else:
        logger.info("Discovery: RSS feeds (set CANLII_API_KEY in config.py for API)")
        new_cases, feed_health = rss_collector.fetch_new_cases(seen_ids)

    logger.info("New cases to evaluate: %d", len(new_cases))

    # Mark new discoveries as seen immediately.
    # Cases that 403 are un-marked at the end so tomorrow retries them.
    for case_meta in new_cases:
        mark_seen(case_meta["case_id"])

    # Merge pending (from crash) with new discoveries, dedup by case_id
    if pending:
        new_ids = {c["case_id"] for c in new_cases}
        resumed = [c for c in pending if c["case_id"] not in new_ids]
        if resumed:
            logger.info("Merged %d resumed cases with %d new cases", len(resumed), len(new_cases))
            new_cases = resumed + new_cases

    if not new_cases:
        elapsed = (datetime.now() - start).seconds
        logger.info("Nothing new today. Run complete.")
        logger.info("=" * 65)
        _clear_pending()
        _send_daily_status(0, 0, 0, elapsed, feed_health, {"discovered": 0})
        return

    saved   = 0
    skipped = 0
    errors  = 0

    # Pipeline stats for the daily email
    stats = {
        "discovered":       len(new_cases),
        "title_rejected":   0,
        "rss_rejected":     0,
        "to_fetch":         0,
        "fetch_ok":         0,
        "fetch_failed":     0,
        "kw_rejected":      0,
        "sent_to_ai":       0,
        "ai_not_pi":        0,
        "ai_failed":        0,
    }

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
            stats["title_rejected"] += 1
            continue

        # RSS summary check — keyword scan on text already in the feed.
        # API discovery doesn't include summaries so this only runs for RSS.
        rss_summary = case_meta.get("rss_summary", "")
        if len(rss_summary) > 150:
            is_candidate, reason = prequalify(rss_summary, title)
            if not is_candidate:
                logger.info("─── %s\n    RSS: skipped — %s", title, reason)
                skipped += 1
                stats["rss_rejected"] += 1
                continue

        candidates.append(case_meta)

    logger.info(
        "Phase 0 complete: %d/%d survive title/RSS filter (%d skipped instantly)",
        len(candidates), len(new_cases), skipped,
    )

    if not candidates:
        elapsed = (datetime.now() - start).seconds
        logger.info("No candidates after pre-filter. Run complete.")
        logger.info("=" * 65)
        _clear_pending()
        _send_daily_status(saved, skipped, errors, elapsed, feed_health, stats)
        return

    stats["to_fetch"] = len(candidates)

    # ── Checkpoint: save candidates so a crash can resume from here ────────
    _save_pending(candidates)

    # ── Phase 1+2: Pipelined fetch → analyse ──────────────────────────────────
    # Main thread fetches (Playwright must stay on the thread that created it).
    # Background thread runs keyword filter + Claude analysis on each case as
    # it arrives.  This overlaps network I/O with AI calls.
    warmup()
    logger.info("Pipeline: fetching + analysing %d cases …", len(candidates))

    analyse_q     = queue.Queue(maxsize=5)
    fetch_failed  = set()
    results_lock  = threading.Lock()
    # Shared counters — analyser thread increments these under lock
    shared = {
        "saved": 0, "skipped": 0, "errors": 0,
        "kw_rejected": 0, "sent_to_ai": 0,
        "ai_failed": 0, "ai_not_pi": 0,
    }

    analyser = threading.Thread(
        target=_analyse_worker,
        args=(analyse_q, shared, results_lock),
        daemon=True,
    )
    analyser.start()

    # Main thread: fetch cases and feed them to the analyser
    fetch_ok_count = 0
    for case_meta in candidates:
        title = case_meta["title"]
        try:
            raw_text = fetch_case_text(
                case_meta["url"],
                db_id=case_meta.get("db_id", ""),
                citation=case_meta.get("citation", ""),
                title=title,
            )
            if raw_text is None:
                logger.warning("─── %s\n    fetch failed (403 or empty)", title)
                fetch_failed.add(case_meta["case_id"])
                errors += 1
            else:
                logger.info("─── %s\n    fetched: %d chars", title, len(raw_text))
                fetch_ok_count += 1
                analyse_q.put((case_meta, raw_text))
        except Exception as exc:
            logger.error("Unexpected fetch error for '%s': %s", title, exc)
            errors += 1

    # Signal end of fetching and wait for analyser to finish
    analyse_q.put(_SENTINEL)
    analyser.join()

    # Merge shared counters back
    with results_lock:
        saved   += shared["saved"]
        skipped += shared["skipped"]
        errors  += shared["errors"]
        stats["kw_rejected"] = shared["kw_rejected"]
        stats["sent_to_ai"]  = shared["sent_to_ai"]
        stats["ai_failed"]   = shared["ai_failed"]
        stats["ai_not_pi"]   = shared["ai_not_pi"]

    stats["fetch_ok"]     = fetch_ok_count
    stats["fetch_failed"] = len(fetch_failed)

    # ── Cookie refresh + retry for failed cases ───────────────────────────────
    if needs_cookie_refresh():
        _send_cookie_alert()
        if _trigger_cookie_refresh():
            rebuild_session()
            reset_403_counter()

            retry_cases = [c for c in candidates if c["case_id"] in fetch_failed]
            if retry_cases:
                logger.info(
                    "Retrying %d cases after session reset (%ds cooldown) …",
                    len(retry_cases), RETRY_COOLDOWN_SEC,
                )
                time.sleep(RETRY_COOLDOWN_SEC)
                errors -= len(retry_cases)

                retry_results, still_failed, retry_errors = _run_fetch_phase(retry_cases)
                fetch_failed = still_failed
                errors += retry_errors

                # Analyse retried cases immediately
                for case_meta, raw_text in retry_results:
                    title = case_meta["title"]
                    stats["fetch_ok"] += 1

                    is_cand, reason = prequalify(raw_text, title)
                    if not is_cand:
                        skipped += 1
                        stats["kw_rejected"] += 1
                        continue

                    stats["sent_to_ai"] += 1
                    text     = smart_truncate(raw_text, MAX_CASE_CHARS)
                    analysis = analyze_case(
                        text=text, title=title,
                        court=case_meta.get("court_name", ""),
                        province=case_meta.get("province", ""),
                    )
                    if analysis is None:
                        errors += 1; stats["ai_failed"] += 1; continue
                    if not analysis.get("is_relevant"):
                        skipped += 1; stats["ai_not_pi"] += 1; continue
                    try:
                        save_case(case_meta, analysis)
                    except Exception as exc:
                        errors += 1; continue
                    saved += 1

    # Un-mark cases that STILL failed so tomorrow's run picks them up
    if fetch_failed:
        unmark_seen(fetch_failed)
        logger.info(
            "%d case(s) still failing — un-marked for tomorrow's retry.",
            len(fetch_failed),
        )

    elapsed = (datetime.now() - start).seconds
    logger.info(
        "Run complete in %ds — saved: %d | skipped: %d | errors: %d",
        elapsed, saved, skipped, errors,
    )
    logger.info("=" * 65)

    # Run completed successfully — clear the crash-recovery checkpoint
    _clear_pending()

    # Close the browser and persist the session for tomorrow's run
    close_browser()

    # ── Daily status email ──────────────────────────────────────────────────
    _send_daily_status(saved, skipped, errors, elapsed, feed_health, stats)


# ── Failure notification helpers ─────────────────────────────────────────────

def _send_daily_status(saved: int, skipped: int, errors: int,
                       elapsed: int, feed_health: list[dict] | None = None,
                       stats: dict | None = None) -> None:
    """Send a status email after every daily run — success or not."""
    try:
        from email_report import send_alert_email

        if errors > 0:
            subject = f"PI Law Tracker — {errors} error(s) in daily run"
        elif saved > 0:
            subject = f"PI Law Tracker — {saved} new case(s) saved"
        else:
            subject = "PI Law Tracker — daily run OK, nothing new"

        body = f"<p>Daily run completed in {elapsed}s.</p>"

        # Pipeline breakdown — always included
        body += _build_pipeline_html(saved, errors, stats)

        # Always append feed summary so user can see feeds are alive
        feed_summary = _build_feed_summary_html(feed_health)
        if feed_summary:
            body += feed_summary

        # Escalate subject if any feeds are unhealthy
        if feed_health:
            bad = [h for h in feed_health if h["status"] != "OK"]
            if bad and "error" not in subject.lower():
                subject = subject.replace("daily run OK", "daily run OK, feed issues")

        send_alert_email(subject=subject, body=body)
    except Exception as exc:
        logger.warning("Could not send daily status email: %s", exc)


def _build_pipeline_html(saved: int, errors: int, stats: dict | None) -> str:
    """Build an HTML table showing where cases were filtered at each stage."""
    if not stats or not stats.get("discovered"):
        return "<p style='font-size:13px;color:#6b7280;'>No new cases in API/RSS feeds.</p>"

    s = stats
    rows = [
        ("Discovered in feeds",    s.get("discovered", 0)),
        ("Rejected by title",      f"-{s.get('title_rejected', 0)}"),
        ("Rejected by RSS keywords", f"-{s.get('rss_rejected', 0)}"),
        ("Sent to PDF fetch",      s.get("to_fetch", 0)),
        ("Fetch failed (403/timeout)", f"-{s.get('fetch_failed', 0)}"),
        ("Fetched OK",             s.get("fetch_ok", 0)),
        ("Rejected by keyword filter", f"-{s.get('kw_rejected', 0)}"),
        ("Sent to AI",             s.get("sent_to_ai", 0)),
        ("AI said not PI",         f"-{s.get('ai_not_pi', 0)}"),
        ("AI failed",              f"-{s.get('ai_failed', 0)}"),
    ]

    html = (
        "<table cellpadding='0' cellspacing='0' border='0' "
        "style='font-family:Arial,sans-serif;margin:10px 0;'>"
    )
    for label, val in rows:
        val_str = str(val)
        # Skip zero-subtract rows to reduce noise
        if val_str == "-0":
            continue
        color = "#dc2626" if val_str.startswith("-") else "#374151"
        html += (
            f"<tr>"
            f"<td style='padding:3px 12px 3px 0;font-size:13px;color:#6b7280;'>{label}</td>"
            f"<td style='padding:3px 0;font-size:13px;font-weight:600;color:{color};'>{val_str}</td>"
            f"</tr>"
        )
    # Final row — saved
    html += (
        f"<tr>"
        f"<td style='padding:6px 12px 3px 0;font-size:14px;font-weight:700;"
        f"color:#111827;border-top:2px solid #e5e7eb;'>PI cases saved</td>"
        f"<td style='padding:6px 0 3px 0;font-size:14px;font-weight:700;"
        f"color:#059669;border-top:2px solid #e5e7eb;'>{saved}</td>"
        f"</tr>"
        f"</table>"
    )
    return html


def _build_feed_summary_html(feed_health: list[dict] | None) -> str:
    """Build an HTML snippet showing RSS feed status — always included."""
    if not feed_health:
        return ""

    total_feeds  = len(feed_health)
    total_entries = sum(h["total"] for h in feed_health)
    ok_feeds     = [h for h in feed_health if h["status"] == "OK"]
    bad_feeds    = [h for h in feed_health if h["status"] != "OK"]

    html = (
        f"<hr style='border:none;border-top:1px solid #e5e7eb;margin:16px 0;'>"
        f"<p style='font-size:13px;color:#6b7280;'>"
        f"RSS: {len(ok_feeds)}/{total_feeds} feeds OK"
        f" &nbsp;&bull;&nbsp; {total_entries} entries polled</p>"
    )

    if bad_feeds:
        rows = ""
        for h in bad_feeds:
            rows += (
                f"<tr><td style='padding:4px 8px;font-size:13px;'>{h['court']}</td>"
                f"<td style='padding:4px 8px;font-size:13px;color:#dc2626;'>"
                f"{h['status']}</td></tr>"
            )
        html += (
            f"<p style='font-size:13px;font-weight:700;color:#dc2626;margin-top:8px;'>"
            f"Feed issues ({len(bad_feeds)}):</p>"
            f"<table cellpadding='0' cellspacing='0' border='0' "
            f"style='font-family:Arial,sans-serif;'>{rows}</table>"
        )

    return html


def _send_crash_alert(error: Exception) -> None:
    """Send an email when the daily run crashes entirely."""
    import traceback
    tb = traceback.format_exc()
    try:
        from email_report import send_alert_email
        send_alert_email(
            subject="PI Law Tracker — daily run CRASHED",
            body=(
                f"<p>The daily run crashed with an unhandled exception:</p>"
                f"<pre style=\"font-size:12px;background:#f3f4f6;"
                f"padding:12px;border-radius:6px;overflow-x:auto;\">"
                f"{tb}</pre>"
                f"<p>The run did not complete. Check the log and fix "
                f"the issue before the next scheduled run.</p>"
            ),
        )
    except Exception as exc:
        logger.warning("Could not send crash alert email: %s", exc)


def _parse_args():
    parser = argparse.ArgumentParser(description="PI Law Tracker daily pipeline")
    parser.add_argument(
        "--provinces", type=str, default=None,
        help="Comma-separated province codes to limit collection (e.g. NB,NS,NL,PE)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # Province filter — limit which courts we pull from
    if args.provinces:
        province_set = {p.strip().upper() for p in args.provinces.split(",")}
        from courts import COURTS as _ALL_COURTS
        import courts
        courts.COURTS = [c for c in _ALL_COURTS if c["province"] in province_set]
        logger.info("Province filter: %s (%d courts)", args.provinces, len(courts.COURTS))

    try:
        run()
    except Exception as exc:
        logger.critical("UNHANDLED EXCEPTION — daily run crashed: %s", exc,
                        exc_info=True)
        _send_crash_alert(exc)
