import asyncio

import pytest

from fakes import MemoryProviderRepository, MemorySecretStore, build_service_fixture
from test_service_create import api_key_draft, no_key_draft
from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.providers.models import (
    ProbeResult,
    ProviderDraft,
    ProviderUpdate,
    SecretAction,
)
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    AuthMode,
    ProviderKind,
    ProviderRecord,
)
from verilex_runtime.storage.provider_repository import ProviderRepository


async def saved_key_provider():
    fixture = build_service_fixture()
    service, _, secrets, probe = fixture
    await service.create(api_key_draft("old-sentinel-key"))
    secrets.calls.clear()
    probe.calls.clear()
    return fixture


def replace_key_update(
    new_key: str = "new-sentinel-key",
    *,
    base_url: str | None = None,
    auth_mode: AuthMode | None = None,
) -> ProviderUpdate:
    return ProviderUpdate(
        base_url=base_url,
        auth_mode=auth_mode,
        secret_action=SecretAction.REPLACE,
        api_key=new_key,
    )


@pytest.mark.asyncio
async def test_update_name_and_enabled_without_probing():
    service, repo, secrets, probe = await saved_key_provider()

    view = await service.update(
        "provider-1",
        ProviderUpdate(name="  Renamed  ", enabled=False),
    )

    assert view.name == "Renamed"
    assert view.enabled is False
    assert probe.calls == []
    assert secrets.values["provider:provider-1"] == "old-sentinel-key"
    assert repo.get("provider-1") is not None


@pytest.mark.asyncio
async def test_update_url_and_model_probes_with_preserved_secret():
    service, _, secrets, probe = await saved_key_provider()

    view = await service.update(
        "provider-1",
        ProviderUpdate(
            base_url="https://new.example.test/v1/",
            default_model="  model-2  ",
        ),
    )

    assert view.base_url == "https://new.example.test/v1"
    assert view.default_model == "model-2"
    assert probe.calls[0][1] == "old-sentinel-key"
    assert secrets.values["provider:provider-1"] == "old-sentinel-key"


@pytest.mark.asyncio
async def test_update_replaces_key_after_probing_new_configuration():
    service, repo, secrets, probe = await saved_key_provider()

    await service.update(
        "provider-1",
        replace_key_update(),
    )

    assert probe.calls[0][1] == "new-sentinel-key"
    record = repo.get("provider-1")
    assert record is not None
    assert record.secret_ref != "provider:provider-1"
    assert secrets.values == {record.secret_ref: "new-sentinel-key"}


@pytest.mark.asyncio
async def test_interrupted_key_replacement_never_repoints_old_config_to_new_key():
    class SimulatedProcessExit(BaseException):
        pass

    class InterruptingRepository(MemoryProviderRepository):
        interrupt_update = False

        def update(self, record):
            if self.interrupt_update:
                raise SimulatedProcessExit
            return super().update(record)

    repository = InterruptingRepository()
    secrets = MemorySecretStore()
    _, _, _, probes = build_service_fixture()
    service = ProviderService(
        repository,
        secrets,
        probes,
        id_factory=lambda: "provider-1",
        now=lambda: "2026-08-04T00:00:00Z",
    )
    await service.create(api_key_draft("old-sentinel-key"))
    original = repository.get("provider-1")
    repository.interrupt_update = True

    with pytest.raises(SimulatedProcessExit):
        await service.update(
            "provider-1",
            replace_key_update(base_url="https://new.example.test/v1"),
        )

    assert repository.get("provider-1") == original
    assert original is not None
    assert secrets.values[original.secret_ref] == "old-sentinel-key"


@pytest.mark.asyncio
async def test_update_to_no_key_deletes_old_secret():
    service, repo, secrets, probe = await saved_key_provider()

    view = await service.update(
        "provider-1",
        ProviderUpdate(
            auth_mode=AuthMode.NONE,
            secret_action=SecretAction.REMOVE,
        ),
    )

    assert probe.calls[0][1] is None
    assert secrets.values == {}
    assert view.auth_mode is AuthMode.NONE
    assert view.has_secret is False
    assert repo.get("provider-1").secret_ref is None


