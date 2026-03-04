# PI Law Tracker — System Schematic

## What It Does

Automatically monitors Canadian court databases (CanLII) daily for new personal
injury (PI), long-term disability (LTD), and class action decisions across
5 provinces. Uses AI to analyze each case and extract damages breakdowns.
Sends a styled HTML email digest to the team every Monday.

---

## System Overview

```
  WINDOWS TASK SCHEDULER
  ├── Daily 06:00 AM ──► daily_run.py     (discover + fetch + analyze)
  └── Monday 08:00 AM ─► weekly_report.py (compile + email digest)
```

---

## Daily Pipeline (daily_run.py)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         DISCOVERY LAYER                                │
│                                                                        │
│   ┌──────────────────────┐     ┌──────────────────────────────────┐    │
│   │  CanLII REST API     │     │  RSS Feeds (11 courts)           │    │
│   │  (api_collector.py)  │     │  (rss_collector.py)              │    │
│   │                      │     │                                  │    │
│   │  • Structured JSON   │     │  • One feed per court            │    │
│   │  • No bot protection │     │  • Includes case summaries       │    │
│   │  • Requires API key  │     │  • Always available              │    │
│   └──────────┬───────────┘     │  • Feed health report logged     │    │
│              │                 └────────────────┬─────────────────┘    │
│              │    PREFERRED ◄──── OR ────►      │    FALLBACK          │
│              └──────────────┬──────────────────-┘                      │
│                             ▼                                          │
│                   List of new case URLs                                │
│                   + titles + dates                                     │
│                   (deduplicated against                                │
│                    seen_case_ids.txt)                                  │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  PHASE 0 — INSTANT PRE-FILTER                          │
│                  (case_prefilter.py — zero cost, no network)           │
│                                                                        │
│   For each case:                                                       │
│                                                                        │
│   1. TITLE EXCLUSION CHECK                                             │
│      Reject immediately if title contains:                             │
│      "R. v." (criminal), "divorce", "child custody",                   │
│      "tax court", "immigration appeal", "habeas corpus", etc.          │
│                                                                        │
│   2. RSS SUMMARY KEYWORD CHECK (RSS discovery only)                    │
│      Scan the summary text already in the RSS entry:                   │
│      • EXCLUSION keywords → hard reject                                │
│        (criminal code, divorce act, income tax act, patent, etc.)      │
│      • HIGH-CONFIDENCE keywords → instant pass                         │
│        (non-pecuniary, slip and fall, whiplash, LTD benefits, etc.)    │
│      • SUPPORTING keywords → pass if 1+ found                         │
│        (general damages, tort, chronic pain, disability, etc.)         │
│                                                                        │
│   Result: ~75-85% of cases eliminated with zero API cost               │
│                                                                        │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │ Only candidates
                            ▼ that passed
┌─────────────────────────────────────────────────────────────────────────┐
│                  PHASE 1 — CASE TEXT FETCHING                          │
│                  (case_fetcher.py — Playwright browser)                │
│                                                                        │
│   Browser Setup:                                                       │
│   • Uses real Google Chrome (not Chromium) via Playwright              │
│   • Window visible (not headless) so user can solve CAPTCHA if needed  │
│   • Session cookies persist in data/browser_state.json between runs    │
│   • webdriver flag removed to avoid bot detection                      │
│                                                                        │
│   Anti-Detection (per fetch):                                          │
│   ┌──────────────────────────────────────────────────────────────┐     │
│   │  DEVICE PROFILE ROTATION                                     │     │
│   │  Every single page load gets a DIFFERENT fingerprint:        │     │
│   │  • 8 user agent strings (Chrome 120-128, Windows + macOS)    │     │
│   │  • Shuffled cycle — all 8 used before any repeats            │     │
│   │  • Viewport jitter: base size ± random offset each time      │     │
│   │    (±80px width, ±50px height)                               │     │
│   │  • Human-like delays between requests:                       │     │
│   │    70% chance: 2-9 seconds                                   │     │
│   │    20% chance: 15-35 seconds                                 │     │
│   │    10% chance: 40-90 seconds                                 │     │
│   └──────────────────────────────────────────────────────────────┘     │
│                                                                        │
│   For each case URL:                                                   │
│   1. Convert URL to .html version (not PDF)                            │
│   2. Navigate with Playwright, wait for page to render                 │
│   3. Extract decision text from known selectors:                       │
│      #cas-content, .cas-content, #document-content, article, main      │
│   4. If 403 → wait 60-120s, retry once                                │
│   5. If 3+ consecutive 403s → rebuild browser session + retry all      │
│   6. Failed cases un-marked in seen_ids for tomorrow's retry           │
│                                                                        │
│   Output: Raw case text (up to 80,000 chars, smart-truncated)          │
│                                                                        │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │ Cases with
                            ▼ fetched text
