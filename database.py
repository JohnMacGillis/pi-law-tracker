"""
database.py
Manages the two persistence files:

  data/cases.csv          — one row per confirmed PI damages case
  data/seen_case_ids.txt  — one CanLII URL per line (already processed)
"""

import csv
import os
import logging
import time
from datetime import datetime

from config import CASES_CSV, SEEN_IDS_FILE, DATA_DIR

logger = logging.getLogger(__name__)

# Ordered column list — do not reorder without migrating existing CSV
CSV_FIELDS = [
    "date_fetched",
    "decision_date",
    "title",
    "jurisdiction",
    "province",
    "case_type",
    "canlii_url",
    "summary",
    "non_pecuniary",
    "general_damages",
    "past_income_loss",
    "future_income_loss",
    "cost_of_future_care",
    "special_damages",
    "aggravated_punitive",
    "total_damages",
    "notes",
    "case_id",
]


# ── Initialisation ────────────────────────────────────────────────────────────

def ensure_data_dir() -> None:
    """Create the data directory and CSV header if they don't exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CASES_CSV):
        with open(CASES_CSV, "w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=CSV_FIELDS).writeheader()
        logger.info("Created new cases CSV: %s", CASES_CSV)


# ── Seen-IDs tracking ─────────────────────────────────────────────────────────

def load_seen_ids() -> set:
    """Return the set of all case IDs that have already been processed."""
    if not os.path.exists(SEEN_IDS_FILE):
        return set()
    with open(SEEN_IDS_FILE, "r", encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


def mark_seen(case_id: str) -> None:
    """Append a case ID to the seen-IDs file."""
    for attempt in range(1, 4):
        try:
            with open(SEEN_IDS_FILE, "a", encoding="utf-8") as fh:
                fh.write(case_id + "\n")
            return
        except PermissionError:
            if attempt < 3:
                logger.warning("seen_ids locked (attempt %d/3) — retrying …", attempt)
                time.sleep(attempt * 3)
            else:
                logger.error("seen_ids still locked — skipping mark_seen for %s", case_id)
                raise


def unmark_seen(case_ids: set) -> None:
    """
    Remove a set of case IDs from the seen-IDs file.

    Used after a cookie refresh so that cases that 403'd during today's run
    will be picked up again in tomorrow's run (they remain in the RSS feed
    for several weeks).
    """
    if not case_ids or not os.path.exists(SEEN_IDS_FILE):
        return
    with open(SEEN_IDS_FILE, "r", encoding="utf-8") as fh:
        existing = [line.strip() for line in fh if line.strip()]
    remaining = [cid for cid in existing if cid not in case_ids]
    with open(SEEN_IDS_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(remaining))
        if remaining:
            fh.write("\n")
    removed = len(existing) - len(remaining)
    logger.info("unmark_seen: removed %d case ID(s) for tomorrow's retry.", removed)


# ── Case storage ──────────────────────────────────────────────────────────────

def save_case(case_meta: dict, analysis: dict) -> None:
    """
    Append a confirmed PI case to cases.csv.

    case_meta keys: case_id, title, url, province, court_name, published_date
    analysis keys:  case_type, summary, damages (dict), notes
    """
    dmg = analysis.get("damages") or {}

    row = {
        "date_fetched":       datetime.now().strftime("%Y-%m-%d"),
        "decision_date":      case_meta.get("published_date", ""),
        "title":              case_meta.get("title", ""),
        "jurisdiction":       case_meta.get("court_name", ""),
        "province":           case_meta.get("province", ""),
        "case_type":          analysis.get("case_type") or "",
        "canlii_url":         case_meta.get("url", ""),
        "summary":            analysis.get("summary") or "",
        "non_pecuniary":      dmg.get("non_pecuniary") or "",
        "general_damages":    dmg.get("general_damages") or "",
        "past_income_loss":   dmg.get("past_income_loss") or "",
        "future_income_loss": dmg.get("future_income_loss") or "",
        "cost_of_future_care":dmg.get("cost_of_future_care") or "",
        "special_damages":    dmg.get("special_damages") or "",
        "aggravated_punitive":dmg.get("aggravated_punitive") or "",
        "total_damages":      dmg.get("total") or "",
        "notes":              analysis.get("notes") or "",
        "case_id":            case_meta.get("case_id", ""),
    }

    # Retry loop — Windows file locks (Excel, antivirus) can hold the CSV
    for attempt in range(1, 6):
        try:
            with open(CASES_CSV, "a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=CSV_FIELDS).writerow(row)
            break
        except PermissionError:
            if attempt < 5:
                logger.warning(
                    "cases.csv locked (attempt %d/5) — retrying in %ds …",
                    attempt, attempt * 5,
                )
                time.sleep(attempt * 5)   # 5s, 10s, 15s, 20s
            else:
                logger.error("cases.csv still locked after 5 attempts — skipping this case.")
                return   # Don't crash the entire run over a file lock

    logger.info("Saved: %s | %s | total=%s",
                row["title"], row["case_type"], row["total_damages"] or "N/A")


# ── Reporting queries ─────────────────────────────────────────────────────────

def load_cases_since(date_str: str) -> list[dict]:
    """
    Return all rows from cases.csv where date_fetched >= date_str.
    date_str format: YYYY-MM-DD
    """
    if not os.path.exists(CASES_CSV):
        return []

    results = []
    with open(CASES_CSV, "r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("date_fetched", "") >= date_str:
                results.append(row)

    return results
