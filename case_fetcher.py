"""
case_fetcher.py
Fetches court decision text from CanLII.

Hybrid approach:
  1. Playwright opens once to visit the CanLII homepage and solve any
     DataDome JS challenge / CAPTCHA. This establishes the datadome cookie.
  2. The datadome cookie is extracted and passed to curl_cffi, which does
     all the actual case fetching — fast HTTP requests with Chrome TLS
     impersonation, no browser needed per case.
  3. If the datadome cookie expires mid-run (403), Playwright reopens
     briefly to refresh it.
"""

import json
import logging
import os
import random
import threading
import time

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from config import REQUEST_DELAY_SECONDS, DATA_DIR
from court_fetcher import can_fetch_from_court, fetch_from_court

logger = logging.getLogger(__name__)

# ── Rate limiter (thread-safe) ────────────────────────────────────────────────
_request_lock      = threading.Lock()
_last_request_time = 0.0

# ── 403 tracking (daily_run.py checks these) ─────────────────────────────────
_403_THRESHOLD    = 3
_consecutive_403s = 0

# ── Session rotation ─────────────────────────────────────────────────────────
_SESSION_ROTATE_EVERY = 15
_fetches_this_session = 0

# ── Cookie file — persists DataDome cookie between runs ──────────────────────
_COOKIE_FILE = os.path.join(DATA_DIR, "datadome_cookies.json")

# ── curl_cffi session ─────────────────────────────────────────────────────────
_session = None

# ── User agents ──────────────────────────────────────────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


# ─────────────────────────────────────────────────────────────────────────────
# Cookie management — Playwright gets the cookie, curl_cffi uses it
# ─────────────────────────────────────────────────────────────────────────────

