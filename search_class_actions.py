"""
search_class_actions.py
Search ALL of CanLII for "class action" cases from the last year
using the full-text search API endpoint.

The search API doesn't return decisionDate, so we extract the year
from the citation (e.g. "2025 ONSC 1234" → 2025). This is reliable
for virtually all Canadian legal citations.

Usage:
    python search_class_actions.py

Outputs results to data/class_actions.csv
"""

import csv
import os
import re
import time
from datetime import datetime, timedelta

import requests

from config import CANLII_API_KEY, DATA_DIR

_API_BASE = "https://api.canlii.org/v1"

# Cap pages — search returns by relevance, most relevant first.
# 3000 is plenty: there aren't 3000 class action decisions per year.
_MAX_RESULTS = 3_000


def _year_from_citation(citation: str) -> int | None:
    """
    Extract the decision year from a Canadian legal citation.
    Examples:
      "2025 ONSC 1234"  → 2025
      "2024 BCCA 567"   → 2024
      "2023 SCC 12"     → 2023
      "[2024] 3 SCR 45" → 2024
    """
    # Try "YYYY CourtCode" pattern first (most common)
    m = re.match(r"(\d{4})\s+\w+", citation.strip())
    if m:
        return int(m.group(1))
    # Try "[YYYY]" pattern (older style)
    m = re.search(r"\[(\d{4})\]", citation)
    if m:
        return int(m.group(1))
    return None


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
                print(f"    (capping fetch at {_MAX_RESULTS} — date filter applied after)")

        for r in results:
            case = r.get("case", r)

            citation = case.get("citation", "")
            year = _year_from_citation(citation)

            all_results.append({
                "title":         case.get("title", ""),
                "citation":      citation,
                "decision_year": year,
                "decision_date": case.get("decisionDate", ""),  # often empty
                "url":           case.get("url", ""),
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

    cutoff_year = (datetime.now() - timedelta(days=365)).year

    # Multiple search queries to catch all variants:
    #   "class action"     — English standard term
    #   "class proceeding" — used in Ontario Class Proceedings Act
    #   "recours collectif" — French (Quebec)
    #   "action collective" — French alternate
    queries = [
        '"class action"',
        '"class proceeding"',
        '"recours collectif"',
        '"action collective"',
    ]

    print("=" * 70)
    print("  CanLII Class Action Search — All Variants")
    print(f"  Queries: {', '.join(queries)}")
    print(f"  Filtering to decisions from {cutoff_year} or later")
    print("=" * 70)
    print()

    results = []
    for q in queries:
        print(f"  Searching: {q}")
        batch = _search(q)
        print(f"    → {len(batch)} results\n")
        results.extend(batch)

    if not results:
        print("  No results found. Check your API key and try again.")
        return

    print(f"  Total raw results: {len(results)}")

    # Deduplicate and filter by year from citation
    seen = set()
    unique = []
    no_year = 0
    too_old = 0

    for r in results:
        key = r["url"] or r["citation"]
        if not key or key in seen:
            continue
        seen.add(key)

        year = r.get("decision_year")
        if year is None:
            no_year += 1
            continue
        if year < cutoff_year:
            too_old += 1
            continue

        unique.append(r)

    print(f"  Duplicates removed:      {len(results) - len(seen)}")
    print(f"  No year in citation:     {no_year} (excluded)")
    print(f"  Older than {cutoff_year}:         {too_old} (excluded)")
    print(f"  ─────────────────────────────────")
    print(f"  Cases from last year:    {len(unique)}")

    # Sort newest first
    unique.sort(key=lambda r: r.get("decision_year", 0), reverse=True)

    # Save to CSV — use citation year as decision_date fallback
    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, "class_actions.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "title", "citation", "decision_date", "url",
        ])
        writer.writeheader()
        for r in unique:
            # Use actual date if available, otherwise just the year
            date = r.get("decision_date", "").strip()
            if not date and r.get("decision_year"):
                date = str(r["decision_year"])
            writer.writerow({
                "title":         r["title"],
                "citation":      r["citation"],
                "decision_date": date,
                "url":           r["url"],
            })

    print(f"\n{'=' * 70}")
    print(f"  Saved {len(unique)} cases → {out}")
    print(f"{'=' * 70}\n")

    # Print first 30 for quick review
    for i, r in enumerate(unique[:30], 1):
        print(f"  {i:3d}. {r['title'][:80]}")
        print(f"       {r['citation']}")
        print()

    if len(unique) > 30:
        print(f"  … and {len(unique) - 30} more (see {out})")


if __name__ == "__main__":
    main()
