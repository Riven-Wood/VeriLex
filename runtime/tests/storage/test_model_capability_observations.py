from dataclasses import replace

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.model_capability_observation_repository import (
    ModelCapabilityObservationRepository,
)
from verilex_runtime.storage.model_capability_repository import ModelCapabilityRepository
from verilex_runtime.storage.models import (
    ModelCapabilityObservationRecord,
    ModelCapabilityRecord,
)


NOW = "2026-09-01T00:00:00Z"


def _repositories(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO providers
               (id,name,kind,base_url,default_model,auth_mode,secret_ref,enabled,created_at,updated_at)
               VALUES ('provider-1','Provider','ollama','http://localhost','model','none',NULL,1,?,?)""",
            (NOW, NOW),
        )
    return (
        database,
        ModelCapabilityRepository(database),
        ModelCapabilityObservationRepository(database),
    )


def test_runtime_observation_does_not_overwrite_user_declaration(tmp_path):
    _, declarations, observations = _repositories(tmp_path)
    declared = ModelCapabilityRecord(
        provider_id="provider-1",
        model="model",
        supports_vision=True,
        supports_native_pdf=False,
        source="user_declared",
        created_at=NOW,
        updated_at=NOW,
    )
    declarations.put(declared)
    observation = ModelCapabilityObservationRecord(
        provider_id="provider-1",
        model="model",
        capability="vision",
        supported=False,
        provider_updated_at=NOW,
        evidence_code="provider_rejected_image",
        observed_at=NOW,
    )

    assert observations.put(observation) == observation
    assert declarations.get("provider-1", "model") == declared
    resolved = observations.resolve(
        provider_id="provider-1",
        model="model",
        capability="vision",
        declared_supported=True,
    )
    assert (resolved.supported, resolved.source) == (False, "runtime_observation")


def test_provider_revision_change_makes_observation_stale(tmp_path):
    database, _, observations = _repositories(tmp_path)
    observation = ModelCapabilityObservationRecord(
        provider_id="provider-1",
        model="model",
        capability="vision",
        supported=False,
        provider_updated_at=NOW,
        evidence_code="provider_rejected_image",
        observed_at=NOW,
    )
    observations.put(observation)
    with database.connect() as connection:
        connection.execute(
            "UPDATE providers SET updated_at='2026-09-01T01:00:00Z' WHERE id='provider-1'"
        )

    assert observations.get_current("provider-1", "model", "vision") is None
    resolved = observations.resolve(
        provider_id="provider-1",
        model="model",
        capability="vision",
        declared_supported=True,
    )
    assert (resolved.supported, resolved.source) == (True, "user_declared")


def test_observation_upsert_is_exact_by_provider_model_and_capability(tmp_path):
    _, _, observations = _repositories(tmp_path)
    original = ModelCapabilityObservationRecord(
        provider_id="provider-1",
        model="model",
        capability="vision",
        supported=False,
        provider_updated_at=NOW,
        evidence_code="provider_rejected_image",
        observed_at=NOW,
    )
    changed = replace(original, supported=True, evidence_code="image_accepted")
    observations.put(original)
    assert observations.put(changed) == changed
    assert observations.get_current("provider-1", "model", "vision") == changed
    assert observations.get_current("provider-1", "MODEL", "vision") is None


def test_stale_runtime_observation_restores_curated_source(tmp_path):
    database, _, observations = _repositories(tmp_path)
    observation = ModelCapabilityObservationRecord(
        provider_id="provider-1",
        model="model",
        capability="vision",
        supported=False,
        provider_updated_at=NOW,
        evidence_code="provider_rejected_image",
        observed_at=NOW,
    )
    observations.put(observation)
    with database.connect() as connection:
        connection.execute(
            "UPDATE providers SET updated_at='2026-09-01T01:00:00Z' WHERE id='provider-1'"
        )
    resolved = observations.resolve(
        provider_id="provider-1",
        model="model",
        capability="vision",
        declared_supported=True,
        declared_source="curated",
    )
    assert (resolved.supported, resolved.source) == (True, "curated")
