"""
setup_scheduler.py
Run once (as Administrator) to register two Windows Task Scheduler tasks:

  PI_Law_Daily   — runs daily_run.py   every day at 06:00
  PI_Law_Weekly  — runs weekly_report.py every Monday at 08:00

Usage:
  Right-click Command Prompt → "Run as Administrator"
  python setup_scheduler.py
"""

import subprocess
import sys
import os


def run_cmd(description: str, cmd: str) -> bool:
    print(f"\n  Setting up: {description}")
    print(f"  Command:    {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  ✓  Success")
        return True
    else:
        print(f"  ✗  Error (code {result.returncode}): {result.stderr.strip()}")
        return False


def main():
    python     = sys.executable
    script_dir = os.path.dirname(os.path.abspath(__file__))
    daily      = os.path.join(script_dir, "daily_run.py")
    weekly     = os.path.join(script_dir, "weekly_report.py")

    print("=" * 60)
    print("  PI Law Tracker — Windows Task Scheduler Setup")
    print("=" * 60)
    print(f"\n  Python:     {python}")
    print(f"  Script dir: {script_dir}")

    # Wrap paths in quotes in case of spaces
    daily_cmd  = f'schtasks /create /tn "PI_Law_Daily" /tr "\\"{python}\\" \\"{daily}\\"" /sc DAILY /st 06:00 /f'
    weekly_cmd = f'schtasks /create /tn "PI_Law_Weekly" /tr "\\"{python}\\" \\"{weekly}\\"" /sc WEEKLY /d MON /st 08:00 /f'

    ok1 = run_cmd("Daily collector (06:00 every day)",   daily_cmd)
    ok2 = run_cmd("Weekly email digest (08:00 Mondays)", weekly_cmd)

    print("\n" + "=" * 60)
    if ok1 and ok2:
        print("  All tasks registered successfully.")
        print("\n  To verify, open Task Scheduler and look for:")
        print("    PI_Law_Daily   — runs daily_run.py at 06:00")
        print("    PI_Law_Weekly  — runs weekly_report.py on Mondays at 08:00")
    else:
        print("  One or more tasks failed. Make sure you are running as Administrator.")
        print("  You can also create the tasks manually in Task Scheduler.")
    print("=" * 60)


if __name__ == "__main__":
    main()
