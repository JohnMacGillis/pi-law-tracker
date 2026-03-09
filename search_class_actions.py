"""
search_class_actions.py
Search ALL of CanLII for "class action" cases from the last year
using the full-text search API endpoint.

Usage:
    python search_class_actions.py

Outputs results to data/class_actions.csv
"""

import csv
import os
import time
from datetime import datetime, timedelta

import requests

from config import CANLII_API_KEY, DATA_DIR

_API_BASE = "https://api.canlii.org/v1"

# Hard cap — the search API can return tens of thousands of results.
# 10,000 is more than enough to capture all class action cases from one year.
_MAX_RESULTS = 10_000


def _search(query: str) -> list[dict]:
    """
    Full-text search across ALL CanLII databases.
    Pages through results up to _MAX_RESULTS.
    """
    all_results = []
    offset = 0
    batch = 100

    while offset < _MAX_RESULTS:
        print(f"    Fetching results {offset + 1}–{offset + batch} …", flush=True)

        resp = requests.get(
            f"{_API_BASE}/search/en/",
            params={
                "api_key": CANLII_API_KEY,
                "searchQuery": query,
                "resultCount": batch,
                "offset": offset,
            },
            timeout=30,
        )

        if resp.status_code == 429:
            print(f"    Rate limited — waiting 30s …")
            time.sleep(30)
            continue
        if not resp.ok:
            print(f"    HTTP {resp.status_code}: {resp.text[:200]}")
            break

        data = resp.json()
        results = data.get("results", [])
        total = data.get("totalResults", "?")

        if offset == 0:
            print(f"    Total results on CanLII: {total}")
            if isinstance(total, int) and total > _MAX_RESULTS:
                print(f"    (capping at {_MAX_RESULTS} — date filter applied after)")

        for r in results:
            case = r.get("case", r)
            db_obj = case.get("databaseId", {})
            db_id = db_obj if isinstance(db_obj, str) else db_obj.get("databaseId", "")

            case_id_obj = case.get("caseId", {})
            case_id = case_id_obj.get("en", "") if isinstance(case_id_obj, dict) else str(case_id_obj)

            all_results.append({
                "title":         case.get("title", ""),
                "citation":      case.get("citation", ""),
                "decision_date": case.get("decisionDate", ""),
                "url":           case.get("url", ""),
                "db_id":         db_id,
                "case_id":       case_id,
            })

        if len(results) < batch:
            break
        offset += batch
        time.sleep(2)

    return all_results


def main():
    if not CANLII_API_KEY or not CANLII_API_KEY.strip():
        print("  ERROR: Set CANLII_API_KEY in config.py first.")
        return

    one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    print("=" * 70)
    print('  CanLII Full-Text Search: "class action"')
    print(f"  Filtering to decisions after: {one_year_ago}")
    print("=" * 70)
    print()

    results = _search('"class action"')

    if not results:
        print("  No results found. Check your API key and try again.")
        return

    print(f"\n  Raw results fetched: {len(results)}")

    # Deduplicate and filter to last 365 days
    # IMPORTANT: only include cases WITH a valid date that falls within range.
    # Cases with no date are excluded (we can't verify they're recent).
    seen = set()
    unique = []
    no_date = 0
    too_old = 0

    for r in results:
        key = r["url"] or r["citation"]
        if not key or key in seen:
            continue
        seen.add(key)

        d = r.get("decision_date", "").strip()
        if not d:
            no_date += 1
            continue
        if d < one_year_ago:
            too_old += 1
            continue

        unique.append(r)

    print(f"  Duplicates removed:  {len(results) - len(seen)}")
    print(f"  No decision date:    {no_date} (excluded)")
    print(f"  Older than 365 days: {too_old} (excluded)")
    print(f"  ─────────────────────────────")
    print(f"  Cases in last year:  {len(unique)}")

    # Save to CSV
    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, "class_actions.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "title", "citation", "decision_date", "url",
        ])
        writer.writeheader()
        for r in unique:
            writer.writerow({k: r[k] for k in writer.fieldnames})

    print(f"\n{'=' * 70}")
    print(f"  Saved {len(unique)} cases → {out}")
    print(f"{'=' * 70}\n")

    # Print first 30 for quick review
    for i, r in enumerate(unique[:30], 1):
        print(f"  {i:3d}. {r['title'][:80]}")
        print(f"       {r['citation']}  |  {r['decision_date']}")
        print()

    if len(unique) > 30:
        print(f"  … and {len(unique) - 30} more (see {out})")


if __name__ == "__main__":
    main()