def _save_cookies(cookies: list[dict]) -> None:
    """Save cookies to disk for reuse across runs."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_COOKIE_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f)
    logger.debug("Cookies saved → %s", _COOKIE_FILE)


def _load_cookies() -> list[dict]:
    """Load cookies from disk if available."""
    if not os.path.exists(_COOKIE_FILE):
        return []
    try:
        with open(_COOKIE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _get_datadome_cookie_via_playwright() -> list[dict]:
    """
    Open Playwright briefly to visit CanLII and solve the DataDome challenge.
    Returns the browser cookies (including the datadome cookie).
    The browser window is visible so the user can solve any CAPTCHA.
    """
    logger.info("Opening browser to establish DataDome session …")
    pw = sync_playwright().start()

    try:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=False)
            logger.info("Playwright: using real Chrome")
        except Exception:
            browser = pw.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            logger.info("Playwright: using bundled Chromium")

        ctx = browser.new_context(
            locale="en-CA",
            timezone_id=random.choice(["America/Halifax", "America/Toronto", "America/Moncton"]),
            user_agent=random.choice(_USER_AGENTS),
        )

        # Hide webdriver flag
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = ctx.new_page()
        page.goto("https://www.canlii.org/en/", wait_until="domcontentloaded", timeout=30_000)

        # Wait for real CanLII content — if DataDome shows a CAPTCHA, the user
        # solves it in the visible browser window.
        logger.info(
            "Waiting for CanLII to load (solve any CAPTCHA in the browser window) …"
        )
        try:
            page.wait_for_function(
                "() => document.body.innerText.length > 1500",
                timeout=120_000,
                polling=3_000,
            )
            logger.info("CanLII loaded.")
        except PWTimeout:
            logger.warning("Timed out waiting for CanLII — proceeding with whatever cookies we have.")

        # Wait for DataDome cookie to fully settle — it can take several
        # seconds after the page appears to load before the cookie is set.
        logger.info("Waiting for DataDome cookie to settle …")
        dd_found = False
        for _attempt in range(20):   # check every 1s for up to 20s
            cookies_now = ctx.cookies()
            if any("datadome" in c.get("name", "").lower() for c in cookies_now):
                dd_found = True
                logger.info("DataDome cookie found after %ds", _attempt + 1)
                break
            page.wait_for_timeout(1000)

        if not dd_found:
            logger.warning("No DataDome cookie detected — fetching may 403")

        # Browse a bit more to look human and let any final cookies settle
        try:
            page.wait_for_timeout(random.randint(3000, 6000))
            links = page.query_selector_all("a[href*='/en/']")
            if links:
                random.choice(links[:10]).click()
                page.wait_for_timeout(random.randint(3000, 6000))

            # Visit an actual case page to confirm cookies work
            page.goto(
                "https://www.canlii.org/en/nb/nbkb/",
                wait_until="domcontentloaded", timeout=30_000,
            )
            page.wait_for_timeout(random.randint(2000, 4000))
        except Exception:
            pass

        # Extra settle time — DataDome sometimes updates cookies after navigation
        page.wait_for_timeout(3000)

        # Extract all cookies
        cookies = ctx.cookies()
        dd_cookies = [c["name"] for c in cookies if "datadome" in c["name"].lower()]
        logger.info("Got %d cookies from browser (datadome: %s)", len(cookies), dd_cookies)

        _save_cookies(cookies)

        page.close()
        ctx.close()
        browser.close()

        return cookies

    except Exception as exc:
        logger.error("Playwright cookie extraction failed: %s", exc)
        return []
    finally:
        pw.stop()


def _apply_cookies_to_session(session, cookies: list[dict]) -> None:
    """Transfer Playwright cookies to the curl_cffi session."""
    for c in cookies:
        session.cookies.set(
            c["name"],
            c["value"],
            domain=c.get("domain", ".canlii.org"),
            path=c.get("path", "/"),
        )
    dd_names = [c["name"] for c in cookies if "datadome" in c["name"].lower()]
    logger.info("Applied %d cookies to HTTP session (datadome: %s)",
                len(cookies), dd_names or "none found")


# ─────────────────────────────────────────────────────────────────────────────
# Session lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def _get_session():
    """Return the shared curl_cffi session, creating it if needed."""
    global _session
    if _session is not None:
        return _session

    _session = cffi_requests.Session(impersonate="chrome")

    # Load saved cookies from disk (from a previous Playwright warmup)
    cookies = _load_cookies()
    if cookies:
        _apply_cookies_to_session(_session, cookies)
        logger.info("curl_cffi session created with %d saved cookies", len(cookies))
    else:
        logger.info("curl_cffi session created (no saved cookies — warmup needed)")

    return _session


def close_browser() -> None:
    """Close the HTTP session. Called at the end of each run."""
    global _session
    if _session is not None:
        _session.close()
        _session = None
    logger.info("HTTP session closed.")


def needs_cookie_refresh() -> bool:
    return _consecutive_403s >= _403_THRESHOLD


def reset_403_counter() -> None:
    global _consecutive_403s
    _consecutive_403s = 0


def rebuild_session() -> None:
    """Close session and refresh DataDome cookies via Playwright."""
    global _consecutive_403s, _fetches_this_session
    close_browser()
    _consecutive_403s = 0
    _fetches_this_session = 0

    # Delete old cookies and get fresh ones via browser
    if os.path.exists(_COOKIE_FILE):
        os.remove(_COOKIE_FILE)
    cookies = _get_datadome_cookie_via_playwright()
    if cookies:
        # Recreate session with fresh cookies
        _get_session()
    logger.info("Session rebuilt with fresh DataDome cookies.")


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
    Ensure we have a valid DataDome cookie. Uses saved cookies if available;
    opens Playwright briefly to get fresh ones if not.
    """
    cookies = _load_cookies()
    if cookies:
        # Check if we have a datadome cookie
        has_dd = any("datadome" in c.get("name", "").lower() for c in cookies)
        if has_dd:
            logger.info("Warmup: using saved DataDome cookies")
            _get_session()  # Ensures session is created with cookies
            return

    # No valid cookies — open browser to get them
    logger.info("Warmup: no DataDome cookies found — opening browser …")
    cookies = _get_datadome_cookie_via_playwright()
    if cookies:
        _get_session()  # Will load the freshly saved cookies


