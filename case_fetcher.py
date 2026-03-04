"""
case_fetcher.py
Fetches court decision text from CanLII using Playwright (real browser).

Why Playwright instead of curl_cffi + exported cookies?
  DataDome (CanLII's bot protection) detects the mismatch between HTTP-client
  TLS fingerprints and browser session cookies.  Playwright runs actual
  Chromium, which is indistinguishable from a real user visiting the site.
  No cookie exports, no manual CAPTCHA — the browser maintains its own session
  automatically and DataDome cookies accumulate naturally.

First run (or after rebuild_session):
  Chromium opens minimized.  If DataDome shows a CAPTCHA, solve it once in
  the browser window — the session is then saved to data/browser_state.json
  and reused on every subsequent run.
"""

import logging
import os
import random
import threading
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from config import REQUEST_DELAY_SECONDS, DATA_DIR

logger = logging.getLogger(__name__)

# ── Rate limiter (thread-safe) ────────────────────────────────────────────────
_request_lock      = threading.Lock()
_last_request_time = 0.0

# ── Persistent browser state (DataDome session survives between runs) ─────────
_STATE_FILE = os.path.join(DATA_DIR, "browser_state.json")

# ── Shared browser objects (one per process) ─────────────────────────────────
_pw      = None
_browser = None
_context = None

# ── 403 threshold (same interface as before — daily_run.py uses these) ────────
_403_THRESHOLD    = 3
_consecutive_403s = 0


# ─────────────────────────────────────────────────────────────────────────────
# Browser lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def _get_context():
    """Return the shared Playwright browser context, creating it if needed."""
    global _pw, _browser, _context

    if _context is not None:
        return _context

    os.makedirs(DATA_DIR, exist_ok=True)

    _pw = sync_playwright().start()

    # Prefer real Google Chrome (perfect fingerprint) over bundled Chromium.
    # Window is NOT minimized — user must be able to see and solve any CAPTCHA.
    try:
        _browser = _pw.chromium.launch(
            channel="chrome",
            headless=False,
        )
        logger.info("Playwright: using real Chrome")
    except Exception:
        _browser = _pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        logger.info("Playwright: using bundled Chromium")

    state = _STATE_FILE if os.path.exists(_STATE_FILE) else None
    _context = _browser.new_context(
        storage_state=state,
        viewport={"width": 1920, "height": 1080},
        locale="en-CA",
        timezone_id="America/Halifax",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )

    # Remove the webdriver flag that bot-detection looks for
    _context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    logger.info(
        "Playwright started (session: %s)",
        "loaded from disk" if state else "fresh — may need CAPTCHA on first visit",
    )
    return _context


def _save_state() -> None:
    """Persist the current browser session (cookies, localStorage) to disk."""
    if _context is not None:
        os.makedirs(DATA_DIR, exist_ok=True)
        _context.storage_state(path=_STATE_FILE)
        logger.debug("Browser state saved → %s", _STATE_FILE)


def close_browser() -> None:
    """Save session and close the browser.  Call once at the end of each run."""
    global _pw, _browser, _context
    if _context is not None:
        _save_state()
        _context.close()
        _context = None
    if _browser is not None:
        _browser.close()
        _browser = None
    if _pw is not None:
        _pw.stop()
        _pw = None
    logger.info("Browser closed and session saved.")


# ─────────────────────────────────────────────────────────────────────────────
# 403 / session-reset interface (called by daily_run.py)
# ─────────────────────────────────────────────────────────────────────────────

def needs_cookie_refresh() -> bool:
    return _consecutive_403s >= _403_THRESHOLD


def reset_403_counter() -> None:
    global _consecutive_403s
    _consecutive_403s = 0


def rebuild_session() -> None:
    """
    Close the browser and delete the saved session so the next fetch starts
    completely fresh (new DataDome handshake).  May prompt for CAPTCHA.
    """
    global _consecutive_403s
    close_browser()
    if os.path.exists(_STATE_FILE):
        os.remove(_STATE_FILE)
        logger.info("Deleted saved browser state — will start fresh.")
    _consecutive_403s = 0
    logger.info("Session will be rebuilt on the next fetch.")


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiting
# ─────────────────────────────────────────────────────────────────────────────

def _human_gap() -> float:
    """Right-skewed delay that mimics natural browsing (70/20/10 distribution)."""
    roll = random.random()
    if roll < 0.70:
        return random.uniform(REQUEST_DELAY_SECONDS, REQUEST_DELAY_SECONDS + 7)
    elif roll < 0.90:
        return random.uniform(15, 35)
    else:
        return random.uniform(40, 90)


def _pause() -> None:
    """Serialise requests — only one goes out at a time, with a human gap."""
    global _last_request_time
    with _request_lock:
        elapsed = time.time() - _last_request_time
        gap     = _human_gap()
        wait    = max(0.0, gap - elapsed)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.time()


# ─────────────────────────────────────────────────────────────────────────────
# Session warm-up
# ─────────────────────────────────────────────────────────────────────────────