┌─────────────────────────────────────────────────────────────────────────┐
│                  PHASE 2 — AI ANALYSIS                                 │
│                  (case_analyzer.py)                                     │
│                                                                        │
│   Step A: Full-Text Pre-Filter (zero cost)                             │
│   Same keyword engine as Phase 0, but on the FULL decision text.       │
│   Cases that passed title-only checks may still fail here              │
│   (e.g., a contract case that happened to mention "the accident").     │
│                                                                        │
│   Step B: AI Case Analysis                                             │
│   ┌──────────────────────────────────────────────────────────────┐     │
│   │                                                              │     │
│   │         ┌──────────────────┐                                 │     │
│   │         │  Anthropic API   │                                 │     │
│   │         │  (Claude)        │                                 │     │
│   │         │                  │                                 │     │
│   │         │  Primary model:  │                                 │     │
│   │         │  claude-haiku    │                                 │     │
│   │         └────────┬─────────┘                                 │     │
│   │                  │                                           │     │
│   │           Success? ──── YES ──► Use result                   │     │
│   │                  │                                           │     │
│   │                  NO (any failure: 429, 529, network, etc.)   │     │
│   │                  │                                           │     │
│   │                  ▼ IMMEDIATE SWITCH                          │     │
│   │         ┌──────────────────┐                                 │     │
│   │         │  OpenAI API      │                                 │     │
│   │         │  (GPT-4o-mini)   │                                 │     │
│   │         │                  │                                 │     │
│   │         │  Same prompt     │                                 │     │
│   │         │  Same JSON spec  │                                 │     │
│   │         │  Zero wait time  │                                 │     │
│   │         └────────┬─────────┘                                 │     │
│   │                  │                                           │     │
│   │           Success? ──── YES ──► Use result                   │     │
│   │                  │                                           │     │
│   │                  NO ──► Case skipped (logged as error)       │     │
│   │                                                              │     │
│   └──────────────────────────────────────────────────────────────┘     │
│                                                                        │
│   AI Prompt — asks the model to return JSON:                           │
│   {                                                                    │
│     "is_relevant": true/false,                                         │
│     "case_type": "MVA" | "Slip and Fall" | "LTD" | "Class Action" |.. │
│     "summary": "2-3 sentence plain-language summary",                  │
│     "damages": {                                                       │
│       "non_pecuniary":       "$75,000",                                │
│       "general_damages":     null,                                     │
│       "past_income_loss":    "$120,000",                               │
│       "future_income_loss":  "$450,000",                               │
│       "cost_of_future_care": "$200,000",                               │
│       "special_damages":     "$35,000",                                │
│       "aggravated_punitive": null,                                     │
│       "total":               "$880,000"                                │
│     },                                                                 │
│     "notes": "30% contributory negligence applied"                     │
│   }                                                                    │
│                                                                        │
│   If is_relevant = true → saved to data/cases.csv                      │
│   If is_relevant = false → skipped                                     │
│                                                                        │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Weekly Email (weekly_report.py → email_report.py)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                  MONDAY MORNING EMAIL DIGEST                           │
│                                                                        │
│   1. Load all cases from data/cases.csv saved in the last 7 days       │
│   2. Group by province (NS, NB, PE, NL, ON)                           │
│   3. Sort each group newest-first                                      │
│   4. Build styled HTML email:                                          │
│                                                                        │
│   ┌────────────────────────────────────────────────────┐               │
│   │  ┌──────────────────────────────────────────────┐  │               │
│   │  │  PI · LTD · Class Action Report             │  │               │
│   │  │  Feb 24 – Mar 03, 2026 · 12 new cases       │  │ Dark header  │
│   │  └──────────────────────────────────────────────┘  │               │
│   │                                                    │               │
│   │  ── Nova Scotia ── 4 cases ──────────────────────  │               │
│   │  ┌──────────────────────────────────────────────┐  │               │
│   │  │ Smith v. Jones                    [MVA]      │  │ Case card    │
│   │  │ NS Supreme Court · 2026-02-28               │  │               │
│   │  │                                              │  │               │
│   │  │ Plaintiff sustained whiplash and chronic     │  │ AI summary   │
│   │  │ pain in a rear-end collision...              │  │               │
│   │  │                                              │  │               │
│   │  │ Non-Pecuniary        $85,000                 │  │               │
│   │  │ Past Income Loss     $120,000                │  │ Damages      │
│   │  │ Future Care          $45,000                 │  │ table        │
│   │  │ ─────────────────────────────                │  │               │
│   │  │ Total                $250,000                │  │               │
│   │  │                                              │  │               │
│   │  │ 20% contributory negligence applied          │  │ Notes        │
│   │  └──────────────────────────────────────────────┘  │               │
│   │                                                    │               │
│   │  ── Ontario ── 8 cases ──────────────────────────  │               │
│   │  [more case cards...]                              │               │
│   │                                                    │               │
│   │  ┌──────────────────────────────────────────────┐  │               │
│   │  │  AI-generated summaries are for research     │  │ Disclaimer   │
│   │  │  purposes only and must be verified.         │  │               │
│   │  └──────────────────────────────────────────────┘  │               │
│   └────────────────────────────────────────────────────┘               │
│                                                                        │
│   5. Send via SendGrid to configured recipient list                    │
│                                                                        │
│   Case type badges (colour-coded):                                     │
│   [MVA] blue  [Slip and Fall] orange  [LTD] green  [Class Action] purple│
│                                                                        │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Courts Monitored (11 total)

