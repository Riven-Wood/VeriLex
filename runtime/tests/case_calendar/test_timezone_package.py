"""The supported Windows build cannot depend on a Unix zoneinfo directory."""
import os
import subprocess
import sys


def test_timezone_package_works_without_system_timezone_files():
    result = subprocess.run(
        [sys.executable, "-c", """
from verilex_runtime.case_calendar.models import EventInput
item = EventInput(title='虚构时区验证', kind='meeting', date='2026-10-06',
    time='09:00', timeZone='Asia/Shanghai', reminderMinutes=0)
assert item.reminder_at() == '2026-10-06T01:00:00.000000Z'
"""],
        env={**os.environ, "PYTHONTZPATH": ""}, capture_output=True,
        text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
