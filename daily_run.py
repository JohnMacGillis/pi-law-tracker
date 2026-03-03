"""
daily_run.py
Scheduled daily job — run this every morning via Windows Task Scheduler.

Pipeline for each new CanLII case:
  1. Fetch RSS feeds from all monitored courts
  2. Skip cases already in seen_case_ids.txt
  3. Fetch full case text from CanLII (PDF via curl_cffi + DataDome cookie)
  4. Send to Claude for PI relevance check + damage extraction
  5. If relevant, save to cases.csv

Cookie refresh:
  If CanLII starts returning 403 errors (DataDome cookie expired), the script
  automatically launches refresh_cookies.py — a Chrome window opens, you solve
  the slider (if prompted), click OK, and the run resumes where it left off.
  Cases that couldn't be fetched before the refresh are un-marked so they will
  be retried the following day (they stay in the RSS feed for several weeks).
"""

import logging
import os
import subprocess
import sys
from datetime import datetime

# Ensure the script can be run from any working directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import DATA_DIR, LOG_FILE, MAX_CASE_CHARS
from database import ensure_data_dir, load_seen_ids, mark_seen, save_case, unmark_seen
from rss_collector import fetch_new_cases
from case_fetcher import (
    fetch_case_text, smart_truncate,
    needs_cookie_refresh, rebuild_session, reset_403_counter,
)
from case_analyzer import analyze_case


# ── Logging setup (file + stdout) ─────────────────────────────────────────────
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


# ── Cookie refresh helper ──────────────────────────────────────────────────────

def _trigger_cookie_refresh() -> bool:
    """
    Launch refresh_cookies.py as a subprocess and wait for it to finish.

    This opens a Chrome window so the user can solve any DataDome slider.
    Returns True if the cookies were saved successfully.
    """
    logger.warning("=" * 65)
    logger.warning("COOKIE REFRESH TRIGGERED — opening Chrome to CanLII")
    logger.warning("A browser window will appear. Solve the slider if prompted,")
    logger.warning("then click OK. The daily run will resume automatically.")
    logger.warning("=" * 65)

    try:
        result = subprocess.run(
            [sys.executable, "refresh_cookies.py"],
            timeout=300,   # 5-minute window for user to act
        )
        if result.returncode == 0:
            logger.info("Cookie refresh completed successfully.")
            return True
        else:
            logger.error("refresh_cookies.py exited with code %d", result.returncode)
            return False
    except subprocess.TimeoutExpired:
        logger.error("Cookie refresh timed out (5 min). Run refresh_cookies.py manually.")
        return False
    except FileNotFoundError:
        logger.error("refresh_cookies.py not found in the script directory.")
        return False
    except Exception as exc:
        logger.error("Cookie refresh failed: %s", exc)
        return False


def _send_cookie_alert(email_enabled: bool = True) -> None:
    """Send an email alert if cookies need refreshing (best-effort)."""
    if not email_enabled:
        return
    try:
        # Import lazily so a SendGrid misconfiguration doesn't crash the run
        from email_report import send_alert_email
        send_alert_email(
            subject="⚠️  PI Law Tracker — Cookie Refresh Required",
            body=(
                "<p>The PI Law Tracker encountered multiple 403 errors from CanLII, "
                "which means the DataDome session cookie has expired.</p>"
                "<p><strong>Action required:</strong> On the Windows computer, "
                "double-click <em>REFRESH COOKIES.bat</em> on the Desktop, "
                "solve the slider if it appears, and click OK.</p>"
                "<p>Cases that could not be fetched today have been un-marked "
                "and will be retried in tomorrow's run.</p>"
            ),
        )
        logger.info("Cookie-refresh alert email sent.")
    except Exception as exc:
        logger.warning("Could not send alert email: %s", exc)


# ── Case processing helper ─────────────────────────────────────────────────────

