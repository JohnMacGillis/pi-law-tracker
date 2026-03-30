"""
send_all_cases.py
One-off script: sends two "Month in Review" emails from cases.csv.

  1. PI Damages (MVA / Slip & Fall / LTD) — Atlantic Canada + Ontario only
  2. Class Actions — all provinces

Usage:
    python send_all_cases.py          # last 30 days (default)
    python send_all_cases.py --days 60
    python send_all_cases.py --all    # everything in the CSV
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from database import load_cases_since
from email_report import (
    _send_digest, _CLASS_ACTION_TYPES,
    _MVA_TYPES, _NATIONAL_TYPES, _REGIONAL_PROVINCES,
)


def main():
    parser = argparse.ArgumentParser(description="Send Month in Review emails")
    parser.add_argument("--days", type=int, default=30,
                        help="Look-back window in days (default: 30)")
    parser.add_argument("--all", action="store_true",
                        help="Send ALL cached cases regardless of date")
    args = parser.parse_args()

    if args.all:
        since = "2000-01-01"
        period_start = None
    else:
        period_start = datetime.now() - timedelta(days=args.days)
        since = period_start.strftime("%Y-%m-%d")

    all_cases = load_cases_since(since)
    print(f"Loaded {len(all_cases)} cases since {since}")

    # Split — MVA limited to Atlantic + ON; LTD/Occupiers/Other PI are national
    ca_cases = [c for c in all_cases
                if c.get("case_type", "") in _CLASS_ACTION_TYPES]
    pi_cases = []
    for c in all_cases:
        ct = c.get("case_type", "")
        prov = c.get("province", "")
        if ct in _MVA_TYPES and prov in _REGIONAL_PROVINCES:
            pi_cases.append(c)
        elif ct in _NATIONAL_TYPES:
            pi_cases.append(c)

    print(f"  PI cases (MVA: Atlantic+ON; LTD/Occ.Liab: national): {len(pi_cases)}")
    print(f"  Class Action cases: {len(ca_cases)}")

    if not pi_cases and not ca_cases:
        print("Nothing to send.")
        sys.exit(0)

    # Date range for the header
    dates = [c.get("date_fetched", "") for c in all_cases if c.get("date_fetched")]
    if period_start:
        range_start = period_start
    elif dates:
        range_start = datetime.strptime(min(dates), "%Y-%m-%d")
    else:
        range_start = datetime.now()
    range_end = datetime.now()

    ok1 = _send_digest(
        pi_cases, range_start, range_end,
        heading="PI Damages — Month in Review",
        subject_prefix="PI Law Tracker — PI Month in Review",
        header_color="#0f172a",
    )
    print(f"  PI email: {'sent' if ok1 else 'FAILED'}")

    ok2 = _send_digest(
        ca_cases, range_start, range_end,
        heading="Class Actions — Month in Review",
        subject_prefix="PI Law Tracker — Class Actions Month in Review",
        header_color="#312e81",
    )
    print(f"  Class Actions email: {'sent' if ok2 else 'FAILED'}")


if __name__ == "__main__":
    main()
