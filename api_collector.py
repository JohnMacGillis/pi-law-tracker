"""
api_collector.py
Discovers new court decisions via the CanLII REST API.

The API returns structured JSON metadata (title, citation, date, URL) for each
decision — no DataDome cookies, no CAPTCHA, no rate-limit surprises at the
discovery stage.  Full case text is still fetched via PDF in Phase 1.

How to get an API key (free):
  https://www.canlii.org/en/feedback/feedback.html
  → describe your use (legal research / practice management)
  → typically approved within a few days

Set  CANLII_API_KEY  in config.py.  If blank, daily_run.py falls back to RSS.
"""

import logging
import time
from datetime import datetime, timedelta

import requests

from config import CANLII_API_KEY, REQUEST_DELAY_SECONDS
from courts import COURTS

logger = logging.getLogger(__name__)

_API_BASE     = "https://api.canlii.org/v1"
_MAX_PER_COURT = 100   # Max cases to pull per court per run (API ceiling: 10,000)
_LOOKBACK_DAYS = 7     # Fetch cases published in the last N days (seen_ids prevents reprocessing)


def api_available() -> bool:
    """Return True if a CanLII API key is configured."""
    return bool(CANLII_API_KEY and CANLII_API_KEY.strip())


def _fetch_court(db_id: str, province: str, court_name: str,
                 published_after: str, seen_ids: set) -> list[dict]:
    """Query the caseBrowse API for one court and return new case dicts."""
    url = f"{_API_BASE}/caseBrowse/en/{db_id}/"
    params = {
        "api_key":       CANLII_API_KEY,
        "offset":        0,
        "resultCount":   _MAX_PER_COURT,
        "publishedAfter": published_after,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
    except Exception as exc:
        logger.error("CanLII API request failed for %s: %s", db_id, exc)
        return []

    if resp.status_code == 401:
        logger.error("CanLII API: invalid key (401). Check CANLII_API_KEY in config.py.")
        return []
    if resp.status_code == 404:
        logger.warning("CanLII API: unknown database '%s' (404) — check courts.py db_id.", db_id)
        return []
    if not resp.ok:
        logger.warning("CanLII API: HTTP %d for %s", resp.status_code, db_id)
        return []

    try:
        raw = resp.json()
    except Exception as exc:
        logger.error("CanLII API: JSON parse error for %s: %s", db_id, exc)
        return []

    results = []
    for c in raw.get("cases", []):
        case_url = c.get("url", "")
        case_id  = case_url          # Use URL as unique ID (matches RSS collector)
        if not case_id or case_id in seen_ids:
            continue

        # The list endpoint returns: databaseId, caseId, url, title, citation
        # decisionDate is available in the per-case metadata endpoint but we
        # skip that extra call here — Claude will extract the date from the PDF.
        results.append({
            "case_id":        case_id,
            "title":          c.get("title", "Unknown").strip(),
            "url":            case_url,
            "province":       province,
            "court_name":     court_name,
            "published_date": c.get("decisionDate", "Unknown"),
            "citation":       c.get("citation", ""),
            "rss_summary":    "",   # API list endpoint has no text snippets
        })

    return results


def fetch_new_cases(seen_ids: set) -> list[dict]:
    """
    Poll every monitored court via the CanLII API and return unseen cases
    published in the last  _LOOKBACK_DAYS  days.

    This is a drop-in replacement for rss_collector.fetch_new_cases().
    Returns the same dict format: {case_id, title, url, province, court_name,
                                   published_date, citation, rss_summary}
    """
    if not api_available():
        raise RuntimeError("CanLII API key not set — call api_available() first.")

    published_after = (
        datetime.now() - timedelta(days=_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    logger.info(
        "CanLII API: fetching cases published after %s for %d courts …",
        published_after, len(COURTS),
    )

    new_cases: list[dict] = []

    for court in COURTS:
        db_id      = court["db_id"]
        province   = court["province"]
        court_name = court["name"]

        logger.info("  API → %s", court_name)
        cases = _fetch_court(db_id, province, court_name, published_after, seen_ids)
        new_cases.extend(cases)
        logger.info("    %d new case(s)", len(cases))

        time.sleep(1)   # 1s between courts — no DataDome, so short pause is fine

    logger.info("API collection complete — %d new case(s) found", len(new_cases))
    return new_cases