def _process_case(case_meta: dict, saved_ref: list, skipped_ref: list,
                  errors_ref: list) -> None:
    """
    Fetch, analyse, and save one case.  Updates the mutable counter lists
    in-place (avoids needing nonlocal in the caller).
    """
    title = case_meta["title"]
    logger.info("─── %s", title)

    # ── Fetch full text ───────────────────────────────────────────────────────
    raw_text = fetch_case_text(case_meta["url"])
    if not raw_text:
        errors_ref[0] += 1
        return  # caller handles the 403-threshold check

    text = smart_truncate(raw_text, MAX_CASE_CHARS)
    logger.info(
        "    Text: %d chars (raw) → %d chars (sent to Claude)",
        len(raw_text), len(text),
    )

    # ── Analyse with Claude ───────────────────────────────────────────────────
    analysis = analyze_case(
        text=text,
        title=title,
        court=case_meta.get("court_name", ""),
        province=case_meta.get("province", ""),
    )

    if analysis is None:
        logger.warning("    Analysis failed — skipping")
        errors_ref[0] += 1
        return

    if not analysis.get("is_relevant"):
        logger.info("    Not a PI damages case — skipping")
        skipped_ref[0] += 1
        return

    # ── Save ──────────────────────────────────────────────────────────────────
    save_case(case_meta, analysis)
    saved_ref[0] += 1
    logger.info(
        "    SAVED — type: %s | total: %s",
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
    logger.info("Known case IDs loaded: %d", len(seen_ids))

    # ── Step 1: Discover new cases via RSS ────────────────────────────────────
    new_cases = fetch_new_cases(seen_ids)
    logger.info("New cases to evaluate: %d", len(new_cases))

    if not new_cases:
        logger.info("Nothing new today. Run complete.")
        logger.info("=" * 65)
        return

    # Mutable counters passed by reference into _process_case
    saved   = [0]
    skipped = [0]
    errors  = [0]

    # Track which cases were marked-seen but couldn't be fetched (403 etc.)
    # so we can un-mark them if a cookie refresh succeeds.
    fetch_failed_ids: set[str] = set()
    cookie_refresh_done = False

    # ── Step 2-4: Process each case ───────────────────────────────────────────
    for case_meta in new_cases:
        case_id = case_meta["case_id"]

        # Mark seen immediately — prevents double-processing if we crash.
        # Cases that 403 will be un-marked at the end so they retry tomorrow.
        mark_seen(case_id)

        prev_errors = errors[0]
        _process_case(case_meta, saved, skipped, errors)
        fetch_failed = (errors[0] > prev_errors)

        if fetch_failed:
            fetch_failed_ids.add(case_id)

        # ── Cookie refresh check ──────────────────────────────────────────────
        if needs_cookie_refresh() and not cookie_refresh_done:
            logger.warning(
                "3 consecutive 403s — triggering cookie refresh "
                "(%d cases in retry queue)", len(fetch_failed_ids),
            )

            # Try to send an email alert (non-blocking best-effort)
            _send_cookie_alert()

            # Launch the browser window for the user to solve the slider
            refresh_ok = _trigger_cookie_refresh()

            if refresh_ok:
                cookie_refresh_done = True
                rebuild_session()
                reset_403_counter()

                # Un-mark the 403'd cases so tomorrow's run picks them up
                unmark_seen(fetch_failed_ids)
                logger.info(
                    "Cookies refreshed. %d cases un-marked for tomorrow's retry.",
                    len(fetch_failed_ids),
                )
                fetch_failed_ids.clear()
            else:
                # Refresh failed — no point continuing, everything will 403
                logger.error(
                    "Cookie refresh failed. Stopping run early. "
                    "Run  python refresh_cookies.py  manually, then re-run."
                )
                # Un-mark failed cases so they're retried when cookies work again
                unmark_seen(fetch_failed_ids)
                break

    elapsed = (datetime.now() - start).seconds
    logger.info(
        "Run complete in %ds — saved: %d | skipped: %d | errors: %d",
        elapsed, saved[0], skipped[0], errors[0],
    )
    logger.info("=" * 65)


if __name__ == "__main__":
    run()