```
Province                    Courts
────────────────────────    ────────────────────────────────────────
Nova Scotia (NS)            Supreme Court, Court of Appeal
New Brunswick (NB)          Court of King's Bench, Court of Appeal
Prince Edward Island (PE)   Supreme Court – Trial Div, Appeal Div
Newfoundland & Lab (NL)     Supreme Court, Court of Appeal
Ontario (ON)                Superior Court, Court of Appeal, Divisional Court
```

---

## Data Storage

```
data/
├── cases.csv               All confirmed PI/LTD/class action cases
│                            (18 columns: date, title, court, province,
│                             case_type, URL, summary, 7 damages fields,
│                             total, notes, case_id)
│
├── seen_case_ids.txt        One CanLII URL per line — deduplication list
│                            (prevents reprocessing the same case)
│
├── browser_state.json       Playwright session cookies + localStorage
│                            (DataDome session persists between daily runs)
│
└── tracker.log              Full run history with timestamps
                             (RSS health report, fetch results, AI analysis,
                              errors, fallback switches)
```

---

## Configuration (config.py)

```
Setting                     What it controls
────────────────────────    ────────────────────────────────────────
ANTHROPIC_API_KEY           Primary AI — Claude (Anthropic)
OPENAI_API_KEY              Fallback AI — GPT-4o-mini (OpenAI)
OPENAI_MODEL                Which OpenAI model to use for fallback
CLAUDE_MODEL                Which Claude model (haiku or sonnet)
SENDGRID_API_KEY            Email delivery service
CANLII_API_KEY              Optional — enables API discovery over RSS
FROM_EMAIL / TO_EMAILS      Email sender + recipient list
CLAUDE_DELAY_SECONDS        Pause between AI calls (15s default)
REQUEST_DELAY_SECONDS       Pause between CanLII page fetches (2s base)
MAX_CASE_CHARS              Max text sent to AI (80,000 chars)
```

---

## Error Handling Summary

```
Error                       What happens
────────────────────────    ────────────────────────────────────────
CanLII 403 (bot blocked)    Wait 60-120s → retry once
                            If 3+ consecutive → rebuild browser session
                            → retry all failed cases
                            → un-mark still-failed for tomorrow

CanLII 429 (rate limit)     Read Retry-After header → wait → retry

Anthropic API failure       Immediately switch to OpenAI GPT-4o-mini
(429, 529, network, any)    No waiting, no retries — instant fallback

OpenAI also fails           Case skipped, logged as error

RSS feed empty/broken       Logged in health report with status:
                            OK / STALE (14+ days) / EMPTY / ERROR

CAPTCHA appears             Browser window visible — user solves it
                            Script waits up to 2 minutes silently
```

---

## File Map

```
pi-law-tracker/
│
├── daily_run.py            Main daily orchestrator — runs the 3-phase pipeline
├── weekly_report.py        Monday email trigger
│
├── config.py               All settings (API keys, email, model, paths)
├── courts.py               11 monitored courts with RSS feed URLs
│
├── api_collector.py        CanLII REST API discovery (preferred)
├── rss_collector.py        RSS feed discovery (fallback) + health report
│
├── case_fetcher.py         Playwright browser fetching + anti-detection
├── case_prefilter.py       Keyword-based filtering (zero API cost)
├── case_analyzer.py        Claude AI analysis + OpenAI fallback
│
├── database.py             CSV read/write + seen_ids deduplication
├── email_report.py         HTML email builder + SendGrid sender
│
├── requirements.txt        Python dependencies
└── data/                   Runtime data (cases, logs, browser state)
```
