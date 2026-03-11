"""
api_collector.py
Discovers new court decisions via the CanLII REST API.

Uses the caseBrowse list endpoint to find recently-published cases, then
constructs CanLII URLs from databaseId + caseId (the list endpoint does NOT
return a url field — only the per-case metadata endpoint does).

How to get an API key (free):
  https://www.canlii.org/en/feedback/feedback.html
  → describe your use (legal research / practice management)
  → typically approved within a few days

Set  CANLII_API_KEY  in config.py.  If blank, daily_run.py falls back to RSS.
"""

import logging
import re
import time
from datetime import datetime, timedelta

import requests

from config import CANLII_API_KEY
from courts import COURTS

logger = logging.getLogger(__name__)

_API_BASE       = "https://api.canlii.org/v1"
_MAX_PER_COURT  = 100   # Max cases to pull per court per run (API ceiling: 10,000)
_LOOKBACK_DAYS  = 60  # Only include cases decided within this many days

# Province code → CanLII jurisdiction path (used for URL construction)
_PROVINCE_TO_JUR = {
    "AB": "ab", "BC": "bc", "MB": "mb", "NB": "nb", "NL": "nl",
    "NS": "ns", "NT": "nt", "NU": "nu", "ON": "on", "PE": "pe",
    "QC": "qc", "SK": "sk", "YT": "yt", "CA": "ca",
}


def api_available() -> bool:
    """Return True if a CanLII API key is configured."""
    return bool(CANLII_API_KEY and CANLII_API_KEY.strip())


def _build_case_url(db_id: str, case_id_raw: str, province: str) -> str:
    """
    Construct a CanLII URL from databaseId + caseId.
    e.g. db_id="onsc", case_id_raw="2025onsc1234", province="ON"
    → https://www.canlii.org/en/on/onsc/doc/2025/2025onsc1234/2025onsc1234.html
    """
    jur = _PROVINCE_TO_JUR.get(province, "")
    if not jur or not case_id_raw:
        return ""
    year_match = re.match(r"(\d{4})", case_id_raw)
    if not year_match:
        return ""
    year = year_match.group(1)
    return f"https://www.canlii.org/en/{jur}/{db_id}/doc/{year}/{case_id_raw}/{case_id_raw}.html"


def _extract_case_id(case_obj: dict) -> str:
    """Extract the case ID string from the API response object."""
    cid = case_obj.get("caseId", "")
    if isinstance(cid, dict):
        return cid.get("en", "") or cid.get("fr", "")
    return str(cid) if cid else ""


def _fetch_court(db_id: str, province: str, court_name: str,
                 seen_ids: set) -> list[dict]:
    """Query the caseBrowse API for one court and return new case dicts."""
    url = f"{_API_BASE}/caseBrowse/en/{db_id}/"
    # Don't use publishedAfter — CanLII's date parameters are unreliable
    # (same issue as the search endpoint).  Fetch the most recent cases
    # and let seen_ids handle dedup.
    params = {
        "api_key":       CANLII_API_KEY,
        "offset":        0,
        "resultCount":   _MAX_PER_COURT,
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

    cases_list = raw.get("cases", [])
    if not cases_list:
        logger.warning("    %s: API returned 0 cases (empty response)", db_id)

    results = []
    no_url = 0
    already_seen = 0
    too_old = 0
    for c in cases_list:
        # Client-side date filter — only include cases decided recently
        decision_date = c.get("decisionDate", "")
        cutoff = (datetime.now() - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        if not decision_date or decision_date < cutoff:
            too_old += 1
            continue

        # The list endpoint does NOT return 'url' — construct it from
        # databaseId + caseId + province jurisdiction mapping.
        case_id_str = _extract_case_id(c)
        case_url = _build_case_url(db_id, case_id_str, province)

        if not case_url:
            no_url += 1
            continue
        if case_url in seen_ids:
            already_seen += 1
            continue

        results.append({
            "case_id":        case_url,   # URL as unique ID (matches RSS collector)
            "title":          c.get("title", "Unknown").strip(),
            "url":            case_url,
            "province":       province,
            "court_name":     court_name,
            "published_date": decision_date or "Unknown",
            "citation":       c.get("citation", ""),
            "rss_summary":    "",
        })

    if no_url:
        logger.warning("    %s: %d case(s) skipped — could not construct URL", db_id, no_url)
    logger.debug("    %s: %d returned, %d old, %d seen, %d no-url, %d new",
                 db_id, len(cases_list), too_old, already_seen, no_url, len(results))

    return results


def _extract_db_and_case(case_url: str) -> tuple[str, str]:
    """
    Extract (db_id, case_id) from a constructed CanLII URL.
    e.g. ".../en/on/onsc/doc/2025/2025onsc1234/2025onsc1234.html"
    → ("onsc", "2025onsc1234")
    """
    try:
        parts = case_url.rstrip("/").replace(".html", "").split("/")
        # URL: …/en/{jur}/{db_id}/doc/{year}/{case_id}/{case_id}.html
        # parts[-1] = case_id, parts[-4] = db_id
        return parts[-4], parts[-1]
    except (IndexError, ValueError):
        return "", ""


def fetch_cited_legislations(case_url: str) -> list[str]:
    """
    Call the caseCitator API to get legislation titles cited by a case.
    Returns a list of legislation title strings (lowercase).
    """
    db_id, case_id = _extract_db_and_case(case_url)
    if not db_id or not case_id:
        return []

    url = f"{_API_BASE}/caseCitator/en/{db_id}/{case_id}/citedLegislations"
    try:
        resp = requests.get(
            url,
            params={"api_key": CANLII_API_KEY},
            timeout=20,
        )
        if not resp.ok:
            return []
        data = resp.json()
        titles = []
        for leg in data.get("citedLegislations", []):
            title = leg.get("title", "")
            if not title:
                # Sometimes nested under legislation object
                leg_obj = leg.get("legislation", {})
                title = leg_obj.get("title", "")
            if title:
                titles.append(title.lower())
        return titles
    except Exception:
        return []


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

    logger.info(
        "CanLII API: fetching latest cases from %d courts …",
        len(COURTS),
    )

    new_cases: list[dict] = []

    for court in COURTS:
        db_id      = court["db_id"]
        province   = court["province"]
        court_name = court["name"]

        logger.info("  API → %s", court_name)
        cases = _fetch_court(db_id, province, court_name, seen_ids)
        new_cases.extend(cases)
        logger.info("    %d new case(s)", len(cases))

        time.sleep(1)   # 1s between courts — no DataDome, so short pause is fine

    logger.info("API collection complete — %d new case(s) found", len(new_cases))
    return new_cases
