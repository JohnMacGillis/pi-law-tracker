@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM  PI Law Tracker — Refresh CanLII Cookies
REM
REM  Double-click this file whenever the daily run reports 403 errors.
REM  A Chrome window will open to CanLII. If a security slider appears,
REM  drag it to the right, wait for the page to load, then click OK.
REM ─────────────────────────────────────────────────────────────────────────

cd /d "%~dp0"
python refresh_cookies.py

if %errorlevel% neq 0 (
    echo.
    echo  Something went wrong. Check the output above for details.
    echo  Common fix: make sure Google Chrome is installed.
    pause
)
