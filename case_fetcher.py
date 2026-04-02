"""
case_fetcher.py
Fetches court decision text from CanLII.

Primary method: curl_cffi — impersonates Chrome's TLS fingerprint perfectly.
  No browser automation to detect, no DataDome JS challenges to fight.
  Just plain HTTP requests that look identical to real Chrome at the TLS level.

Fallback: Playwright (real browser) — only used if curl_cffi gets blocked.
  Opens a visible Chrome window so the user can solve any CAPTCHA.
"""

import logging
import os
import random
import re
import threading
import time

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from config import REQUEST_DELAY_SECONDS, DATA_DIR

logger = logging.getLogger(__name__)

# ── User agents — rotated per fetch ──────────────────────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# ── Rate limiter (thread-safe) ────────────────────────────────────────────────
_request_lock      = threading.Lock()
_last_request_time = 0.0

# ── 403 tracking (daily_run.py checks these) ─────────────────────────────────
_403_THRESHOLD    = 3
_consecutive_403s = 0

# ── Session: curl_cffi keeps cookies across requests like a real browser ──────
_session = None

# ── Session rotation ─────────────────────────────────────────────────────────
_SESSION_ROTATE_EVERY = 15
_fetches_this_session = 0


# ─────────────────────────────────────────────────────────────────────────────
# Session lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def _get_session():
    """Return the shared curl_cffi session, creating it if needed."""
    global _session
    if _session is not None:
        return _session

    _session = cffi_requests.Session(impersonate="chrome")
    logger.info("curl_cffi session created (impersonating Chrome TLS)")
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
    """Close and recreate the session — fresh cookies, fresh identity."""
    global _consecutive_403s
    close_browser()
    _consecutive_403s = 0
    logger.info("Session rebuilt — fresh cookies on next fetch.")


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
    Hit the CanLII homepage to establish cookies before fetching cases.
    With curl_cffi this is just a quick HTTP GET — no browser needed.
    """
    session = _get_session()
    try:
        logger.info("Warming up — hitting CanLII homepage …")
        headers = _random_headers()
        resp = session.get(
            "https://www.canlii.org/en/",
            headers=headers,
            timeout=30,
        )
        logger.info("Warmup: HTTP %d (%d bytes)", resp.status_code, len(resp.content))
        time.sleep(random.uniform(2, 5))
    except Exception as exc:
        logger.warning("Warmup failed (non-fatal): %s", exc)


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
# Text extraction (from HTML string, no browser needed)
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

def fetch_case_text(url: str) -> str | None:
    """
    Fetch a CanLII decision page and return its plain text.
    Uses curl_cffi (Chrome TLS impersonation) — no browser needed.
    Returns None on any failure.
    """
    global _consecutive_403s, _fetches_this_session

    if not url:
        return None

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
            # Single retry after a longer pause
            retry_wait = random.randint(30, 90)
            logger.warning("    403 on %s — waiting %ds then retrying …", html_url, retry_wait)
            time.sleep(retry_wait)
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
