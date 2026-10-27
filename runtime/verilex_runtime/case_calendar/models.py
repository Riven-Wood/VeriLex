from datetime import date as calendar_date, datetime, time as calendar_time, timedelta, timezone
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .errors import _VALIDATION_MESSAGES

BEIJING_ZONE = "Asia/Shanghai"
BEIJING_TIME = timezone(timedelta(hours=8))


def _invalid(code: str):
    return PydanticCustomError(code, _VALIDATION_MESSAGES[code])


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EventInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=120)
    kind: Literal["hearing", "deadline", "meeting", "interview", "mediation", "filing", "investigation", "evidence-deadline", "appeal-deadline", "enforcement-deadline", "preservation-expiry", "other"]
    customKind: str = Field(default="", max_length=40)
    date: str
    time: str | None = None
    timeZone: str = Field(min_length=1, max_length=100)
    caseName: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=5000)
    reminderMinutes: int | None = None

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise _invalid("calendar_title")
        return value

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise _invalid("calendar_date")
        try:
            calendar_date.fromisoformat(value)
        except ValueError:
            raise _invalid("calendar_date") from None
        return value

    @field_validator("time")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            raise _invalid("calendar_time")
        return value

    @field_validator("timeZone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*", value):
            raise _invalid("calendar_timezone")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise _invalid("calendar_timezone") from None
        return value

    @field_validator("reminderMinutes")
    @classmethod
    def valid_reminder(cls, value: int | None) -> int | None:
        if value is not None and value not in (0, 15, 60, 1440, 4320, 10080):
            raise _invalid("calendar_reminder")
        return value

    @model_validator(mode="after")
    def valid_local_instant(self):
        # Older clients may submit a regional zone. Preserve timed instants,
        # but all-day events retain their calendar day and use Beijing 09:00.
        if self.time is not None and self.timeZone != BEIJING_ZONE:
            zone = ZoneInfo(self.timeZone)
            local = datetime.fromisoformat(f"{self.date}T{self.time}").replace(tzinfo=zone, fold=0)
            try:
                instant = local.astimezone(timezone.utc)
                roundtrip = instant.astimezone(zone).replace(tzinfo=None)
                beijing = instant.astimezone(BEIJING_TIME)
            except (OverflowError, ValueError):
                raise _invalid("calendar_time_range") from None
            if roundtrip != local.replace(tzinfo=None):
                raise _invalid("calendar_time_gap")
            self.date = beijing.date().isoformat()
            self.time = beijing.strftime("%H:%M")
        self.timeZone = BEIJING_ZONE
        self.customKind = self.customKind.strip() if self.kind == "other" else ""
        self.reminder_at()
        return self

    def reminder_at(self) -> str | None:
        zone = BEIJING_TIME
        local = datetime.combine(
            calendar_date.fromisoformat(self.date),
            calendar_time.fromisoformat(self.time or "09:00"),
        ).replace(tzinfo=zone, fold=0)
        try:
            instant = local.astimezone(timezone.utc)
            roundtrip = instant.astimezone(zone).replace(tzinfo=None)
            reminder = None if self.reminderMinutes is None else stamp(instant - timedelta(minutes=self.reminderMinutes))
        except (OverflowError, ValueError):
            raise _invalid("calendar_time_range") from None
        if roundtrip != local.replace(tzinfo=None):
            raise _invalid("calendar_time_gap")
        return reminder


class EventUpdate(EventInput):
    revision: int = Field(ge=1)


class ReminderAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=1)
