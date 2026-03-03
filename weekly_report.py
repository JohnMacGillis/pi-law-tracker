"""
weekly_report.py
Scheduled weekly job — run this every Monday morning via Windows Task Scheduler.

Compiles all cases collected in the past 7 days and sends the HTML digest
via SendGrid.
"""

import logging
import os
import sys
from datetime import datetime

# Ensure the script can be run from any working directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import DATA_DIR, LOG_FILE
from database import ensure_data_dir
from email_report import send_weekly_report

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("weekly_report")

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Weekly report job started: %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    ensure_data_dir()

    success = send_weekly_report()

    if success:
        logger.info("Weekly report sent successfully.")
        sys.exit(0)
    else:
        logger.error("Weekly report FAILED — check log for details.")
        sys.exit(1)
