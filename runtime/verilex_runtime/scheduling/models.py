from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal['daily', 'weekly', 'once']
    timeOfDay: str | None = Field(default=None, pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    weekday: int | None = Field(default=None, ge=0, le=6)
    fireAt: str | None = None

    @model_validator(mode='after')
    def valid_time(self):
        if self.kind in ('daily', 'weekly') and self.timeOfDay is None:
            raise ValueError('需要运行时间')
        if self.kind == 'weekly' and self.weekday is None:
            raise ValueError('需要星期')
        if self.kind == 'once':
            if self.fireAt is None:
                raise ValueError('需要运行日期')
            dt = parse(self.fireAt)
            if dt.tzinfo is None:
                raise ValueError('日期需要时区')
            self.fireAt = stamp(dt)
        return self


class TaskInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    instruction: str = Field(min_length=1, max_length=2000)
    name: str = Field(default='', max_length=40)
    schedule: Schedule
    providerId: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=256)
    workspaceId: str | None = None
    knowledgeLibraryIds: list[str] = Field(default_factory=list, max_length=10)
    maxMinutes: int = Field(default=15, ge=5, le=60)


def parse(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def utc_now():
    return datetime.now(timezone.utc)


def local_zone():
    # Read the system's IANA zone, retaining DST rules rather than today's offset.
    try:
        with Path('/etc/localtime').open('rb') as source:
            return ZoneInfo.from_file(source)
    except (OSError, ValueError):
        return datetime.now().astimezone().tzinfo


def compute_next_run(schedule, *, now, zone, enabled=True, run_count=0):
    if not enabled or (schedule.kind == 'once' and run_count):
        return None
    if schedule.kind == 'once':
        return schedule.fireAt if parse(schedule.fireAt) > now else None
    local = now.astimezone(zone)
    hour, minute = map(int, schedule.timeOfDay.split(':'))
    for offset in range(9):
        day = local.date() + timedelta(days=offset)
        if schedule.kind == 'weekly' and day.weekday() != schedule.weekday:
            continue
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        # Spring gaps run at the first corresponding real time; folds run once.
        candidate = candidate.astimezone(timezone.utc)
        if candidate > now:
            return stamp(candidate)
    raise ValueError('No next run')
