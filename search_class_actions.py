"""
search_class_actions.py
Search CanLII for class action cases from the last year.

Phase 1 — Fetch the database list once to build db_id → jurisdiction mapping.
Phase 2 — Full-text search for class action keywords (English + French).
Phase 3 — Construct URLs from databaseId + caseId (no per-case metadata needed).

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
_MAX_PER_QUERY = 3_000


def _year_from_citation(citation: str) -> int | None:
    """Extract decision year from citation: '2025 ONSC 1234' → 2025"""
    m = re.match(r"(\d{4})\s+\w+", citation.strip())
    if m:
        return int(m.group(1))
    m = re.search(r"\[(\d{4})\]", citation)
    if m:
        return int(m.group(1))
    return None


def _fetch_db_map() -> dict:
    """
    Fetch ALL databases from the caseBrowse list endpoint.
    Returns a dict mapping databaseId → jurisdiction path.
    e.g. {"onsc": "on", "bcca": "bc", "fct": "ca", "csc-scc": "ca"}
    """
    print("  Loading database → jurisdiction map …", end=" ", flush=True)
    resp = requests.get(
        f"{_API_BASE}/caseBrowse/en/",
        params={"api_key": CANLII_API_KEY},
        timeout=30,
    )
    if not resp.ok:
        print(f"FAILED (HTTP {resp.status_code})")
        return {}

    db_map = {}
    for db in resp.json().get("caseDatabases", []):
        db_id = db.get("databaseId", "")
        jur = db.get("jurisdiction", "")
        # jurisdiction can be a string like "on" or a dict like {"jurisdiction": "on"}
        if isinstance(jur, dict):
            jur = jur.get("jurisdiction", "")
        if db_id and jur:
            db_map[db_id] = jur

    print(f"OK ({len(db_map)} databases)")
    return db_map


def _build_url(db_id: str, case_id: str, db_map: dict) -> str:
    """
    Construct a CanLII URL from databaseId and caseId.
    e.g. db_id="onsc", case_id="2025onsc1234"
    → https://www.canlii.org/en/on/onsc/doc/2025/2025onsc1234/2025onsc1234.html
    """
    jur = db_map.get(db_id, "")
    if not jur or not case_id:
        return ""

    # Extract year from case_id (first 4 chars are usually the year)
    year_match = re.match(r"(\d{4})", case_id)
    year = year_match.group(1) if year_match else ""
    if not year:
        return ""

    return (
        f"https://www.canlii.org/en/{jur}/{db_id}/doc/"
        f"{year}/{case_id}/{case_id}.html"
    )


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

            db_obj = case.get("databaseId", {})
            db_id = db_obj if isinstance(db_obj, str) else db_obj.get("databaseId", "")

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


def main():
    if not CANLII_API_KEY or not CANLII_API_KEY.strip():
        print("  ERROR: Set CANLII_API_KEY in config.py first.")
        return

    # Use cutoff_year - 1 to catch cases at the boundary (e.g. a case
    # decided in Dec 2024 whose citation reads "2024 ONSC …" but whose
    # publication date is Jan 2025).  The analyzer will do the final
    # precise date filtering.
    cutoff_year = (datetime.now() - timedelta(days=365)).year - 1

    queries = [
        '"class action"',
        '"class proceeding"',
        '"class proceedings"',
        '"recours collectif"',
        '"action collective"',
    ]

    print("=" * 70)
    print("  CanLII Class Action Search")
    print(f"  Queries: {', '.join(queries)}")
    print(f"  Year filter: {cutoff_year}+")
    print("=" * 70)
    print()

    # Phase 1: Get database → jurisdiction mapping (single API call)
    db_map = _fetch_db_map()
    if not db_map:
        print("  ERROR: Could not load database list. Check API key.")
        return
    print()

    # Phase 2: Search
    raw_results = []
    for q in queries:
        print(f"  Searching: {q}")
        batch = _search(q)
        print(f"    → {len(batch)} results\n")
        raw_results.extend(batch)

    if not raw_results:
        print("  No results found.")
        return

    # Deduplicate, filter by year, build URLs
    seen = set()
    results = []
    no_year = 0
    too_old = 0
    no_url = 0

    for r in raw_results:
        key = r["citation"]
        if not key or key in seen:
            continue
        seen.add(key)

        year = r.get("decision_year")

        # Include cases with unknown year (citation unparseable) — let the
        # analyzer's AI decide.  Only exclude cases clearly older than cutoff.
        if year is not None and year < cutoff_year:
            too_old += 1
            continue
        if year is None:
            no_year += 1
            # Don't skip — include them with year-only date from case_id if possible
            cid = r.get("case_id", "")
            m = re.match(r"(\d{4})", cid)
            if m:
                fallback_year = int(m.group(1))
                if fallback_year < cutoff_year:
                    too_old += 1
                    continue
                year = fallback_year
            # If still no year, include anyway — better to over-include

        # Build URL from db_id + case_id + jurisdiction map
        url = _build_url(r["db_id"], r["case_id"], db_map)
        if not url:
            no_url += 1
            continue

        results.append({
            "title":         r["title"],
            "citation":      r["citation"],
            "decision_date": str(year) if year else "",
            "url":           url,
        })

    print(f"  Total raw results:       {len(raw_results)}")
    print(f"  Duplicates removed:      {len(raw_results) - len(seen)}")
    print(f"  No year in citation:     {no_year}")
    print(f"  Older than {cutoff_year}:         {too_old}")
    print(f"  Could not build URL:     {no_url}")
    print(f"  ─────────────────────────────────")
    print(f"  Cases saved:             {len(results)}")

    if not results:
        print("  Nothing to save.\n")
        return

    # Sort newest first
    results.sort(key=lambda r: r.get("decision_date", ""), reverse=True)

    # Save to CSV
    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, "class_actions.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "title", "citation", "decision_date", "url",
        ])
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    print(f"\n{'=' * 70}")
    print(f"  Saved {len(results)} cases → {out}")
    print(f"{'=' * 70}\n")

    for i, r in enumerate(results[:30], 1):
        print(f"  {i:3d}. {r['title'][:80]}")
        print(f"       {r['citation']}  |  {r['url'][:60]}")
        print()

    if len(results) > 30:
        print(f"  … and {len(results) - 30} more (see {out})")


if __name__ == "__main__":
    main()
