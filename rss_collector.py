"""
rss_collector.py
Fetches CanLII RSS feeds for every monitored court and returns
cases that have not been processed before.
"""

import html
import re
import time
import logging
from datetime import datetime, timedelta

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
    feed_health: list[dict] = []       # per-court health tracking
    failed_feeds: list[str] = []

    for court in COURTS:
        logger.info("Fetching RSS → %s", court["name"])
        court_new = 0
        try:
            feed = feedparser.parse(court["rss"])

            # feedparser sets bozo=True on any parse issue
            if feed.bozo:
                logger.warning(
                    "RSS parse warning for %s: %s",
                    court["name"],
                    feed.bozo_exception,
                )

            total_entries = len(feed.entries)

            if not feed.entries:
                logger.warning("No entries in feed for %s", court["name"])
                failed_feeds.append(court["name"])
                feed_health.append({
                    "court": court["name"],
                    "province": court["province"],
                    "total": 0,
                    "new": 0,
                    "newest": "N/A",
                    "status": "⚠ EMPTY",
                })
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            # Find the newest entry date for health reporting
            entry_dates = [_parse_date(e) for e in feed.entries]
            known_dates = [d for d in entry_dates if d != "Unknown"]
            newest_date = max(known_dates) if known_dates else "Unknown"

            cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

            for entry in feed.entries:
                case_id = _case_id_from_entry(entry)
                if not case_id or case_id in seen_ids:
                    continue

                # Skip cases older than 7 days — prevents reprocessing stale
                # entries if seen_ids is ever cleared
                pub_date = _parse_date(entry)
                if pub_date != "Unknown" and pub_date < cutoff:
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
                court_new += 1

            # Flag stale feeds — if the newest entry is over 14 days old,
            # the feed may be broken or the court stopped publishing
            days_old = None
            status = "OK"
            if newest_date != "Unknown":
                try:
                    days_old = (datetime.now() - datetime.strptime(newest_date, "%Y-%m-%d")).days
                    if days_old > 14:
                        status = f"⚠ STALE ({days_old}d old)"
                except ValueError:
                    pass

            feed_health.append({
                "court": court["name"],
                "province": court["province"],
                "total": total_entries,
                "new": court_new,
                "newest": newest_date,
                "status": status,
            })

        except Exception as exc:
            logger.error("Failed to fetch RSS for %s: %s", court["name"], exc)
            failed_feeds.append(court["name"])
            feed_health.append({
                "court": court["name"],
                "province": court["province"],
                "total": 0,
                "new": 0,
                "newest": "N/A",
                "status": f"✗ ERROR: {exc}",
            })

        time.sleep(REQUEST_DELAY_SECONDS)

    # ── Feed health summary ──────────────────────────────────────────────────
    logger.info("─" * 65)
    logger.info("RSS FEED HEALTH REPORT")
    logger.info("%-45s %5s %4s  %-12s  %s", "Court", "Total", "New", "Newest", "Status")
    logger.info("%-45s %5s %4s  %-12s  %s", "─" * 45, "─" * 5, "─" * 4, "─" * 12, "─" * 10)
    for h in feed_health:
        logger.info(
            "%-45s %5d %4d  %-12s  %s",
            h["court"], h["total"], h["new"], h["newest"], h["status"],
        )
    logger.info("─" * 65)

    if failed_feeds:
        logger.warning(
            "⚠ %d feed(s) returned errors or empty: %s",
            len(failed_feeds), ", ".join(failed_feeds),
        )

    stale = [h for h in feed_health if h["status"].startswith("⚠ STALE")]
    if stale:
        logger.warning(
            "⚠ %d feed(s) appear stale (no entries in 14+ days): %s",
            len(stale), ", ".join(h["court"] for h in stale),
        )

    logger.info("RSS collection complete — %d new case(s) found", len(new_cases))
    return new_cases
