"""
test_report.py
Dry-run the weekly report: builds the HTML email and saves it locally
so you can preview it in a browser without needing SendGrid credentials.
"""

import os
import sys
from datetime import datetime, timedelta

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from database import ensure_data_dir, load_cases_since
from email_report import build_html

ensure_data_dir()

week_end   = datetime.now()
week_start = week_end - timedelta(days=7)

cases = load_cases_since(week_start.strftime("%Y-%m-%d"))
print(f"Found {len(cases)} case(s) from the last 7 days")

if not cases:
    print("No cases to report. Add cases to data/cases.csv first.")
    sys.exit(1)

for c in cases:
    print(f"  • [{c.get('case_type', '?')}] {c.get('title', 'Unknown')} — {c.get('total_damages', 'N/A')}")

html = build_html(cases, week_start, week_end)

output_path = os.path.join("data", "preview_report.html")
with open(output_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"\nEmail preview saved → {output_path}")
print("Open it in your browser to see exactly what the team will receive.")