@pytest.mark.asyncio
async def test_update_repository_failure_restores_old_key_and_record():
    service, repo, secrets, _ = await saved_key_provider()
    original = repo.get("provider-1")
    repo.fail_next_update = True

    with pytest.raises(RuntimeError, match="repository update failed"):
        await service.update(
            "provider-1",
            replace_key_update(),
        )

    assert repo.get("provider-1") == original
    assert secrets.values["provider:provider-1"] == "old-sentinel-key"


@pytest.mark.asyncio
async def test_update_repository_failure_restores_deleted_key():
    service, repo, secrets, _ = await saved_key_provider()
    original = repo.get("provider-1")
    repo.fail_next_update = True

    with pytest.raises(RuntimeError, match="repository update failed"):
        await service.update(
            "provider-1",
            ProviderUpdate(
                auth_mode=AuthMode.NONE,
                secret_action=SecretAction.REMOVE,
            ),
        )

    assert repo.get("provider-1") == original
    assert secrets.values["provider:provider-1"] == "old-sentinel-key"


@pytest.mark.asyncio
async def test_interrupted_key_removal_preserves_the_referenced_key():
    class SimulatedProcessExit(BaseException):
        pass

    class InterruptingRepository(MemoryProviderRepository):
        interrupt_update = False

        def update(self, record):
            if self.interrupt_update:
                raise SimulatedProcessExit
            return super().update(record)

    repository = InterruptingRepository()
    secrets = MemorySecretStore()
    _, _, _, probes = build_service_fixture()
    service = ProviderService(
        repository,
        secrets,
        probes,
        id_factory=lambda: "provider-1",
        now=lambda: "2026-08-04T00:00:00Z",
    )
    await service.create(api_key_draft("old-sentinel-key"))
    original = repository.get("provider-1")
    repository.interrupt_update = True

    with pytest.raises(SimulatedProcessExit):
        await service.update(
            "provider-1",
            ProviderUpdate(
                auth_mode=AuthMode.NONE,
                secret_action=SecretAction.REMOVE,
            ),
        )

    assert repository.get("provider-1") == original
    assert original is not None
    assert secrets.values[original.secret_ref] == "old-sentinel-key"


@pytest.mark.asyncio
async def test_key_removal_failure_leaves_a_safe_no_key_record():
    service, repo, secrets, _ = await saved_key_provider()
    secrets.failures.add("delete")

    with pytest.raises(ProviderDomainError) as captured:
        await service.update(
            "provider-1",
            ProviderUpdate(
                auth_mode=AuthMode.NONE,
                secret_action=SecretAction.REMOVE,
            ),
        )

    assert captured.value.code == "secret_store_write_failed"
    record = repo.get("provider-1")
    assert record is not None
    assert record.auth_mode is AuthMode.NONE
    assert record.secret_ref is None
    assert secrets.values["provider:provider-1"] == "old-sentinel-key"


