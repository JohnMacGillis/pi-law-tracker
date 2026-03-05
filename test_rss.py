"""
test_rss.py
Audit every RSS feed — shows total entries, date range, and whether
the feed is actually returning data. Run this to verify you're not
missing any courts.

Usage:
    python test_rss.py
"""

import time
from datetime import datetime

import feedparser
import requests

from courts import COURTS
from config import REQUEST_DELAY_SECONDS

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _parse_date(entry) -> str:
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d")
        if hasattr(entry, "updated_parsed") and entry.updated_parsed:
            return datetime(*entry.updated_parsed[:6]).strftime("%Y-%m-%d")
    except Exception:
        pass
    return "Unknown"


def main():
    print("=" * 75)
    print("  RSS FEED AUDIT")
    print("=" * 75)
    print()

    total_entries = 0
    ok_count = 0
    problem_count = 0

    for court in COURTS:
        name = court["name"]
        url  = court["rss"]

        try:
            resp = requests.get(url, headers={"User-Agent": _UA}, timeout=15)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            count = len(feed.entries)
            total_entries += count

            if not feed.entries:
                status = "EMPTY"
                oldest = newest = "N/A"
                problem_count += 1
            else:
                dates = [_parse_date(e) for e in feed.entries]
                known = sorted([d for d in dates if d != "Unknown"])
                oldest = known[0] if known else "Unknown"
                newest = known[-1] if known else "Unknown"

                days_old = None
                if newest != "Unknown":
                    days_old = (datetime.now() - datetime.strptime(newest, "%Y-%m-%d")).days

                if days_old and days_old > 14:
                    status = f"STALE ({days_old}d)"
                    problem_count += 1
                else:
                    status = "OK"
                    ok_count += 1

            print(f"  {court['province']}  {name}")
            print(f"      Entries: {count}  |  Oldest: {oldest}  |  Newest: {newest}  |  {status}")
            print()

        except requests.RequestException as exc:
            print(f"  {court['province']}  {name}")
            print(f"      HTTP ERROR: {exc}")
            print()
            problem_count += 1
        except Exception as exc:
            print(f"  {court['province']}  {name}")
            print(f"      ERROR: {exc}")
            print()
            problem_count += 1

        time.sleep(REQUEST_DELAY_SECONDS)

    print("=" * 75)
    print(f"  {len(COURTS)} feeds checked  |  {ok_count} OK  |  {problem_count} problems  |  {total_entries} total entries")
    print("=" * 75)


if __name__ == "__main__":
    main()
