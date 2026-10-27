import pytest

from fakes import build_service_fixture
from verilex_runtime.providers.errors import ProviderDomainError, provider_error
from verilex_runtime.providers.models import ProviderDraft
from verilex_runtime.storage.models import AuthMode, ProviderKind


def api_key_draft(api_key: str = "sentinel-key") -> ProviderDraft:
    return ProviderDraft(
        name="  Compat  ",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://models.example.test/v1/",
        default_model="  model-1  ",
        auth_mode=AuthMode.API_KEY,
        api_key=api_key,
    )


def invalid_auth_draft(
    *,
    name: str,
    kind: ProviderKind,
    base_url: str,
    default_model: str,
    api_key: str = "sentinel-key",
) -> ProviderDraft:
    return ProviderDraft(
        name=name,
        kind=kind,
        base_url=base_url,
        default_model=default_model,
        auth_mode=AuthMode.API_KEY,
        api_key=api_key,
    )


def no_key_draft() -> ProviderDraft:
    return ProviderDraft(
        name="Local",
        kind=ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434/",
        default_model="model-1",
        auth_mode=AuthMode.NONE,
    )


@pytest.mark.asyncio
async def test_create_writes_secret_then_non_secret_record():
    service, repo, secrets, probe = build_service_fixture()
    probe.on_probe = lambda: secrets.calls.append(("probe", None))

    view = await service.create(api_key_draft())

    record = repo.get(view.id)
    assert record is not None
    assert record.name == "Compat"
    assert record.base_url == "https://models.example.test/v1"
    assert record.default_model == "model-1"
    assert record.secret_ref == f"provider:{view.id}"
    assert secrets.values[record.secret_ref] == "sentinel-key"
    assert secrets.calls[:3] == [
        ("ensure", None),
        ("probe", None),
        ("put", "provider:provider-1"),
    ]
    assert view.has_secret is True
    assert "sentinel-key" not in repr(view)


@pytest.mark.asyncio
async def test_create_with_probe_reuses_the_single_successful_connection_probe():
    service, repo, _secrets, probe = build_service_fixture()

    result = await service.create_with_probe(api_key_draft())

    assert result.provider.id == "provider-1"
    assert result.probe.models == ("model-1",)
    assert repo.get(result.provider.id) is not None
    assert len(probe.calls) == 1


@pytest.mark.asyncio
async def test_create_rolls_back_secret_when_repository_fails():
    service, repo, secrets, _ = build_service_fixture()
    repo.fail_next_create = True

    with pytest.raises(RuntimeError, match="repository create failed"):
        await service.create(api_key_draft())

    assert secrets.values == {}
    assert secrets.calls[-1] == ("delete", "provider:provider-1")


@pytest.mark.asyncio
async def test_create_checks_keyring_before_probe():
    service, repo, secrets, probe = build_service_fixture()
    secrets.failures.add("ensure")

    with pytest.raises(ProviderDomainError) as captured:
        await service.create(api_key_draft())

    assert captured.value.code == "secret_store_unavailable"
    assert probe.calls == []
    assert repo.records == {}


@pytest.mark.asyncio
async def test_create_probe_failure_does_not_persist_any_state():
    service, repo, secrets, probe = build_service_fixture()
    probe.error = provider_error("provider_auth_failed")

    with pytest.raises(ProviderDomainError) as captured:
        await service.create(api_key_draft())

    assert captured.value.code == "provider_auth_failed"
    assert secrets.values == {}
    assert repo.records == {}


@pytest.mark.asyncio
async def test_create_no_key_mode_never_touches_secret_store():
    service, repo, secrets, probe = build_service_fixture()

    view = await service.create(no_key_draft())

    assert view.has_secret is False
    assert repo.get(view.id) is not None
    assert secrets.calls == []
    assert probe.calls[0][1] is None


@pytest.mark.asyncio
async def test_create_compensation_failure_is_sanitized():
    service, repo, secrets, _ = build_service_fixture()
    repo.fail_next_create = True
    secrets.failures.add("delete")

    with pytest.raises(ProviderDomainError) as captured:
        await service.create(api_key_draft())

    assert captured.value.code == "secret_store_write_failed"
    assert "sentinel-key" not in str(captured.value)
    assert isinstance(captured.value.__cause__, RuntimeError)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "draft",
    [
        api_key_draft(""),
        invalid_auth_draft(
            name="   ",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-1",
        ),
        invalid_auth_draft(
            name="Compat",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="   ",
        ),
        invalid_auth_draft(
            name="Local",
            kind=ProviderKind.OLLAMA,
            base_url="http://127.0.0.1:11434",
            default_model="model-1",
        ),
    ],
)
async def test_create_rejects_invalid_auth_or_required_text_before_side_effects(draft):
    service, repo, secrets, probe = build_service_fixture()

    with pytest.raises(ValueError):
        await service.create(draft)

    assert repo.records == {}
    assert secrets.calls == []
    assert probe.calls == []


@pytest.mark.asyncio
async def test_unsaved_test_probes_without_persisting():
    service, repo, secrets, probe = build_service_fixture()

    result = await service.test(api_key_draft())

    assert result.selected_model == "model-1"
    assert probe.calls[0][1] == "sentinel-key"
    assert repo.records == {}
    assert secrets.values == {}


@pytest.mark.asyncio
async def test_saved_test_and_discovery_use_saved_secret():
    service, _, secrets, probe = build_service_fixture()
    await service.create(api_key_draft())
    secrets.calls.clear()
    probe.calls.clear()

    tested = await service.test_saved("provider-1")
    discovered = await service.discover_models("provider-1")

    assert tested.selected_model == "model-1"
    assert discovered.models == ("model-1",)
    assert [secret for _, secret in probe.calls] == ["sentinel-key", "sentinel-key"]


@pytest.mark.asyncio
async def test_list_get_and_enabled_count_map_saved_state():
    service, _, _, _ = build_service_fixture()
    created = await service.create(api_key_draft())

    assert service.list() == [created]
    assert service.get("provider-1") == created
    assert service.count_enabled() == 1


def test_get_missing_provider_raises_stable_error():
    service, _, _, _ = build_service_fixture()

    with pytest.raises(ProviderDomainError) as captured:
        service.get("missing")

    assert captured.value.code == "provider_not_found"
