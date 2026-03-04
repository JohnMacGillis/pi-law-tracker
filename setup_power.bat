@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM  PI Law Tracker — Power Settings Lockdown
REM
REM  Run as Administrator. Prevents the computer from sleeping, hibernating,
REM  or turning off the hard disk. Screen can still turn off (saves power,
REM  doesn't affect scheduled tasks).
REM
REM  To undo: Control Panel → Power Options → Restore default settings
REM ─────────────────────────────────────────────────────────────────────────

echo.
echo ============================================================
echo   PI Law Tracker — Power Settings Lockdown
echo ============================================================
echo.

REM Get the active power plan GUID
for /f "tokens=2 delims=:(" %%G in ('powercfg /getactivescheme') do set SCHEME=%%G
set SCHEME=%SCHEME: =%

echo   Active plan: %SCHEME%
echo.

REM ── Disable sleep (AC and DC) ────────────────────────────────────────────
echo   Disabling sleep...
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0

REM ── Disable hibernate ────────────────────────────────────────────────────
echo   Disabling hibernate...
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0
powercfg /hibernate off

REM ── Prevent hard disk from turning off ───────────────────────────────────
echo   Preventing hard disk sleep...
powercfg /change disk-timeout-ac 0
powercfg /change disk-timeout-dc 0

REM ── Disable automatic sleep after idle (system unattended timeout) ───────
REM  This is the hidden "System unattended sleep timeout" that can still
REM  put the computer to sleep even with the above settings.
echo   Disabling unattended sleep timeout...
powercfg /setacvalueindex %SCHEME% 238c9fa8-0aad-41ed-83f4-97be242c8f20 7bc4a2f9-d8fc-4469-b07b-33eb785aaca0 0
powercfg /setdcvalueindex %SCHEME% 238c9fa8-0aad-41ed-83f4-97be242c8f20 7bc4a2f9-d8fc-4469-b07b-33eb785aaca0 0

REM ── Apply changes ────────────────────────────────────────────────────────
powercfg /setactive %SCHEME%

echo.
echo   Done. This computer will not sleep or hibernate.
echo   Screen timeout is unchanged (doesn't affect tasks).
echo.
echo ============================================================
pause
