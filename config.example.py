# ─────────────────────────────────────────────────────────────────────────────
# config.example.py  —  TEMPLATE: copy this to config.py and fill in your keys
# ─────────────────────────────────────────────────────────────────────────────
#
# SETUP:
#   1. Copy this file:   copy config.example.py config.py
#   2. Open config.py in Notepad and replace every REPLACE_ME value
#   3. NEVER commit config.py to git (it is in .gitignore)
#

# ── API Keys ──────────────────────────────────────────────────────────────────
# Claude / Anthropic  →  https://console.anthropic.com
ANTHROPIC_API_KEY = "sk-ant-REPLACE_ME"

# SendGrid            →  https://app.sendgrid.com/settings/api_keys
SENDGRID_API_KEY = "SG.REPLACE_ME"

# CanLII API key — leave blank until you receive one.
CANLII_API_KEY = ""

# ── Email ─────────────────────────────────────────────────────────────────────
# Must be a sender address verified in your SendGrid account
FROM_EMAIL = "reports@yourlawfirm.com"
FROM_NAME  = "PI Law Tracker"

# Add as many recipients as you like
TO_EMAILS = [
    "lawyer@yourlawfirm.com",
    # "paralegal@yourlawfirm.com",
]

EMAIL_SUBJECT = "PI Damages Weekly Digest"

# ── AI Model ──────────────────────────────────────────────────────────────────
# claude-haiku-4-5-20251001  →  fast, very cost-effective (recommended to start)
# claude-sonnet-4-6          →  better extraction accuracy, higher cost
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# Maximum characters of case text to send to Claude.
MAX_CASE_CHARS = 80_000

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA_DIR      = "data"
CASES_CSV     = "data/cases.csv"
SEEN_IDS_FILE = "data/seen_case_ids.txt"
LOG_FILE      = "data/tracker.log"

# ── Request Throttle ──────────────────────────────────────────────────────────
# Seconds to wait between HTTP requests to CanLII. Please keep at 2+.
REQUEST_DELAY_SECONDS = 2
