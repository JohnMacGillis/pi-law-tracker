"""
search_class_actions.py
Search CanLII for class action cases from the last year.

Two-phase approach:
  Phase 1 — API full-text search for class action keywords.
            Fetches up to 10,000 results per query (sorted by relevance).
  Phase 2 — Filter: year from citation + title must contain a class action term.
            This removes the thousands of cases that merely *mention* "class action"
            in passing (criminal cases, family law, labour, etc.).

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

# Fetch up to 10K per query — the API sorts by relevance so we need enough
# depth to catch recent cases that may not rank highest.
_MAX_PER_QUERY = 10_000

# ── Title keywords ───────────────────────────────────────────────────────────
# A case is only included if its TITLE contains one of these.
# This filters out the thousands of cases that merely cite or mention
# class actions in their body text.
_TITLE_KEYWORDS = [
    # English
    "class action",
    "class proceeding",
    "class proc.",
    "certification",
    "representative plaintiff",
    "settlement approval",
    "common issues",
    "aggregate damages",
    "class member",
    "opt out",
    "opt-out",
    # French
    "recours collectif",
    "action collective",
    "autorisation d",      # autorisation d'exercer un recours collectif
    "membres du groupe",
    "règlement",           # settlement
]


def _title_is_class_action(title: str) -> bool:
    """Check if a case title suggests it's actually a class action case."""
    t = title.lower()
    return any(kw in t for kw in _TITLE_KEYWORDS)


def _year_from_citation(citation: str) -> int | None:
    """
    Extract the decision year from a Canadian legal citation.
      "2025 ONSC 1234"  → 2025
      "2024 BCCA 567"   → 2024
      "[2024] 3 SCR 45" → 2024
    """
    m = re.match(r"(\d{4})\s+\w+", citation.strip())
    if m:
        return int(m.group(1))
    m = re.search(r"\[(\d{4})\]", citation)
    if m:
        return int(m.group(1))
    return None


def _search(query: str) -> list[dict]:
    """
    Full-text search across ALL CanLII databases.
    Pages through results up to _MAX_PER_QUERY.
    """
    all_results = []
    offset = 0
    batch = 100

    while offset < _MAX_PER_QUERY:
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
            print(f"    Total on CanLII: {total}")

        for r in results:
            case = r.get("case", r)
            citation = case.get("citation", "")

            all_results.append({
                "title":         case.get("title", ""),
                "citation":      citation,
                "decision_year": _year_from_citation(citation),
                "decision_date": case.get("decisionDate", ""),
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

    # Search queries — English and French variants
    queries = [
        '"class action"',
        '"class proceeding"',
        '"recours collectif"',
        '"action collective"',
    ]

    print("=" * 70)
    print("  CanLII Class Action Search")
    print(f"  Queries: {', '.join(queries)}")
    print(f"  Year filter: {cutoff_year}+")
    print(f"  Title filter: must contain class action keywords")
    print("=" * 70)
    print()

    # Phase 1: Search
    raw_results = []
    for q in queries:
        print(f"  Searching: {q}")
        batch = _search(q)
        print(f"    → {len(batch)} results\n")
        raw_results.extend(batch)

    if not raw_results:
        print("  No results found. Check your API key and try again.")
        return

    # Phase 2: Deduplicate + year filter + title filter
    seen = set()
    unique = []
    no_year = 0
    too_old = 0
    title_rejected = 0

    for r in raw_results:
        key = r["url"] or r["citation"]
        if not key or key in seen:
            continue
        seen.add(key)

        # Year filter
        year = r.get("decision_year")
        if year is None:
            no_year += 1
            continue
        if year < cutoff_year:
            too_old += 1
            continue

        # Title filter — must actually BE a class action case
        if not _title_is_class_action(r["title"]):
            title_rejected += 1
            continue

        unique.append(r)

    deduped = len(raw_results) - len(seen)

    print(f"  Raw results:             {len(raw_results)}")
    print(f"  Duplicates removed:      {deduped}")
    print(f"  No year in citation:     {no_year}")
    print(f"  Older than {cutoff_year}:         {too_old}")
    print(f"  Title not class action:  {title_rejected}")
    print(f"  ─────────────────────────────────")
    print(f"  Class action cases:      {len(unique)}")

    # Sort newest first
    unique.sort(key=lambda r: r.get("decision_year", 0), reverse=True)

    # Save to CSV
    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, "class_actions.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "title", "citation", "decision_date", "url",
        ])
        writer.writeheader()
        for r in unique:
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

    for i, r in enumerate(unique[:30], 1):
        print(f"  {i:3d}. {r['title'][:80]}")
        print(f"       {r['citation']}")
        print()

    if len(unique) > 30:
        print(f"  … and {len(unique) - 30} more (see {out})")


if __name__ == "__main__":
    main()
