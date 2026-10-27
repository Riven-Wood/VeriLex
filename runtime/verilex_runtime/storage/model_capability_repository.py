from __future__ import annotations

import json
import sqlite3

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.models import ModelCapabilityRecord, ReasoningEffort


class ModelCapabilityRepository:
    def __init__(self, database: Database):
        self._database = database

    def get(self, provider_id: str, model: str) -> ModelCapabilityRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM model_capabilities WHERE provider_id = ? AND model = ?",
                (provider_id, model),
            ).fetchone()
        return None if row is None else self._record(row)

    def list_by_provider(self, provider_id: str) -> list[ModelCapabilityRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM model_capabilities
                   WHERE provider_id = ? ORDER BY model""",
                (provider_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def put(self, record: ModelCapabilityRecord) -> ModelCapabilityRecord:
        reasoning_json = self._serialize_reasoning_efforts(
            record.supported_reasoning_efforts
        )
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO model_capabilities
                   (provider_id, model, supports_vision, supports_native_pdf,
                    source, created_at, updated_at, context_window_tokens,
                    supported_reasoning_efforts_json, context_window_source,
                    reasoning_efforts_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider_id, model) DO UPDATE SET
                     supports_vision = excluded.supports_vision,
                     supports_native_pdf = excluded.supports_native_pdf,
                     source = excluded.source,
                     context_window_tokens = excluded.context_window_tokens,
                     supported_reasoning_efforts_json = excluded.supported_reasoning_efforts_json,
                     context_window_source = excluded.context_window_source,
                     reasoning_efforts_source = excluded.reasoning_efforts_source,
                     updated_at = excluded.updated_at""",
                (
                    record.provider_id,
                    record.model,
                    int(record.supports_vision),
                    int(record.supports_native_pdf),
                    record.source,
                    record.created_at,
                    record.updated_at,
                    record.context_window_tokens,
                    reasoning_json,
                    record.context_window_source,
                    record.reasoning_efforts_source,
                ),
            )
            row = connection.execute(
                "SELECT * FROM model_capabilities WHERE provider_id = ? AND model = ?",
                (record.provider_id, record.model),
            ).fetchone()
        assert row is not None
        return self._record(row)

    @staticmethod
    def _record(row: sqlite3.Row) -> ModelCapabilityRecord:
        return ModelCapabilityRecord(
            provider_id=row["provider_id"],
            model=row["model"],
            supports_vision=bool(row["supports_vision"]),
            supports_native_pdf=bool(row["supports_native_pdf"]),
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            context_window_tokens=row["context_window_tokens"],
            supported_reasoning_efforts=ModelCapabilityRepository._parse_reasoning_efforts(
                row["supported_reasoning_efforts_json"]
            ),
            context_window_source=row["context_window_source"],
            reasoning_efforts_source=row["reasoning_efforts_source"],
        )

    @staticmethod
    def _serialize_reasoning_efforts(
        values: tuple[ReasoningEffort, ...] | tuple[str, ...] | None,
    ) -> str | None:
        if values is None:
            return None
        normalized: list[str] = []
        for value in values:
            try:
                effort = ReasoningEffort(value)
            except ValueError as error:
                raise ValueError("unsupported reasoning effort") from error
            if effort is ReasoningEffort.AUTO or effort.value in normalized:
                raise ValueError("reasoning efforts must be a unique subset of low/medium/high")
            normalized.append(effort.value)
        canonical = [value for value in ("low", "medium", "high") if value in normalized]
        if normalized != canonical:
            raise ValueError("reasoning efforts must use low/medium/high order")
        return json.dumps(normalized, separators=(",", ":"))

    @staticmethod
    def _parse_reasoning_efforts(value: str | None) -> tuple[ReasoningEffort, ...] | None:
        if value is None:
            return None
        try:
            parsed = json.loads(value)
            if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
                raise ValueError
            result = tuple(ReasoningEffort(item) for item in parsed)
            canonical = tuple(
                effort
                for effort in (ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH)
                if effort in result
            )
            if result != canonical:
                raise ValueError
            return result
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise sqlite3.DatabaseError("invalid persisted reasoning efforts") from error
