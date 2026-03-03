"""
refresh_cookies.py
Opens real Google Chrome to CanLII, waits for you to solve any DataDome
slider challenge, then exports the session cookies to canlii_cookies.json.

Run this:
  • Manually when daily_run.py starts reporting 403 errors
  • Automatically: daily_run.py will open this window when it detects
    3+ consecutive 403s

How it works:
  1. Launches YOUR installed Chrome with a remote-debug port (9222)
  2. Navigates to the CanLII NS Supreme Court listing page
  3. Shows a popup — solve the slider if it appears, then click OK
  4. Connects to Chrome's DevTools Protocol to export cookies
  5. Saves cookies to canlii_cookies.json

Requirements (one-time install):
  pip install websocket-client

Note: Playwright is NOT required. We use the lightweight CDP-over-WebSocket
      approach so you don't need to install browser binaries.
"""

import ctypes
import json
import logging
import os
import subprocess
import sys
import time

import requests as std_requests   # standard requests (already in requirements)
import websocket                  # websocket-client

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
COOKIE_FILE  = "canlii_cookies.json"
DEBUG_PORT   = 9222

# A stable CanLII landing page — NS Supreme Court listing is reliable
TARGET_URL   = "https://www.canlii.org/en/ns/nssc/"

# Temp profile directory for the debug Chrome session.
# We use a separate profile so we don't interfere with your normal Chrome.
TEMP_PROFILE = os.path.join(os.environ.get("TEMP", r"C:\Temp"), "canlii-chrome-profile")

# Paths where Chrome is typically installed on Windows
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_chrome() -> str | None:
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    return None


def _popup(title: str, message: str) -> None:
    """
    Show a Windows message box and wait for the user to click OK.
    Falls back to a console prompt if the Windows API is unavailable.
    """
    try:
        # MB_OK (0x0) | MB_ICONINFORMATION (0x40) | MB_SYSTEMMODAL (0x1000)
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x1040)
    except Exception:
        print(f"\n{'═' * 60}")
        print(f"  {title}")
        print(f"{'─' * 60}")
        print(message)
        print(f"{'═' * 60}")
        input("  Press Enter to continue …")


def _get_ws_debugger_url(port: int, retries: int = 10) -> str | None:
    """Poll Chrome's HTTP endpoint until it's ready, then return the WebSocket URL."""
    for attempt in range(retries):
        try:
            resp = std_requests.get(
                f"http://localhost:{port}/json/version", timeout=3
            )
            if resp.status_code == 200:
                return resp.json().get("webSocketDebuggerUrl")
        except Exception:
            pass
        time.sleep(1)
    return None


def _read_cookies_via_cdp(ws_url: str) -> list:
    """Connect to Chrome DevTools Protocol and retrieve all cookies."""
    ws = websocket.create_connection(ws_url, timeout=15)
    try:
        # Network.getAllCookies returns every cookie in the browser profile
        ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
        raw = ws.recv()
        result = json.loads(raw)
        return result.get("result", {}).get("cookies", [])
    finally:
        ws.close()


# ─────────────────────────────────────────────────────────────────────────────
# Main export function
# ─────────────────────────────────────────────────────────────────────────────