# ─────────────────────────────────────────────────────────────────────────────
# Headers
# ─────────────────────────────────────────────────────────────────────────────

def _random_headers() -> dict:
    """Return realistic browser headers with a random UA and referrer."""
    referer = random.choice([
        "https://www.canlii.org/en/",
        "https://www.canlii.org/en/#search",
        "https://www.google.com/",
        "https://www.google.ca/",
    ])
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": referer,
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Text extraction (from HTML string)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_text_from_html(html: str) -> str | None:
    """
    Parse CanLII HTML and extract the decision text.
    Uses BeautifulSoup — no browser/JS needed since the text is in the HTML.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove nav, header, footer, sidebar junk
    for tag in soup.select("nav, header, footer, .navbar, .breadcrumb, .sidebar, .toc, script, style"):
        tag.decompose()

    # Try known CanLII content selectors first
    for sel in ["#cas-content", ".cas-content", "#document-content",
                ".document-content", "article", "main"]:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return text

    # Fallback: full body text
    body = soup.find("body")
    if body:
        text = body.get_text(separator="\n", strip=True)
        if len(text) > 200:
            return text

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

def fetch_case_text(url: str, db_id: str = "", citation: str = "",
                    title: str = "") -> str | None:
    """
    Fetch a court decision and return its plain text.

    Tries the provincial court website first (no bot protection) if the
    court is supported. Falls back to CanLII via curl_cffi if not.
    Returns None on any failure.
    """
    global _consecutive_403s, _fetches_this_session

    if not url:
        return None

    # ── Try court website first (no DataDome, no 403s) ────────────────────
    if db_id and can_fetch_from_court(db_id):
        logger.info("    Trying court website for %s …", db_id)
        text = fetch_from_court(db_id, citation, title)
        if text:
            return text
        logger.info("    Court website miss — falling back to CanLII")

    # ── Fall back to CanLII via curl_cffi ─────────────────────────────────
    # Session rotation — fresh cookies every N fetches
    if _fetches_this_session >= _SESSION_ROTATE_EVERY:
        rotate_wait = random.randint(10, 30)
        logger.info(
            "Session rotation after %d fetches — pausing %ds …",
            _fetches_this_session, rotate_wait,
        )
        rebuild_session()
        time.sleep(rotate_wait)
        _fetches_this_session = 0

    html_url = _to_html_url(url)
    _pause()

    session = _get_session()
    headers = _random_headers()

    try:
        resp = session.get(html_url, headers=headers, timeout=45)
        status = resp.status_code

        if status == 403:
            # DataDome cookie may have expired — try refreshing via Playwright
            logger.warning("    403 on %s — refreshing DataDome cookie …", html_url)
            rebuild_session()
            time.sleep(random.randint(5, 15))

            session = _get_session()
            resp = session.get(html_url, headers=_random_headers(), timeout=45)
            status = resp.status_code
            if status == 403:
                _consecutive_403s += 1
                logger.warning(
                    "    403 again on %s  (consecutive: %d/%d)",
                    html_url, _consecutive_403s, _403_THRESHOLD,
                )
                return None

        if status == 429:
            retry_after = int(resp.headers.get("retry-after", "60"))
            logger.warning("    429 — waiting %ds then retrying …", retry_after)
            time.sleep(retry_after)
            resp = session.get(html_url, headers=_random_headers(), timeout=45)
            status = resp.status_code
            if status != 200:
                return None

        if status != 200:
            logger.warning("    HTTP %d on %s", status, html_url)
            return None

        _consecutive_403s = 0
        _fetches_this_session += 1

        text = _extract_text_from_html(resp.text)

        if text:
            logger.info("    HTML: %d chars from %s", len(text), html_url)
        else:
            logger.warning("    No text extracted from %s", html_url)

        return text

    except Exception as exc:
        logger.error("    Fetch error for %s: %s", html_url, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Truncation helper
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