@pytest.mark.asyncio
async def test_update_compensation_failure_returns_sanitized_secret_error():
    service, repo, secrets, _ = await saved_key_provider()
    repo.fail_next_update = True
    secrets.failures.add("delete")

    with pytest.raises(ProviderDomainError) as captured:
        await service.update(
            "provider-1",
            replace_key_update(),
        )

    assert captured.value.code == "secret_store_write_failed"
    assert "old-sentinel-key" not in str(captured.value)
    assert isinstance(captured.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_update_no_key_provider_does_not_touch_secret_store():
    service, _, secrets, probe = build_service_fixture()
    await service.create(no_key_draft())
    secrets.calls.clear()
    probe.calls.clear()

    await service.update("provider-1", ProviderUpdate(name="Renamed"))

    assert secrets.calls == []
    assert probe.calls == []


@pytest.mark.asyncio
async def test_update_from_no_key_checks_keyring_before_probe():
    service, repo, secrets, probe = build_service_fixture()
    await service.create(
        ProviderDraft(
            name="No Key Compat",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-1",
            auth_mode=AuthMode.NONE,
        )
    )
    secrets.calls.clear()
    probe.calls.clear()
    secrets.failures.add("ensure")

    with pytest.raises(ProviderDomainError) as captured:
        await service.update(
            "provider-1",
            replace_key_update(auth_mode=AuthMode.API_KEY),
        )

    assert captured.value.code == "secret_store_unavailable"
    assert probe.calls == []
    assert repo.get("provider-1").auth_mode is AuthMode.NONE


@pytest.mark.asyncio
async def test_update_detects_concurrent_row_deletion_and_removes_replacement_key(
    tmp_path,
):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repository = ProviderRepository(database)

    class DeleteBeforeUpdateRepository:
        def __init__(self, target):
            self._target = target
            self.delete_before_update = False

        def __getattr__(self, name):
            return getattr(self._target, name)

        def update(self, record):
            if self.delete_before_update:
                self._target.delete(record.id)
            return self._target.update(record)

    interleaved_repository = DeleteBeforeUpdateRepository(repository)
    _, _, secrets, probes = build_service_fixture()
    service = ProviderService(
        interleaved_repository,
        secrets,
        probes,
        id_factory=lambda: "provider-1",
        now=lambda: "2026-08-04T00:00:00Z",
    )
    await service.create(api_key_draft("old-sentinel-key"))
    interleaved_repository.delete_before_update = True

    with pytest.raises(ProviderDomainError) as captured:
        await service.update(
            "provider-1",
            replace_key_update(),
        )

    assert captured.value.code == "provider_not_found"
    assert repository.get("provider-1") is None
    assert secrets.values == {}


@pytest.mark.asyncio
async def test_concurrent_updates_preserve_both_successful_changes():
    class GateProbe:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def probe(self, draft, api_key):
            del api_key
            self.started.set()
            await self.release.wait()
            return ProbeResult((draft.default_model,), draft.default_model)

    repository = MemoryProviderRepository()
    repository.records["provider-1"] = ProviderRecord(
        id="provider-1",
        name="Original name",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://old.example.test/v1",
        default_model="model-1",
        auth_mode=AuthMode.NONE,
        secret_ref=None,
        enabled=True,
        created_at="2026-08-04T00:00:00Z",
        updated_at="2026-08-04T00:00:00Z",
    )
    probe = GateProbe()
    timestamps = iter(
        ["2026-08-04T00:00:01Z", "2026-08-04T00:00:02Z"]
    )
    service = ProviderService(
        repository,
        MemorySecretStore(),
        probe,
        now=lambda: next(timestamps),
    )

    url_update = asyncio.create_task(
        service.update(
            "provider-1",
            ProviderUpdate(base_url="https://new.example.test/v1"),
        )
    )
    await probe.started.wait()
    name_update = asyncio.create_task(
        service.update("provider-1", ProviderUpdate(name="New name"))
    )
    await asyncio.sleep(0)
    probe.release.set()
    await asyncio.gather(url_update, name_update)

    record = repository.get("provider-1")
    assert record is not None
    assert record.name == "New name"
    assert record.base_url == "https://new.example.test/v1"


@pytest.mark.asyncio
async def test_independent_services_preserve_concurrent_updates(tmp_path):
    class GateProbe:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def probe(self, draft, api_key):
            del api_key
            self.started.set()
            await self.release.wait()
            return ProbeResult((draft.default_model,), draft.default_model)

    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    first_repository = ProviderRepository(database)
    second_repository = ProviderRepository(database)
    first_repository.create(
        ProviderRecord(
            id="provider-1",
            name="Original name",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://old.example.test/v1",
            default_model="model-1",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-04T00:00:00Z",
            updated_at="2026-08-04T00:00:00Z",
        )
    )
    probe = GateProbe()
    secrets = MemorySecretStore()
    first_service = ProviderService(
        first_repository,
        secrets,
        probe,
        now=lambda: "2026-08-04T00:00:01Z",
    )
    second_service = ProviderService(
        second_repository,
        secrets,
        probe,
        now=lambda: "2026-08-04T00:00:02Z",
    )

    url_update = asyncio.create_task(
        first_service.update(
            "provider-1",
            ProviderUpdate(base_url="https://new.example.test/v1"),
        )
    )
    await probe.started.wait()
    name_update = asyncio.create_task(
        second_service.update("provider-1", ProviderUpdate(name="New name"))
    )
    await asyncio.sleep(0)
    probe.release.set()
    await asyncio.gather(url_update, name_update)

    record = first_repository.get("provider-1")
    assert record is not None
    assert record.name == "New name"
    assert record.base_url == "https://new.example.test/v1"
