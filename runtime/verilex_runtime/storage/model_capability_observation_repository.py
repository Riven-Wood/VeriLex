from __future__ import annotations

import sqlite3

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.models import (
    ModelCapabilityObservationRecord,
    ModelCapabilityResolution,
)


class ModelCapabilityObservationRepository:
    """Runtime-learned capability evidence kept separate from declarations."""

    def __init__(self, database: Database):
        self._database = database

    def put(
        self, record: ModelCapabilityObservationRecord
    ) -> ModelCapabilityObservationRecord:
        with self._database.connect() as connection:
            provider = connection.execute(
                "SELECT updated_at FROM providers WHERE id = ?",
                (record.provider_id,),
            ).fetchone()
            if provider is None:
                raise ValueError("provider_not_found")
            if provider["updated_at"] != record.provider_updated_at:
                raise ValueError("model_capability_observation_stale")
            connection.execute(
                """INSERT INTO model_capability_observations
                   (provider_id,model,capability,supported,provider_updated_at,
                    evidence_code,observed_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(provider_id,model,capability) DO UPDATE SET
                     supported=excluded.supported,
                     provider_updated_at=excluded.provider_updated_at,
                     evidence_code=excluded.evidence_code,
                     observed_at=excluded.observed_at""",
                (
                    record.provider_id,
                    record.model,
                    record.capability,
                    int(record.supported),
                    record.provider_updated_at,
                    record.evidence_code,
                    record.observed_at,
                ),
            )
            row = connection.execute(
                """SELECT * FROM model_capability_observations
                   WHERE provider_id=? AND model=? AND capability=?""",
                (record.provider_id, record.model, record.capability),
            ).fetchone()
        assert row is not None
        return self._record(row)

    def get_current(
        self, provider_id: str, model: str, capability: str
    ) -> ModelCapabilityObservationRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT observation.*
                   FROM model_capability_observations AS observation
                   JOIN providers AS provider ON provider.id=observation.provider_id
                   WHERE observation.provider_id=? AND observation.model=?
                     AND observation.capability=?
                     AND observation.provider_updated_at=provider.updated_at""",
                (provider_id, model, capability),
            ).fetchone()
        return None if row is None else self._record(row)

    def resolve(
        self,
        *,
        provider_id: str,
        model: str,
        capability: str,
        declared_supported: bool | None,
        declared_source: str = "user_declared",
    ) -> ModelCapabilityResolution:
        observation = self.get_current(provider_id, model, capability)
        if observation is not None:
            return ModelCapabilityResolution(
                supported=observation.supported,
                source="runtime_observation",
            )
        if declared_source not in {"curated", "user_declared", "unknown"}:
            raise ValueError("model_capability_source_invalid")
        return ModelCapabilityResolution(
            supported=declared_supported,
            source=declared_source if declared_supported is not None else "unknown",
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> ModelCapabilityObservationRecord:
        return ModelCapabilityObservationRecord(
            provider_id=row["provider_id"],
            model=row["model"],
            capability=row["capability"],
            supported=bool(row["supported"]),
            provider_updated_at=row["provider_updated_at"],
            evidence_code=row["evidence_code"],
            observed_at=row["observed_at"],
        )
