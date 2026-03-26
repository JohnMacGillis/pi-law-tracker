"""
send_all_cases.py
One-off script: sends two emails with ALL cases in cases.csv.

Usage:
    python send_all_cases.py
"""

from datetime import datetime, timedelta
from database import load_cases_since
from email_report import (
    build_html, _send_digest, _CLASS_ACTION_TYPES, _PI_PROVINCES,
)


def main():
    # Load everything (use a very old date)
    all_cases = load_cases_since("2000-01-01")
    print(f"Loaded {len(all_cases)} total cases from cases.csv")

    # Same split logic as send_weekly_report
    ca_cases = [c for c in all_cases
                if c.get("case_type", "") in _CLASS_ACTION_TYPES]
    pi_cases = [c for c in all_cases
                if c.get("case_type", "") not in _CLASS_ACTION_TYPES
                and c.get("province", "") in _PI_PROVINCES]

    print(f"  PI cases (Atlantic + ON): {len(pi_cases)}")
    print(f"  Class Action cases:       {len(ca_cases)}")

    # Date range spanning all data
    dates = [c.get("date_fetched", "") for c in all_cases if c.get("date_fetched")]
    if dates:
        earliest = min(dates)
        latest = max(dates)
    else:
        earliest = latest = datetime.now().strftime("%Y-%m-%d")

    week_start = datetime.strptime(earliest, "%Y-%m-%d")
    week_end = datetime.strptime(latest, "%Y-%m-%d")

    ok1 = _send_digest(
        pi_cases, week_start, week_end,
        heading="PI Damages — All Cases",
        subject_prefix="PI Law Tracker — All PI Cases",
        header_color="#0f172a",
    )
    print(f"  PI email: {'sent' if ok1 else 'FAILED'}")

    ok2 = _send_digest(
        ca_cases, week_start, week_end,
        heading="Class Actions — All Cases",
        subject_prefix="PI Law Tracker — All Class Actions",
        header_color="#312e81",
    )
    print(f"  Class Actions email: {'sent' if ok2 else 'FAILED'}")


if __name__ == "__main__":
    main()
