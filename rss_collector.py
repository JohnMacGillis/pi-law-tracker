"""
rss_collector.py
Fetches CanLII RSS feeds for every monitored court and returns
cases that have not been processed before.
"""

import html
import re
import time
import logging
from datetime import datetime

import feedparser

from config import REQUEST_DELAY_SECONDS
from courts import COURTS

logger = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    """Strip HTML tags and decode entities for plain-text keyword matching."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def _parse_date(entry) -> str:
    """Return YYYY-MM-DD from an RSS entry, or 'Unknown'."""
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d")
        if hasattr(entry, "updated_parsed") and entry.updated_parsed:
            return datetime(*entry.updated_parsed[:6]).strftime("%Y-%m-%d")
    except Exception:
        pass
    return "Unknown"


def _case_id_from_entry(entry) -> str:
    """
    Use the entry link (canonical CanLII URL) as the unique ID.
    Fallback to the RSS id field if link is absent.
    """
    return entry.get("link") or entry.get("id") or ""


def fetch_new_cases(seen_ids: set) -> list[dict]:
    """
    Poll every court RSS feed and return a list of unseen case dicts:
      {case_id, title, url, province, court_name, published_date}
    """
    new_cases: list[dict] = []

    for court in COURTS:
        logger.info("Fetching RSS → %s", court["name"])
        try:
            feed = feedparser.parse(court["rss"])

            # feedparser sets bozo=True on any parse issue
            if feed.bozo:
                logger.warning(
                    "RSS parse warning for %s: %s",
                    court["name"],
                    feed.bozo_exception,
                )

            if not feed.entries:
                logger.warning("No entries in feed for %s", court["name"])

            for entry in feed.entries:
                case_id = _case_id_from_entry(entry)
                if not case_id or case_id in seen_ids:
                    continue

                # CanLII RSS summaries contain the opening paragraphs of the
                # decision — enough for a first-pass keyword filter with no
                # additional HTTP requests.
                raw_summary = (
                    entry.get("summary")
                    or entry.get("description")
                    or ""
                )
                rss_summary = _strip_html(raw_summary)

                new_cases.append(
                    {
                        "case_id":        case_id,
                        "title":          entry.get("title", "Unknown").strip(),
                        "url":            entry.get("link", ""),
                        "province":       court["province"],
                        "court_name":     court["name"],
                        "published_date": _parse_date(entry),
                        "rss_summary":    rss_summary,
                    }
                )

        except Exception as exc:
            logger.error("Failed to fetch RSS for %s: %s", court["name"], exc)

        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info("RSS collection complete — %d new case(s) found", len(new_cases))
    return new_cases