def warmup() -> None:
    """
    Navigate to the CanLII home page once to establish a DataDome session.

    If a CAPTCHA appears, solve it in the browser window — the script waits
    silently (no re-navigation) for up to 2 minutes until the real CanLII
    page loads, then saves the session and continues.
    """
    ctx  = _get_context()
    page = ctx.new_page()
    try:
        logger.info("Warming up — navigating to CanLII …")
        page.goto("https://www.canlii.org/en/",
                  wait_until="domcontentloaded", timeout=30_000)

        # Wait for real CanLII content without re-navigating.
        # CAPTCHA pages are short; the real CanLII home has substantial content.
        # We check silently every few seconds — no browser interference.
        logger.info(
            "Waiting for CanLII to load cleanly "
            "(solve any CAPTCHA in the browser window) …"
        )
        try:
            page.wait_for_function(
                # Real CanLII pages have a visible search bar or nav links.
                # DataDome challenge pages are tiny by comparison.
                "() => document.body.innerText.length > 1500",
                timeout=120_000,   # wait up to 2 minutes
                polling=3_000,     # check every 3 seconds (no navigation)
            )
            logger.info("Session OK — CanLII loaded cleanly.")
        except PWTimeout:
            logger.warning(
                "Could not confirm CanLII loaded within 2 min. "
                "Proceeding anyway — delete data/browser_state.json and "
                "re-run if you continue getting 403 errors."
            )

        _save_state()

    except Exception as exc:
        logger.error("Warm-up error: %s", exc)
    finally:
        page.close()


# ─────────────────────────────────────────────────────────────────────────────
# Text extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_text(page) -> str | None:
    """
    Pull decision text from a rendered CanLII HTML page.
    Tries known content selectors first; falls back to cleaned body text.
    """
    # CanLII wraps the decision body in one of these elements
    for sel in ["#cas-content", ".cas-content", "#document-content",
                ".document-content", "article", "main"]:
        try:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text()
                if len(text) > 200:
                    return text.strip()
        except Exception:
            continue

    # Fallback: strip nav / header / footer and return body text
    try:
        text = page.evaluate("""() => {
            ['nav','header','footer','.navbar','.breadcrumb',
             '.sidebar','.toc'].forEach(s => {
                document.querySelectorAll(s).forEach(e => e.remove());
            });
            return document.body.innerText;
        }""")
        return text.strip() if len(text) > 200 else None
    except Exception:
        return None


def _to_html_url(url: str) -> str:
    """Return the HTML version of a CanLII URL (strip .pdf if present)."""
    u = url.rstrip("/")
    if u.lower().endswith(".pdf"):
        u = u[:-4]
    if not u.lower().endswith(".html"):
        u = u + ".html"
    return u


# ─────────────────────────────────────────────────────────────────────────────
# Public fetch function
# ─────────────────────────────────────────────────────────────────────────────

def fetch_case_text(url: str) -> str | None:
    """
    Navigate to the CanLII HTML decision page and return its plain text.
    Returns None on any failure.
    """
    global _consecutive_403s

    if not url:
        return None

    html_url = _to_html_url(url)
    _pause()

    ctx  = _get_context()
    page = ctx.new_page()

    try:
        resp   = page.goto(html_url, wait_until="domcontentloaded", timeout=45_000)
        status = resp.status if resp else 0

        if status == 403:
            # Single-case retry: pause 60-120 s then try once more before
            # counting this as a failure.  DataDome often clears after a
            # longer natural gap.
            retry_wait = random.randint(60, 120)
            logger.warning(
                "    403 on %s — waiting %ds then retrying once …",
                html_url, retry_wait,
            )
            time.sleep(retry_wait)
            resp2  = page.goto(html_url, wait_until="domcontentloaded", timeout=45_000)
            status = resp2.status if resp2 else 0
            if status == 403:
                _consecutive_403s += 1
                logger.warning(
                    "    403 again on %s  (consecutive: %d/%d)",
                    html_url, _consecutive_403s, _403_THRESHOLD,
                )
                return None
            # Retry succeeded — fall through to text extraction below

        if status == 429:
            retry_after = int((resp.headers or {}).get("retry-after", "60"))
            logger.warning("    429 — waiting %ds then retrying …", retry_after)
            time.sleep(retry_after)
            _pause()
            resp   = page.goto(html_url, wait_until="domcontentloaded", timeout=45_000)
            status = resp.status if resp else 0
            if status not in (200,):
                return None

        _consecutive_403s = 0

        # Brief render pause — looks more human, lets JS settle
        page.wait_for_timeout(random.randint(600, 1800))

        text = _extract_text(page)

        if text:
            logger.info("    HTML: %d chars from %s", len(text), html_url)
            _save_state()   # Persist updated cookies after a successful hit
        else:
            logger.warning("    No text extracted from %s", html_url)

        return text

    except PWTimeout:
        logger.error("    Timeout fetching %s", html_url)
        return None
    except Exception as exc:
        logger.error("    Fetch error for %s: %s", html_url, exc)
        return None
    finally:
        page.close()


# ─────────────────────────────────────────────────────────────────────────────
# Truncation helper (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def smart_truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    front = max_chars // 3
    back  = max_chars - front
    return (
        text[:front]
        + "\n\n[... middle of decision omitted for length ...]\n\n"
        + text[-back:]
    )
