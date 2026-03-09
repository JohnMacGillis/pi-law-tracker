"""
search_class_actions.py
Pull all class action settlement approval cases from the last year,
nationwide, via the CanLII API.

Usage:
    python search_class_actions.py

Outputs results to data/class_action_settlements.csv
"""

import csv
import os
import time
from datetime import datetime, timedelta

import requests

from config import CANLII_API_KEY, DATA_DIR

_API_BASE = "https://api.canlii.org/v1"

# Every Superior/Supreme Court + Court of Appeal in Canada
_ALL_COURTS = [
    # Alberta
    ("ab", "abqb",   "Court of King's Bench of Alberta"),
    ("ab", "abca",   "Court of Appeal of Alberta"),
    # British Columbia
    ("bc", "bcsc",   "Supreme Court of British Columbia"),
    ("bc", "bcca",   "Court of Appeal for British Columbia"),
    # Manitoba
    ("mb", "mbkb",   "Court of King's Bench of Manitoba"),
    ("mb", "mbca",   "Court of Appeal of Manitoba"),
    # New Brunswick
    ("nb", "nbkb",   "Court of King's Bench of New Brunswick"),
    ("nb", "nbca",   "Court of Appeal of New Brunswick"),
    # Newfoundland & Labrador
    ("nl", "nlsctd", "Supreme Court of Newfoundland and Labrador"),
    ("nl", "nlca",   "Court of Appeal of Newfoundland and Labrador"),
    # Nova Scotia
    ("ns", "nssc",   "Supreme Court of Nova Scotia"),
    ("ns", "nsca",   "Nova Scotia Court of Appeal"),
    # Ontario
    ("on", "onsc",   "Superior Court of Justice"),
    ("on", "onca",   "Court of Appeal for Ontario"),
    ("on", "onscdc", "Ontario Divisional Court"),
    # PEI
    ("pe", "pesctd", "Supreme Court of PEI – Trial Division"),
    ("pe", "pescad", "PEI Court of Appeal"),
    # Quebec
    ("qc", "qccs",   "Quebec Superior Court"),
    ("qc", "qcca",   "Court of Appeal of Quebec"),
    # Saskatchewan
    ("sk", "skkb",   "Court of King's Bench for Saskatchewan"),
    ("sk", "skca",   "Court of Appeal for Saskatchewan"),
    # Federal
    ("ca", "fct",    "Federal Court"),
    ("ca", "fca",    "Federal Court of Appeal"),
    ("ca", "csc-scc", "Supreme Court of Canada"),
]

# Keywords that indicate a class action settlement approval
_SETTLEMENT_KEYWORDS = [
    "settlement approval",
    "approval of settlement",
    "approve the settlement",
    "approving settlement",
    "fair and reasonable",     # standard test for settlement approval
    "class action settlement",
    "settlement agreement",
    "approval hearing",
    "distribution protocol",
    "settlement funds",
    "cy-près",
    "cy pres",
]

# Broader class action keywords for title matching
_CLASS_ACTION_TITLE = [
    "class action",
    "class proceeding",
    "class proc",
    "certification",
    "representative plaintiff",
    "settlement",
]


def _fetch_cases(db_id: str, published_after: str) -> list[dict]:
    """Fetch ALL cases from one court published after the given date (up to API max 10,000)."""
    all_cases = []
    offset = 0
    batch = 100

    while True:
        resp = requests.get(
            f"{_API_BASE}/caseBrowse/en/{db_id}/",
            params={
                "api_key": CANLII_API_KEY,
                "offset": offset,
                "resultCount": batch,
                "publishedAfter": published_after,
            },
            timeout=30,
        )

        if resp.status_code == 404:
            return []  # unknown database
        if not resp.ok:
            print(f"    HTTP {resp.status_code} — skipping")
            return all_cases

        cases = resp.json().get("cases", [])
        all_cases.extend(cases)

        if len(cases) < batch:
            break
        offset += batch
        time.sleep(0.5)

    return all_cases


def _fetch_metadata(db_id: str, case_id: str) -> dict:
    """Fetch detailed metadata for a single case (includes keywords, date)."""
    resp = requests.get(
        f"{_API_BASE}/caseBrowse/en/{db_id}/{case_id}/",
        params={"api_key": CANLII_API_KEY},
        timeout=15,
    )
    if resp.ok:
        return resp.json()
    return {}


def _is_class_action_title(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _CLASS_ACTION_TITLE)


def _is_settlement_related(title: str, keywords: list[str] | None = None) -> bool:
    """Check title + API keywords for settlement approval indicators."""
    text = title.lower()
    if keywords:
        text += " " + " ".join(k.lower() for k in keywords)
    return any(kw in text for kw in _SETTLEMENT_KEYWORDS)


def main():
    if not CANLII_API_KEY or not CANLII_API_KEY.strip():
        print("  ERROR: Set CANLII_API_KEY in config.py first.")
        return

    one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    print("=" * 70)
    print("  Class Action Settlement Search — Last 12 Months, Nationwide")
    print(f"  Published after: {one_year_ago}")
    print("=" * 70)
    print()

    # Step 1: Pull all cases from every court, filter by title
    candidates = []

    for prov, db_id, court_name in _ALL_COURTS:
        print(f"  {prov.upper()}  {court_name} ({db_id}) …", end=" ", flush=True)
        cases = _fetch_cases(db_id, one_year_ago)
        print(f"{len(cases)} cases", end="")

        court_hits = []
        for c in cases:
            title = c.get("title", "")
            if _is_class_action_title(title):
                case_id_obj = c.get("caseId", {})
                case_id = case_id_obj.get("en", "") if isinstance(case_id_obj, dict) else str(case_id_obj)
                court_hits.append({
                    "province":   prov.upper(),
                    "court":      court_name,
                    "db_id":      db_id,
                    "case_id":    case_id,
                    "title":      title,
                    "citation":   c.get("citation", ""),
                    "url":        c.get("url", ""),
                })

        candidates.extend(court_hits)
        print(f" → {len(court_hits)} class action candidates")
        time.sleep(1)

    print(f"\n  Total class action cases found: {len(candidates)}")
    print(f"  Fetching metadata (decision dates, keywords) …\n")

    # Step 2: Fetch metadata for all candidates
    for i, c in enumerate(candidates, 1):
        print(f"  [{i}/{len(candidates)}] {c['title'][:70]}", flush=True)

        meta = _fetch_metadata(c["db_id"], c["case_id"])
        keywords = meta.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [keywords]

        c["decision_date"] = meta.get("decisionDate", "")
        c["keywords"] = "; ".join(keywords) if keywords else ""
        time.sleep(0.5)

    # Step 3: Save ALL class action cases
    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, "class_actions.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "province", "court", "title", "citation", "decision_date",
            "keywords", "url",
        ])
        writer.writeheader()
        for c in candidates:
            writer.writerow({k: c[k] for k in writer.fieldnames})

    print(f"\n{'=' * 70}")
    print(f"  Saved {len(candidates)} class action cases → {out}")
    print(f"{'=' * 70}\n")

    for c in candidates:
        print(f"  [{c['province']}] {c['title']}")
        print(f"        {c['citation']}  |  {c['decision_date']}")
        print()


if __name__ == "__main__":
    main()
