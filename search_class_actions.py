"""
search_class_actions.py
Search ALL of CanLII for class action cases from 2025 and 2026.

Two-phase approach:
  Phase 1 — Full-text search for class action keywords (English + French).
            Extracts databaseId + caseId from each result.
  Phase 2 — For cases from the target years, fetches metadata via caseBrowse
            to get the actual URL and decision date (the search API doesn't
            return these).

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

# Fetch up to 3K per query — after year filtering there will be far fewer.
_MAX_PER_QUERY = 3_000


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
    """Full-text search across ALL CanLII databases."""
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

            # Extract databaseId
            db_obj = case.get("databaseId", {})
            db_id = db_obj if isinstance(db_obj, str) else db_obj.get("databaseId", "")

            # Extract caseId
            case_id_obj = case.get("caseId", {})
            case_id = case_id_obj.get("en", "") if isinstance(case_id_obj, dict) else str(case_id_obj)

            all_results.append({
                "title":         case.get("title", ""),
                "citation":      citation,
                "decision_year": _year_from_citation(citation),
                "db_id":         db_id,
                "case_id":       case_id,
            })

        if len(results) < batch:
            break
        offset += batch
        time.sleep(4)

    return all_results


def _fetch_case_metadata(db_id: str, case_id: str) -> dict:
    """Fetch metadata for a single case — gets URL and decision date."""
    try:
        resp = requests.get(
            f"{_API_BASE}/caseBrowse/en/{db_id}/{case_id}/",
            params={"api_key": CANLII_API_KEY},
            timeout=15,
        )
        if resp.status_code == 429:
            time.sleep(30)
            resp = requests.get(
                f"{_API_BASE}/caseBrowse/en/{db_id}/{case_id}/",
                params={"api_key": CANLII_API_KEY},
                timeout=15,
            )
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return {}


def _url_from_citation(citation: str) -> str:
    """
    Construct a CanLII URL from a citation as fallback when metadata 404s.
    e.g. "2025 ONSC 1234 (CanLII)" → https://www.canlii.org/en/on/onsc/doc/2025/2025onsc1234/2025onsc1234.html

    Returns empty string if citation format is unrecognized.
    """
    # Province mapping for court codes
    _PROV_MAP = {
        "ab": "ab", "bc": "bc", "mb": "mb", "nb": "nb", "nl": "nl",
        "ns": "ns", "on": "on", "pe": "pe", "qc": "qc", "sk": "sk",
        "nt": "nt", "nu": "nu", "yk": "yk",
        # Federal courts
        "fc": "ca", "fca": "ca", "scc": "ca", "tcc": "ca",
    }

    # Parse "2025 ONSC 1234 (CanLII)" → year=2025, court=ONSC, num=1234
    m = re.match(r"(\d{4})\s+(\w+)\s+(\d+)", citation.strip())
    if not m:
        return ""

    year = m.group(1)
    court = m.group(2).lower()
    num = m.group(3)

    # Derive province from court code
    prov = None
    for prefix, p in _PROV_MAP.items():
        if court.startswith(prefix):
            prov = p
            break
    if not prov:
        return ""

    case_slug = f"{year}{court}{num}"
    return f"https://www.canlii.org/en/{prov}/{court}/doc/{year}/{case_slug}/{case_slug}.html"


def main():
    if not CANLII_API_KEY or not CANLII_API_KEY.strip():
        print("  ERROR: Set CANLII_API_KEY in config.py first.")
        return

    # Include all of 2025 and 2026
    cutoff_year = (datetime.now() - timedelta(days=365)).year

    queries = [
        '"class action"',
        '"class proceeding"',
        '"recours collectif"',
        '"action collective"',
    ]

    print("=" * 70)
    print("  CanLII Class Action Search")
    print(f"  Queries: {', '.join(queries)}")
    print(f"  Year filter: {cutoff_year}+  (includes {cutoff_year} and {cutoff_year + 1})")
    print("=" * 70)
    print()

    # ── Phase 1: Search ──────────────────────────────────────────────────────
    raw_results = []
    for q in queries:
        print(f"  Searching: {q}")
        batch = _search(q)
        print(f"    → {len(batch)} results\n")
        raw_results.extend(batch)

    if not raw_results:
        print("  No results found. Check your API key and try again.")
        return

    # Deduplicate and filter by year
    seen = set()
    filtered = []
    no_year = 0
    too_old = 0

    for r in raw_results:
        key = r["citation"]
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

        filtered.append(r)

    print(f"  Total raw results:       {len(raw_results)}")
    print(f"  Duplicates removed:      {len(raw_results) - len(seen)}")
    print(f"  No year in citation:     {no_year}")
    print(f"  Older than {cutoff_year}:         {too_old}")
    print(f"  ─────────────────────────────────")
    print(f"  Cases to look up:        {len(filtered)}")

    if not filtered:
        print("  Nothing to save.\n")
        return

    # ── Phase 2: Fetch metadata (URL + date) for each case ───────────────────
    print(f"\n  Fetching URLs and dates for {len(filtered)} cases …\n")

    results_with_urls = []
    for i, r in enumerate(filtered, 1):
        title = r["title"][:65]
        db_id = r["db_id"]
        case_id = r["case_id"]

        print(f"  [{i}/{len(filtered)}] {title} …", end=" ", flush=True)

        if not db_id or not case_id:
            # No API IDs — try building URL from citation
            url = _url_from_citation(r["citation"])
            if url:
                results_with_urls.append({
                    "title":         r["title"],
                    "citation":      r["citation"],
                    "decision_date": str(r.get("decision_year", "")),
                    "url":           url,
                })
                print("OK (from citation)")
            else:
                print("skip (no IDs)")
            continue

        meta = _fetch_case_metadata(db_id, case_id)
        url = meta.get("url", "")
        decision_date = meta.get("decisionDate", "")

        # Fallback: construct URL from citation if metadata 404'd
        if not url:
            url = _url_from_citation(r["citation"])

        if not url:
            print("skip (no URL)")
            continue

        # Ensure full URL
        if not url.startswith("http"):
            url = f"https://www.canlii.org{url}"

        results_with_urls.append({
            "title":         r["title"],
            "citation":      r["citation"],
            "decision_date": decision_date or str(r.get("decision_year", "")),
            "url":           url,
        })
        print("OK")
        time.sleep(2)

    # Sort newest first
    results_with_urls.sort(key=lambda r: r.get("decision_date", ""), reverse=True)

    # Save to CSV
    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, "class_actions.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "title", "citation", "decision_date", "url",
        ])
        writer.writeheader()
        for r in results_with_urls:
            writer.writerow(r)

    print(f"\n{'=' * 70}")
    print(f"  Saved {len(results_with_urls)} cases → {out}")
    print(f"{'=' * 70}\n")

    for i, r in enumerate(results_with_urls[:30], 1):
        print(f"  {i:3d}. {r['title'][:80]}")
        print(f"       {r['citation']}  |  {r['decision_date']}")
        print()

    if len(results_with_urls) > 30:
        print(f"  … and {len(results_with_urls) - 30} more (see {out})")


if __name__ == "__main__":
    main()
