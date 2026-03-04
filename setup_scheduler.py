"""
setup_scheduler.py
Run once (as Administrator) to register two Windows Task Scheduler tasks:

  PI_Law_Daily   — runs daily_run.py   every day at 06:00
  PI_Law_Weekly  — runs weekly_report.py every Monday at 08:00

Also configures:
  - Run missed tasks immediately if the computer was off at scheduled time
  - Don't stop the task if the computer switches to battery
  - Allow the task to run for up to 4 hours before timing out

Usage:
  Right-click Command Prompt → "Run as Administrator"
  python setup_scheduler.py
"""

import subprocess
import sys
import os
import tempfile


_TASK_XML_TEMPLATE = r"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{description}</Description>
  </RegistrationInfo>
  <Triggers>
    {trigger_xml}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python}</Command>
      <Arguments>"{script}"</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""

_DAILY_TRIGGER = """<CalendarTrigger>
      <StartBoundary>2026-01-01T06:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>"""

_WEEKLY_TRIGGER = """<CalendarTrigger>
      <StartBoundary>2026-01-06T08:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>
          <Monday />
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>"""


def _create_task(task_name: str, description: str, trigger_xml: str,
                 script_path: str, python: str, workdir: str) -> bool:
    xml = _TASK_XML_TEMPLATE.format(
        description=description,
        trigger_xml=trigger_xml,
        python=python,
        script=script_path,
        workdir=workdir,
    )

    # Write XML to a temp file, import it with schtasks
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False,
                                      encoding="utf-16")
    tmp.write(xml)
    tmp.close()

    print(f"\n  Setting up: {task_name}")
    print(f"    Script:  {script_path}")
    print(f"    Options: run-if-missed, ignore-battery, 4h timeout")

    cmd = f'schtasks /create /tn "{task_name}" /xml "{tmp.name}" /f'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    os.unlink(tmp.name)

    if result.returncode == 0:
        print(f"    ✓  Registered successfully")
        return True
    else:
        print(f"    ✗  Error: {result.stderr.strip()}")
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

    ok1 = _create_task(
        task_name="PI_Law_Daily",
        description="PI Law Tracker — daily case collection and analysis",
        trigger_xml=_DAILY_TRIGGER,
        script_path=daily,
        python=python,
        workdir=script_dir,
    )
    ok2 = _create_task(
        task_name="PI_Law_Weekly",
        description="PI Law Tracker — weekly digest email",
        trigger_xml=_WEEKLY_TRIGGER,
        script_path=weekly,
        python=python,
        workdir=script_dir,
    )

    print("\n" + "=" * 60)
    if ok1 and ok2:
        print("  All tasks registered successfully.")
        print()
        print("  Task settings applied:")
        print("    • Run missed tasks on next boot (StartWhenAvailable)")
        print("    • Don't stop if switching to battery power")
        print("    • 4-hour execution timeout")
        print("    • Requires network connection")
        print()
        print("  Schedule:")
        print("    PI_Law_Daily   — every day at 06:00")
        print("    PI_Law_Weekly  — Mondays at 08:00")
    else:
        print("  One or more tasks failed.")
        print("  Make sure you are running as Administrator.")
    print("=" * 60)


if __name__ == "__main__":
    main()
