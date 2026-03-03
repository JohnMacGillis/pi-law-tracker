"""
test_setup.py
Run this once after filling in config.py to verify everything is wired up
before the first live run.

Checks:
  1. Anthropic API key is valid
  2. SendGrid API key is valid
  3. All CanLII RSS feeds are reachable
  4. A single sample case can be fetched and analysed
"""

import sys
import time
import requests
import feedparser
import anthropic
from sendgrid import SendGridAPIClient

# ── Colour helpers for terminal output ───────────────────────────────────────
OK   = "  [OK]  "
FAIL = "  [FAIL]"
INFO = "  [INFO]"


def check(label: str, passed: bool, detail: str = "") -> bool:
    sym = OK if passed else FAIL
    print(f"{sym} {label}" + (f"  —  {detail}" if detail else ""))
    return passed


def section(title: str):
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


# ── 1. Config sanity ─────────────────────────────────────────────────────────
section("1. Configuration")

try:
    from config import (
        ANTHROPIC_API_KEY, SENDGRID_API_KEY,
        FROM_EMAIL, TO_EMAILS, CLAUDE_MODEL,
        REQUEST_DELAY_SECONDS,
    )
    check("config.py imports cleanly", True)
    check("Anthropic key looks set",
          ANTHROPIC_API_KEY and not ANTHROPIC_API_KEY.startswith("sk-ant-REPLACE"),
          ANTHROPIC_API_KEY[:12] + "…" if ANTHROPIC_API_KEY else "(empty)")
    check("SendGrid key looks set",
          SENDGRID_API_KEY and not SENDGRID_API_KEY.startswith("SG.REPLACE"),
          SENDGRID_API_KEY[:8] + "…" if SENDGRID_API_KEY else "(empty)")
    check("FROM_EMAIL set",   "@" in FROM_EMAIL, FROM_EMAIL)
    check("TO_EMAILS set",    len(TO_EMAILS) > 0, str(TO_EMAILS))
except Exception as exc:
    print(f"{FAIL} Could not import config.py: {exc}")
    sys.exit(1)


# ── 2. Anthropic API ──────────────────────────────────────────────────────────
section("2. Anthropic / Claude API")

try:
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=10,
        messages=[{"role": "user", "content": "Say OK"}],
    )
    check("API call succeeded", True, f"model={CLAUDE_MODEL}")
except anthropic.AuthenticationError:
    check("API call succeeded", False, "Invalid API key")
except Exception as exc:
    check("API call succeeded", False, str(exc))


# ── 3. SendGrid API ───────────────────────────────────────────────────────────
section("3. SendGrid API")

try:
    sg   = SendGridAPIClient(SENDGRID_API_KEY)
    resp = sg.client.user.profile.get()
    check("API connection succeeded", resp.status_code == 200,
          f"status={resp.status_code}")
except Exception as exc:
    # A 401 means bad key; any connection means key may still be fine
    msg = str(exc)
    ok  = "401" not in msg and "403" not in msg
    check("API connection succeeded", ok, msg[:80])


# ── 4. CanLII RSS feeds ───────────────────────────────────────────────────────
section("4. CanLII RSS Feeds")

from courts import COURTS

all_ok    = True
sample_url = None   # grab one case URL for the fetch test

for court in COURTS:
    time.sleep(0.5)
    try:
        feed = feedparser.parse(court["rss"])
        ok   = not feed.bozo and len(feed.entries) > 0
        if ok and sample_url is None and feed.entries:
            sample_url = feed.entries[0].get("link")
        check(
            court["name"],
            ok,
            f"{len(feed.entries)} entries" if ok else str(feed.bozo_exception),
        )
        if not ok:
            all_ok = False
    except Exception as exc:
        check(court["name"], False, str(exc))
        all_ok = False

if not all_ok:
    print(f"\n{INFO} Some feeds failed. Check courts.py — a db_id may have changed.")


# ── 5. Case fetch + analysis ──────────────────────────────────────────────────
section("5. Case Fetch + Claude Analysis (smoke test)")

if not sample_url:
    print(f"{INFO} Skipping — no sample URL found from RSS feeds.")
else:
    print(f"{INFO} Sample URL: {sample_url}")
    from case_fetcher import fetch_case_text, smart_truncate
    from case_analyzer import analyze_case
    from config import MAX_CASE_CHARS

    text = fetch_case_text(sample_url)
    check("Case text fetched", bool(text),
          f"{len(text)} chars" if text else "fetch returned None")

    if text:
        truncated = smart_truncate(text, MAX_CASE_CHARS)
        result    = analyze_case(truncated, "Test case", province="ON")
        check("Claude analysis returned", result is not None)
        if result:
            check("Result has is_relevant key", "is_relevant" in result)
            check("Result has damages key",     "damages"     in result)
            print(f"{INFO} is_relevant={result.get('is_relevant')}  "
                  f"case_type={result.get('case_type')}")


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'=' * 55}")
print("  Setup test complete.")
print("  If all checks show [OK], run:  python daily_run.py")
print(f"{'=' * 55}\n")
