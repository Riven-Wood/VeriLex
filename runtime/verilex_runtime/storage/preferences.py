"""Validated repository for the ``runtime_preferences`` table.

Preferences are Runtime-wide user settings persisted as one JSON value per
whitelisted key. Integer reads validate against the key's spec and audit
fallbacks. Invalid saved global instructions fail explicitly so a damaged
row cannot silently remove user rules. Missing instructions mean no rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.storage.database import Database


class PreferenceDomainError(Exception):
    def __init__(self, code: str, message: str, retryable: bool, http_status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status


MAX_GLOBAL_INSTRUCTIONS_CHARS = 32_000


BACKGROUND_TASK_MAX_MINUTES = "background_task_max_minutes"


DESKTOP_DEFAULTS = {
    "notify_completed": 1, "notify_failed": 1, "notify_approval": 1,
    "notify_calendar": 1, "notification_sound": 0, "keep_awake": 0,
}


@dataclass(frozen=True, slots=True)
class _IntPreferenceSpec:
    minimum: int
    maximum: int


_INT_PREFERENCES: dict[str, _IntPreferenceSpec] = {
    "scheduled_task_max_minutes": _IntPreferenceSpec(minimum=5, maximum=60),
    BACKGROUND_TASK_MAX_MINUTES: _IntPreferenceSpec(minimum=1, maximum=120),
    **{key: _IntPreferenceSpec(0, 1) for key in DESKTOP_DEFAULTS},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class PreferenceRepository:
    def __init__(
        self,
        database: Database,
        *,
        audit: AuditRecorder | None = None,
    ):
        self._database = database
        self._audit = audit

    def get_int(self, key: str, default: int) -> int:
        spec = self._require_int_key(key)
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM runtime_preferences WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return default
        value = self._decode_int(row["value_json"])
        if value is None or not spec.minimum <= value <= spec.maximum:
            if self._audit is not None:
                self._audit.record_preference(
                    key=key,
                    outcome="read_fallback",
                    details={"default": default},
                )
            return default
        return value

    def get_personal_instructions(self) -> str:
        with self._database.connect() as connection:
            row = connection.execute("SELECT value_json FROM runtime_preferences WHERE key='personal_instructions'").fetchone()
        if row is None:
            return ""
        try:
            value = json.loads(row["value_json"])
            self._validate_instructions(value)
        except (ValueError, TypeError, PreferenceDomainError) as error:
            raise PreferenceDomainError(
                "preference_instructions_unreadable",
                "无法读取全局指令，请在设置中检查后重试。", False, 422,
            ) from error
        return value

    @staticmethod
    def _validate_instructions(value: str) -> None:
        if not isinstance(value, str) or len(value) > MAX_GLOBAL_INSTRUCTIONS_CHARS or "\x00" in value:
            raise PreferenceDomainError("preference_value_invalid", "全局指令须为不超过 32000 字的文本，不能包含空字符。", False, 422)
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise PreferenceDomainError("preference_value_invalid", "全局指令包含无效文字编码。", False, 422) from error

    def set_personal_instructions(self, value: str) -> None:
        self._validate_instructions(value)
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO runtime_preferences (key,value_json,updated_at) VALUES ('personal_instructions',?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (json.dumps(value.strip()), _utc_now()),
            )

    def desktop_activity(self) -> dict:
        """Privacy-minimal, bounded snapshot for the native app-lifetime monitor."""
        with self._database.connect() as connection:
            active = connection.execute(
                "SELECT COUNT(*) FROM turns WHERE status IN ('queued','running')"
            ).fetchone()[0]
            active += connection.execute(
                "SELECT COUNT(*) FROM background_tasks WHERE status='running'"
            ).fetchone()[0]
            events = connection.execute(
                """SELECT id, type, created_at FROM events
                   WHERE type IN ('turn_completed','turn_failed','approval_requested')
                   ORDER BY created_at DESC, rowid DESC LIMIT 100"""
            ).fetchall()
        return {"active_count": active, "events": [dict(row) for row in events],
                "preferences": {key: self.get_int(key, default) for key, default in DESKTOP_DEFAULTS.items()}}

    def set_int(self, key: str, value: int) -> None:
        spec = self._require_int_key(key)
        self._validate_int_value(spec, value)
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO runtime_preferences (key, value_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
                (key, json.dumps(value), _utc_now()),
            )

    @staticmethod
    def _require_int_key(key: str) -> _IntPreferenceSpec:
        spec = _INT_PREFERENCES.get(key)
        if spec is None:
            raise PreferenceDomainError(
                "preference_key_unknown",
                "The preference key is not supported.",
                False,
                404,
            )
        return spec

    @staticmethod
    def _validate_int_value(spec: _IntPreferenceSpec, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise PreferenceDomainError(
                "preference_value_invalid",
                "The preference value must be an integer.",
                False,
                422,
            )
        if not spec.minimum <= value <= spec.maximum:
            raise PreferenceDomainError(
                "preference_value_invalid",
                "The preference value is outside the allowed range.",
                False,
                422,
            )

    @staticmethod
    def _decode_int(raw: str) -> int | None:
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value
