# PI Law Tracker

Monitors CanLII daily for new personal injury damages decisions across Nova Scotia,
New Brunswick, Prince Edward Island, Newfoundland & Labrador, and Ontario.
Sends a formatted HTML email digest every Monday morning.

---

## What it does

| Step | What happens |
|------|-------------|
| **Daily (06:00)** | Polls CanLII RSS feeds for all monitored courts |
| | Fetches the full text of each new case |
| | Sends the text to Claude (AI) to determine if it is a PI damages case |
| | If yes — extracts case type, summary, and damages by category |
| | Saves the result to `data/cases.csv` |
| **Weekly (Mon 08:00)** | Compiles all cases from the past 7 days |
| | Sends a formatted HTML digest email via SendGrid |

---

## Monitored courts

- Nova Scotia Supreme Court + Court of Appeal
- Court of King's Bench of NB + NB Court of Appeal
- PEI Supreme Court (Trial + Appeal)
- Supreme Court of NL + NL Court of Appeal
- Ontario Superior Court of Justice, Court of Appeal, Divisional Court

---

## Setup — Step by Step

### 1. Install Python 3.11 or later

Download from **https://www.python.org/downloads/**
During installation, check **"Add Python to PATH"**.

Verify in Command Prompt:
```
python --version
```

---

### 2. Copy the project folder to the Windows computer

Copy the entire `pi-law-tracker` folder to somewhere permanent, e.g.:
```
C:\Users\YourName\pi-law-tracker\
```

---

### 3. Install dependencies

Open Command Prompt, navigate to the folder, and run:
```
cd C:\Users\YourName\pi-law-tracker
python -m pip install -r requirements.txt
```

---

### 4. Get your API keys

**Anthropic (Claude)**
1. Go to https://console.anthropic.com
2. Sign up / log in → API Keys → Create Key
3. Copy the key (starts with `sk-ant-…`)

**SendGrid**
1. Go to https://sendgrid.com → sign up for a free account
2. Settings → API Keys → Create API Key → Full Access
3. Copy the key (starts with `SG.…`)
4. Go to Settings → Sender Authentication → verify your `FROM_EMAIL` address

---

### 5. Edit config.py

Open `config.py` in Notepad (or any text editor) and fill in:

```python
ANTHROPIC_API_KEY = "sk-ant-..."     # your Anthropic key
SENDGRID_API_KEY  = "SG...."         # your SendGrid key

FROM_EMAIL = "reports@yourlawfirm.com"   # must be verified in SendGrid
TO_EMAILS  = ["you@yourlawfirm.com"]     # who receives the digest
```

Everything else can stay as-is to start.

---

### 6. Test your setup

```
python test_setup.py
```

All items should show `[OK]`. Fix any `[FAIL]` items before continuing.

---

### 7. Run manually for the first time

```
python daily_run.py
```

This will process all cases currently in the RSS feeds (could be 50–200 cases
the very first run). Watch the output — it shows each case as it is processed.

To send a test email immediately:
```
python weekly_report.py
```

---

### 8. Schedule automatic runs (Windows Task Scheduler)

Right-click Command Prompt → **Run as Administrator**, then:

```
cd C:\Users\YourName\pi-law-tracker
python setup_scheduler.py
```

This registers two tasks:
- `PI_Law_Daily`  — 06:00 every day
- `PI_Law_Weekly` — 08:00 every Monday

To verify: open **Task Scheduler** (search in Start menu) and look for both tasks
under **Task Scheduler Library**.

---

## Files

```
pi-law-tracker/
├── config.py            ← EDIT THIS — all your keys and settings
├── courts.py            ← list of courts and RSS feed URLs
├── rss_collector.py     ← fetches RSS feeds
├── case_fetcher.py      ← downloads case text from CanLII
├── case_analyzer.py     ← sends text to Claude for analysis
├── database.py          ← reads/writes cases.csv
├── email_report.py      ← builds and sends the HTML email
├── daily_run.py         ← daily job (run by Task Scheduler)
├── weekly_report.py     ← weekly email job (run by Task Scheduler)
├── setup_scheduler.py   ← registers Task Scheduler tasks (run once)
├── test_setup.py        ← verifies everything works (run once)
├── requirements.txt     ← Python dependencies
└── data/
    ├── cases.csv        ← your PI damages database (auto-created)
    ├── seen_case_ids.txt← tracks processed cases (auto-created)
    └── tracker.log      ← daily run logs (auto-created)
```

---

## The CSV database

`data/cases.csv` has these columns:

| Column | Description |
|--------|-------------|
| `date_fetched` | Date the tracker found the case |
| `decision_date` | Date of the court decision |
| `title` | Full case name (e.g. Smith v Jones, 2024 NSSC 42) |
| `jurisdiction` | Court name |
| `province` | NS / NB / PE / NL / ON |
| `case_type` | MVA / Slip and Fall / Trip and Fall / Other PI |
| `canlii_url` | Direct link to the case on CanLII |
| `summary` | AI-generated 2–3 sentence summary |
| `non_pecuniary` | Non-pecuniary general damages |
| `general_damages` | General damages (when not itemised separately) |
| `past_income_loss` | Past income loss |
| `future_income_loss` | Future income loss / loss of earning capacity |
| `cost_of_future_care` | Cost of future care |
| `special_damages` | Special damages / out-of-pocket expenses |
| `aggravated_punitive` | Aggravated or punitive damages |
| `total_damages` | Total damages awarded |
| `notes` | AI-noted caveats (liability splits, reductions, appeals) |
| `case_id` | CanLII case URL used as unique identifier |

---

## When you receive your CanLII API key

1. Open `config.py` and paste the key:
   ```python
   CANLII_API_KEY = "your-key-here"
   ```
2. The system will automatically use the API in addition to RSS feeds,
   giving you more reliable case discovery.

---

## Troubleshooting

**A court RSS feed shows [FAIL] in test_setup.py**
> CanLII may have changed the database ID. Go to https://www.canlii.org/en/
> navigate to the province and court, and check the URL. Update `courts.py`
> with the correct `db_id` and `rss` URL.

**Cases not appearing in the CSV**
> Check `data/tracker.log` for details. The most common reason is that the
> cases in that court were not PI damages decisions (correct behaviour).

**Email not arriving**
> Check SendGrid dashboard → Activity → look for the send event and any
> bounce/block messages. Make sure FROM_EMAIL is verified as a sender.

**"Rate limit" errors from Claude**
> Reduce the number of courts in `courts.py` temporarily, or increase
> `REQUEST_DELAY_SECONDS` in `config.py` to slow down processing.

---

## Legal / Usage note

This tool uses CanLII's public RSS feeds for case discovery — the same
mechanism CanLII provides for syndication. Individual case pages are
fetched in the same way a lawyer would manually research a case.
The firm should apply for a CanLII API key at https://api.canlii.org/
to ensure continued compliant access.

AI-generated summaries are research aids only and must be verified
against the original decision before any professional reliance.
