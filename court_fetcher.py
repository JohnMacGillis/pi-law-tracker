"""
court_fetcher.py
Fetches decision text directly from provincial court websites.

No bot protection, no DataDome, no CAPTCHA — just plain HTTP requests
to government sites that serve public court decisions.

Supported courts:
  - NS Supreme Court (nssc)       → decisions.courts.ns.ca
  - NS Court of Appeal (nsca)     → decisions.courts.ns.ca
  - NB Court of Appeal (nbca)     → courtsnb-coursnb.ca (PDFs)
  - NL Court of Appeal (nlca)     → records.court.nl.ca (PDFs)

Courts that only publish on CanLII (not supported here):
  - NB King's Bench (nbkb)
  - NL Supreme Court (nlsctd)
  - PEI Supreme Court (pesctd)
  - PEI Appeal Division (pescad)
"""

import logging
import random
import re
import time
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Which courts we can fetch directly ───────────────────────────────────────

# db_id → fetcher function mapping (populated at bottom of file)
_COURT_FETCHERS: dict = {}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
}


def can_fetch_from_court(db_id: str) -> bool:
    """Return True if we can fetch this court's decisions directly."""
    return db_id in _COURT_FETCHERS


def fetch_from_court(db_id: str, citation: str, title: str) -> str | None:
    """
    Try to fetch the full decision text from the court's own website.
    Returns the text, or None if not found / not supported.
    """
    fetcher = _COURT_FETCHERS.get(db_id)
    if not fetcher:
        return None
    try:
        return fetcher(citation, title)
    except Exception as exc:
        logger.warning("Court fetch failed for %s [%s]: %s", db_id, citation, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Nova Scotia — decisions.courts.ns.ca (Lexum Decisia platform)
# ─────────────────────────────────────────────────────────────────────────────
# Clean HTML, searchable, no bot protection.
# Decision text is in div.documentcontent on iframe pages.
# Search: /nsc/en/d/s/index.do?iframe=true&ref={citation}&col={25|26}

_NS_BASE = "https://decisions.courts.ns.ca"
_NS_COURT_COL = {
    "nsca": "25",   # Court of Appeal
    "nssc": "26",   # Supreme Court
}


def _fetch_ns(citation: str, title: str, col: str) -> str | None:
    """
    Search the NS court site for a decision by citation, then fetch its text.
    """
    # Step 1: Search by citation (most reliable match)
    search_url = f"{_NS_BASE}/nsc/en/d/s/index.do"
    params = {
        "iframe": "true",
        "ref": citation,
        "col": col,
    }

    time.sleep(random.uniform(1.0, 3.0))
    resp = requests.get(search_url, params=params, headers=_HEADERS, timeout=30)
    if not resp.ok:
        logger.warning("NS search HTTP %d for citation=%s", resp.status_code, citation)
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # Find the first matching decision link
    item_link = soup.select_one("li.list-item-expanded .title a[href*='/item/']")
    if not item_link:
        # Try searching by title as fallback
        params["ref"] = title.split(" v. ")[0].strip() if " v. " in title else title[:50]
        time.sleep(random.uniform(1.0, 2.0))
        resp = requests.get(search_url, params=params, headers=_HEADERS, timeout=30)
        if resp.ok:
            soup = BeautifulSoup(resp.text, "lxml")
            item_link = soup.select_one("li.list-item-expanded .title a[href*='/item/']")

    if not item_link:
        logger.debug("NS: no results for citation=%s title=%s", citation, title)
        return None

    # Step 2: Fetch the actual decision page
    href = item_link.get("href", "")
    if not href.startswith("http"):
        href = _NS_BASE + href
    # Ensure we get the iframe version (clean HTML)
    if "iframe=true" not in href:
        href += ("&" if "?" in href else "?") + "iframe=true"

    time.sleep(random.uniform(1.0, 3.0))
    resp2 = requests.get(href, headers=_HEADERS, timeout=30)
    if not resp2.ok:
        logger.warning("NS decision HTTP %d for %s", resp2.status_code, href)
        return None

    soup2 = BeautifulSoup(resp2.text, "lxml")

    # Extract text from div.documentcontent
    content = soup2.select_one("div.documentcontent")
    if content:
        text = content.get_text(separator="\n", strip=True)
        if len(text) > 200:
            logger.info("    NS court: %d chars from %s", len(text), href)
            return text

    logger.debug("NS: no documentcontent found at %s", href)
    return None


def _fetch_nssc(citation: str, title: str) -> str | None:
    return _fetch_ns(citation, title, col=_NS_COURT_COL["nssc"])


def _fetch_nsca(citation: str, title: str) -> str | None:
    return _fetch_ns(citation, title, col=_NS_COURT_COL["nsca"])


# ─────────────────────────────────────────────────────────────────────────────
# New Brunswick Court of Appeal — courtsnb-coursnb.ca (PDFs)
# ─────────────────────────────────────────────────────────────────────────────
# Decisions are PDFs organized by year/month. No search API.
# We'll try to construct the URL from the citation and extract text from PDF.
# This is less reliable than NS, so it's a best-effort fallback.

def _fetch_nbca(citation: str, title: str) -> str | None:
    """
    NB Court of Appeal publishes PDFs at:
    courtsnb-coursnb.ca/content/dam/courts/pdf/appeal-appel/decisions/{YEAR}/{MM}/...

    Since there's no search API, we browse the monthly listing pages
    and match by citation.
    """
    # Extract year from citation: "2026 NBCA 29" → 2026
    m = re.match(r"(\d{4})\s+NBCA", citation, re.IGNORECASE)
    if not m:
        return None
    year = m.group(1)

    # Try recent months (current month back to 3 months ago)
    import datetime
    now = datetime.datetime.now()
    months_to_try = []
    for offset in range(4):
        dt = now - datetime.timedelta(days=30 * offset)
        months_to_try.append((dt.strftime("%Y"), dt.strftime("%B").lower()))

    for yr, month_name in months_to_try:
        if yr != year:
            continue
        listing_url = (
            f"https://www.courtsnb-coursnb.ca/content/cour/en/appeal/"
            f"content/decisions/{yr}/{month_name}.html"
        )
        time.sleep(random.uniform(1.0, 2.0))
        try:
            resp = requests.get(listing_url, headers=_HEADERS, timeout=20)
        except Exception:
            continue
        if not resp.ok:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        # Find PDF links and match by citation number
        cit_num = re.search(r"NBCA\s+(\d+)", citation, re.IGNORECASE)
        if not cit_num:
            continue
        target = cit_num.group(1)

        for link in soup.select("a[href$='.pdf']"):
            href = link.get("href", "")
            link_text = link.get_text()
            if target in link_text or f"nbca-{target}" in href.lower():
                if not href.startswith("http"):
                    href = "https://www.courtsnb-coursnb.ca" + href
                # Fetch PDF and extract text
                return _fetch_pdf_text(href)

    return None


def _fetch_pdf_text(url: str) -> str | None:
    """Download a PDF and extract text using pdfminer if available."""
    try:
        from io import BytesIO
        try:
            from pdfminer.high_level import extract_text as pdf_extract
        except ImportError:
            logger.debug("pdfminer not installed — cannot extract PDF text")
            return None

        time.sleep(random.uniform(1.0, 2.0))
        resp = requests.get(url, headers=_HEADERS, timeout=45)
        if not resp.ok:
            return None

        text = pdf_extract(BytesIO(resp.content))
        if text and len(text.strip()) > 200:
            logger.info("    Court PDF: %d chars from %s", len(text.strip()), url)
            return text.strip()
    except Exception as exc:
        logger.debug("PDF extraction failed for %s: %s", url, exc)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Newfoundland Court of Appeal — records.court.nl.ca (PDFs)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_nlca(citation: str, title: str) -> str | None:
    """
    NL Court of Appeal at records.court.nl.ca has a search interface.
    Search by citation, find the decision ID, download PDF.
    """
    # Extract the neutral citation for search: "2026 NLCA 5"
    m = re.match(r"(\d{4}\s+NLCA\s+\d+)", citation, re.IGNORECASE)
    if not m:
        return None
    search_term = m.group(1)

    search_url = "https://records.court.nl.ca/public/supremecourt/search"
    params = {"keywords": search_term}

    time.sleep(random.uniform(1.0, 2.0))
    try:
        resp = requests.get(search_url, params=params, headers=_HEADERS, timeout=30)
    except Exception:
        return None
    if not resp.ok:
        return None

    # Look for a decision-id link in the results
    soup = BeautifulSoup(resp.text, "lxml")
    link = soup.select_one("a[href*='decisiondetails']")
    if not link:
        return None

    href = link.get("href", "")
    # Extract decision-id
    id_match = re.search(r"decision-id=(\d+)", href)
    if not id_match:
        return None

    decision_id = id_match.group(1)
    pdf_url = (
        f"https://records.court.nl.ca/public/supremecourt/"
        f"decisiondownload/?decision-id={decision_id}&mode=stream"
    )
    return _fetch_pdf_text(pdf_url)


# ─────────────────────────────────────────────────────────────────────────────
# Registry — map db_id to fetcher function
# ─────────────────────────────────────────────────────────────────────────────

_COURT_FETCHERS = {
    "nssc": _fetch_nssc,
    "nsca": _fetch_nsca,
    "nbca": _fetch_nbca,
    "nlca": _fetch_nlca,
}