def refresh_cookies(interactive: bool = True) -> bool:
    """
    Launch Chrome → navigate to CanLII → export cookies to canlii_cookies.json.

    Parameters
    ----------
    interactive : bool
        True  — shows a popup asking the user to solve any slider challenge.
        False — waits a fixed time (15 s) and exports whatever cookies exist.
                Use this for headless/service contexts; may fail if DataDome
                triggers a challenge.

    Returns
    -------
    bool
        True if the DataDome cookie was successfully saved.
    """
    chrome = _find_chrome()
    if not chrome:
        logger.error(
            "Google Chrome not found.\n"
            "Expected: C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\n"
            "Please install Chrome and try again."
        )
        if interactive:
            _popup(
                "PI Law Tracker — Chrome Not Found",
                "Google Chrome is not installed in the expected location.\n\n"
                "Please install Chrome from https://www.google.com/chrome/\n"
                "then run this script again.",
            )
        return False

    logger.info("Launching Chrome (debug port %d) → %s", DEBUG_PORT, TARGET_URL)

    # Close any Chrome process that might already hold port 9222
    subprocess.run(
        "taskkill /f /im chrome.exe",
        shell=True, capture_output=True,
    )
    time.sleep(2)

    # Launch Chrome with remote-debugging enabled and a fresh temp profile
    proc = subprocess.Popen([
        chrome,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={TEMP_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        TARGET_URL,
    ])

    # ── Wait for user (interactive) or a fixed delay (non-interactive) ─────────
    if interactive:
        _popup(
            "PI Law Tracker — Complete the CanLII Security Challenge",
            "A Chrome window has opened to CanLII.\n\n"
            "IF a security slider appears:\n"
            "  → Drag the slider arrow all the way to the right to solve it.\n"
            "  → Wait until the CanLII page finishes loading.\n\n"
            "IF the page loads normally (no slider):\n"
            "  → Just click OK below.\n\n"
            "Click OK once the CanLII page has fully loaded.",
        )
    else:
        logger.info("Non-interactive mode — waiting 15 s for Chrome to load …")
        time.sleep(15)

    # ── Connect to Chrome DevTools Protocol and export cookies ─────────────────
    ws_url = _get_ws_debugger_url(DEBUG_PORT)
    if not ws_url:
        logger.error(
            "Could not connect to Chrome's debug port (%d). "
            "Chrome may have crashed or not started correctly.",
            DEBUG_PORT,
        )
        proc.terminate()
        return False

    try:
        all_cookies = _read_cookies_via_cdp(ws_url)
    except Exception as exc:
        logger.error("CDP cookie export failed: %s", exc)
        proc.terminate()
        return False

    # Filter to CanLII cookies only
    canlii_cookies = [
        c for c in all_cookies
        if "canlii" in c.get("domain", "").lower()
    ]

    if not canlii_cookies:
        logger.warning(
            "No CanLII cookies found. "
            "The page may not have loaded or the session wasn't established."
        )
        proc.terminate()
        if interactive:
            _popup(
                "PI Law Tracker — No Cookies Found",
                "No CanLII cookies were found.\n\n"
                "This usually means the page did not finish loading.\n\n"
                "Please run refresh_cookies.py again and make sure the\n"
                "CanLII page fully loads before clicking OK.",
            )
        return False

    # Save to JSON
    os.makedirs(os.path.dirname(os.path.abspath(COOKIE_FILE)) or ".", exist_ok=True)
    with open(COOKIE_FILE, "w", encoding="utf-8") as fh:
        json.dump(canlii_cookies, fh, indent=2)

    dd_found = any(c["name"] == "datadome" for c in canlii_cookies)
    logger.info(
        "Saved %d CanLII cookies to %s  (datadome present: %s)",
        len(canlii_cookies), COOKIE_FILE, dd_found,
    )

    proc.terminate()

    if interactive:
        if dd_found:
            _popup(
                "PI Law Tracker — Cookies Saved ✓",
                f"Success!  {len(canlii_cookies)} cookies saved to canlii_cookies.json\n\n"
                "The DataDome session cookie is present.\n\n"
                "You can close this window. The daily run will use these\n"
                "cookies automatically from now on.",
            )
        else:
            _popup(
                "PI Law Tracker — Warning: DataDome Cookie Missing",
                f"{len(canlii_cookies)} cookies were saved, but the DataDome "
                "cookie was not found.\n\n"
                "This may mean the security challenge was not completed.\n\n"
                "Please run refresh_cookies.py again, solve the slider\n"
                "(if it appears), and wait for the page to fully load.",
            )

    return dd_found


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    ok = refresh_cookies(interactive=True)
    sys.exit(0 if ok else 1)
