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

# ── Device profiles — rotated per fetch to simulate different users ───────────
# Each profile is (user_agent, viewport_width, viewport_height)
_DEVICE_PROFILES = [
    # Windows 10/11 — Chrome 122, 1080p
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36",
        1920, 1080,
    ),
    # Windows 10/11 — Chrome 124, 1440p
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
        2560, 1440,
    ),
    # Windows 10/11 — Chrome 120, 1366×768 (very common laptop size)
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36",
        1366, 768,
    ),
    # Windows 10/11 — Chrome 126, 1536×864
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36",
        1536, 864,
    ),
    # macOS — Chrome 123, 1440×900 (MacBook Pro 15")
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36",
        1440, 900,
    ),
    # macOS — Chrome 125, 1920×1080 (external monitor)
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36",
        1920, 1080,
    ),
    # macOS — Chrome 121, 2560×1600 (MacBook Pro 16" Retina)
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36",
        2560, 1600,
    ),
    # Windows 10/11 — Chrome 128, 1280×800
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36",
        1280, 800,
    ),
]

# Shuffle once at import so we cycle through all profiles before repeating
_profile_cycle = list(_DEVICE_PROFILES)
random.shuffle(_profile_cycle)
_profile_index = 0


def _next_profile() -> tuple[str, int, int]:
    """Return the next (user_agent, width, height) with small random jitter."""
    global _profile_index
    ua, base_w, base_h = _profile_cycle[_profile_index % len(_profile_cycle)]
    _profile_index += 1

    # Small viewport jitter (±0-80px) — simulates natural window resizing
    w = base_w + random.randint(-80, 80)
    h = base_h + random.randint(-50, 50)

    return ua, max(800, w), max(600, h)

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

# ── Session rotation — look like different users over time ────────────────────
_SESSION_ROTATE_EVERY = 8    # Rebuild browser every N successful fetches
_fetches_this_session = 0


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

    # Context uses a baseline profile — each page overrides with its own
    # random profile via _apply_profile() for per-fetch rotation.
    ua, vp_w, vp_h = _next_profile()
    tz = random.choice(["America/Halifax", "America/Toronto", "America/Moncton"])
    logger.info("Context baseline: %dx%d  tz=%s  ua=…%s", vp_w, vp_h, tz, ua[-40:])

    state = _STATE_FILE if os.path.exists(_STATE_FILE) else None
    _context = _browser.new_context(
        storage_state=state,
        viewport={"width": vp_w, "height": vp_h},
        locale="en-CA",
        timezone_id=tz,
        user_agent=ua,
    )

    # Remove automation fingerprints that bot-detection looks for
    _context.add_init_script("""
        // Hide webdriver flag
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

        // Hide automation-related properties
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

        // Realistic plugin/mime arrays (Chrome on Windows/Mac)
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-CA', 'en-US', 'en'],
        });

        // Realistic Chrome object
        window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };

        // Realistic permissions query
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : origQuery(parameters);
    """)

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
# Per-page profile rotation
# ─────────────────────────────────────────────────────────────────────────────

def _apply_profile(page) -> None:
    """
    Give this page a unique device fingerprint — different user agent and
    viewport on every fetch so CanLII sees what looks like many different
    users, not one bot hammering the site.
    """
    ua, w, h = _next_profile()
    page.set_viewport_size({"width": w, "height": h})

    # Realistic referrer — looks like the user navigated from CanLII search
    # or Google, not from nothing (which screams bot).
    referer = random.choice([
        "https://www.canlii.org/en/",
        "https://www.canlii.org/en/#search",
        "https://www.google.com/",
        "https://www.google.ca/",
    ])
    page.set_extra_http_headers({
        "User-Agent": ua,
        "Referer": referer,
        "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
    })
    logger.debug("    Profile: %dx%d  ua=…%s", w, h, ua[-30:])


# ─────────────────────────────────────────────────────────────────────────────
# Human behaviour simulation
# ─────────────────────────────────────────────────────────────────────────────

def _simulate_human(page) -> None:
    """
    Mimic a real person looking at a court decision: brief pause, mouse
    movement to the content area, a scroll or two.  Bot detectors like
    DataDome track mouse events and scroll patterns.
    """
    try:
        vp = page.viewport_size or {"width": 1280, "height": 800}
        w, h = vp["width"], vp["height"]

        # Initial reading pause (like eyes finding the content)
        page.wait_for_timeout(random.randint(800, 2200))

        # Move mouse to a plausible content area (centre-ish, with jitter)
        x = w // 2 + random.randint(-200, 200)
        y = h // 3 + random.randint(-100, 100)
        page.mouse.move(max(10, x), max(10, y))
        page.wait_for_timeout(random.randint(300, 800))

        # Scroll down a bit — reading the opening paragraphs
        scrolls = random.randint(1, 3)
        for _ in range(scrolls):
            page.mouse.wheel(0, random.randint(200, 600))
            page.wait_for_timeout(random.randint(400, 1200))

        # Occasionally move mouse again (like selecting text)
        if random.random() < 0.4:
            x2 = w // 2 + random.randint(-300, 300)
            y2 = h // 2 + random.randint(-150, 150)
            page.mouse.move(max(10, x2), max(10, y2))
            page.wait_for_timeout(random.randint(200, 600))

    except Exception:
        # Non-critical — don't let simulation failures break the fetch
        pass


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
    global _consecutive_403s, _fetches_this_session

    if not url:
        return None

    # Session rotation — tear down and rebuild the browser every N fetches
    # so DataDome sees what looks like different users.
    if _fetches_this_session >= _SESSION_ROTATE_EVERY and _context is not None:
        rotate_wait = random.randint(15, 45)
        logger.info(
            "Session rotation after %d fetches — pausing %ds, rebuilding …",
            _fetches_this_session, rotate_wait,
        )
        rebuild_session()   # closes browser + deletes saved state
        time.sleep(rotate_wait)
        _fetches_this_session = 0

    html_url = _to_html_url(url)
    _pause()

    ctx  = _get_context()
    page = ctx.new_page()
    _apply_profile(page)

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
        _fetches_this_session += 1

        # Simulate human reading behaviour — mouse movement + scroll
        _simulate_human(page)

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
