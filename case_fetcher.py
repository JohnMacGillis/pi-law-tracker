"""
case_fetcher.py
Fetches court decision text from CanLII as a PDF using a Chrome TLS fingerprint
(curl_cffi) plus an exported DataDome session cookie.

Why PDF?  CanLII's HTML pages are behind DataDome's JS challenge, which blocks
Python HTTP clients.  PDFs are served by the same CDN but the DataDome cookie
obtained from a real Chrome session is accepted.

Setup:
  1. Run  refresh_cookies.py  to export your Chrome cookies to canlii_cookies.json
  2. If a 403 recurs after several runs, re-run refresh_cookies.py
"""

import json
import logging
import os
import random
import time

import fitz                           # PyMuPDF
from curl_cffi import requests        # Chrome TLS fingerprint

from config import REQUEST_DELAY_SECONDS

logger = logging.getLogger(__name__)

# ── Cookie file ───────────────────────────────────────────────────────────────
COOKIE_FILE = "canlii_cookies.json"

# ── 403 threshold — how many consecutive 403s before we flag for refresh ──────
_403_THRESHOLD    = 3
_consecutive_403s = 0

# ── Session (lazy-built) ──────────────────────────────────────────────────────
_session = None


# ─────────────────────────────────────────────────────────────────────────────
# Session management
# ─────────────────────────────────────────────────────────────────────────────

def _build_session() -> "curl_cffi.requests.Session":
    """Create a curl_cffi session that impersonates Chrome and loads cookies."""
    s = requests.Session(impersonate="chrome120")

    if not os.path.exists(COOKIE_FILE):
        logger.warning(
            "Cookie file not found (%s). "
            "Run  python refresh_cookies.py  to create it.",
            COOKIE_FILE,
        )
        return s

    try:
        with open(COOKIE_FILE, encoding="utf-8") as fh:
            cookies = json.load(fh)

        for c in cookies:
            domain = c.get("domain", ".canlii.org")
            if not domain.startswith("."):
                domain = "." + domain
            s.cookies.set(
                c["name"], c["value"],
                domain=domain,
                path=c.get("path", "/"),
            )

        dd_present = any(c["name"] == "datadome" for c in cookies)
        logger.info(
            "Loaded %d cookies from %s  (datadome: %s)",
            len(cookies), COOKIE_FILE, dd_present,
        )

    except Exception as exc:
        logger.error("Failed to load cookies from %s: %s", COOKIE_FILE, exc)

    return s


def _get_session():
    global _session
    if _session is None:
        _session = _build_session()
    return _session


def rebuild_session() -> None:
    """
    Reload cookies from canlii_cookies.json and reset the 403 counter.
    Call this after refresh_cookies.py has updated the cookie file.
    """
    global _session, _consecutive_403s
    _session          = _build_session()
    _consecutive_403s = 0
    logger.info("Session rebuilt with fresh cookies.")


# ─────────────────────────────────────────────────────────────────────────────
# 403 / cookie-refresh state
# ─────────────────────────────────────────────────────────────────────────────

def needs_cookie_refresh() -> bool:
    """Return True when consecutive 403s have hit the threshold."""
    return _consecutive_403s >= _403_THRESHOLD


def reset_403_counter() -> None:
    global _consecutive_403s
    _consecutive_403s = 0


# ─────────────────────────────────────────────────────────────────────────────
# Fetching
# ─────────────────────────────────────────────────────────────────────────────

def _pause() -> None:
    """Polite random delay between requests."""
    time.sleep(random.uniform(REQUEST_DELAY_SECONDS, REQUEST_DELAY_SECONDS + 5.0))


def _case_url_to_pdf_url(url: str) -> str:
    """Convert a CanLII case HTML URL to its PDF counterpart."""
    u = url.rstrip("/")
    # Strip any .html extension first
    if u.lower().endswith(".html"):
        u = u[:-5]
    # If already .pdf, leave it
    if not u.lower().endswith(".pdf"):
        u += ".pdf"
    return u


def fetch_case_text(url: str) -> str | None:
    """
    Download the PDF for `url` from CanLII and return its plain text.

    Returns None on any error.  Updates the consecutive-403 counter so
    daily_run.py can decide when to trigger a cookie refresh.
    """
    global _consecutive_403s

    if not url:
        return None

    pdf_url = _case_url_to_pdf_url(url)
    _pause()

    try:
        resp = _get_session().get(pdf_url, timeout=60)

        if resp.status_code == 403:
            _consecutive_403s += 1
            logger.warning(
                "    403 Forbidden on %s  (consecutive: %d/%d)",
                pdf_url, _consecutive_403s, _403_THRESHOLD,
            )
            if needs_cookie_refresh():
                logger.warning(
                    "    ── Cookie refresh threshold reached. "
                    "Run  python refresh_cookies.py  or wait for auto-refresh."
                )
            return None

        resp.raise_for_status()
        _consecutive_403s = 0   # successful request resets the counter

        if b"%PDF" not in resp.content[:10]:
            logger.warning("    Response is not a PDF: %s", pdf_url)
            return None

        doc  = fitz.open(stream=resp.content, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc).strip()
        doc.close()

        if len(text) < 200:
            logger.warning(
                "    PDF too short (%d chars) — may be scanned image: %s",
                len(text), pdf_url,
            )
            return None

        logger.info("    PDF: %d chars extracted from %s", len(text), pdf_url)
        return text

    except Exception as exc:
        logger.error("    PDF fetch error for %s: %s", pdf_url, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Truncation helper
# ─────────────────────────────────────────────────────────────────────────────

def smart_truncate(text: str, max_chars: int) -> str:
    """
    Trim `text` to `max_chars` while keeping the beginning (case intro / type
    of claim) and the end (where the damages award usually appears).
    """
    if len(text) <= max_chars:
        return text

    front = max_chars // 3
    back  = max_chars - front
    return (
        text[:front]
        + "\n\n[... middle of decision omitted for length ...]\n\n"
        + text[-back:]
    )
