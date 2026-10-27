import pytest

from fakes import build_service_fixture
from test_service_create import api_key_draft, no_key_draft
from verilex_runtime.providers.errors import ProviderDomainError, provider_error


async def saved_key_provider():
    fixture = build_service_fixture()
    service, repo, secrets, _ = fixture
    await service.create(api_key_draft("old-sentinel-key"))
    repo.calls.clear()
    secrets.calls.clear()
    return fixture


@pytest.mark.asyncio
async def test_delete_removes_secret_before_repository_row():
    service, repo, secrets, _ = await saved_key_provider()
    operations = []
    original_delete = secrets.delete

    def delete_secret(secret_ref):
        operations.append("secret")
        original_delete(secret_ref)

    secrets.delete = delete_secret
    repo_delete = repo.delete

    def delete_record(provider_id):
        operations.append("repository")
        repo_delete(provider_id)

    repo.delete = delete_record

    service.delete("provider-1")

    assert operations == ["secret", "repository"]
    assert secrets.values == {}
    assert repo.get("provider-1") is None


@pytest.mark.asyncio
async def test_delete_secret_failure_preserves_repository_row():
    service, repo, secrets, _ = await saved_key_provider()
    secrets.failures.add("delete")

    with pytest.raises(ProviderDomainError) as captured:
        service.delete("provider-1")

    assert captured.value.code == "secret_store_write_failed"
    assert repo.get("provider-1") is not None
    assert secrets.values["provider:provider-1"] == "old-sentinel-key"


@pytest.mark.asyncio
async def test_delete_repository_failure_restores_key():
    service, repo, secrets, _ = await saved_key_provider()
    repo.fail_next_delete = True

    with pytest.raises(RuntimeError, match="repository delete failed"):
        service.delete("provider-1")

    assert repo.get("provider-1") is not None
    assert secrets.values["provider:provider-1"] == "old-sentinel-key"


@pytest.mark.asyncio
async def test_delete_missing_provider_raises_stable_error():
    service, _, secrets, _ = build_service_fixture()

    with pytest.raises(ProviderDomainError) as captured:
        service.delete("missing")

    assert captured.value.code == "provider_not_found"
    assert secrets.calls == []


@pytest.mark.asyncio
async def test_delete_provider_in_use_preserves_both_stores():
    service, repo, secrets, _ = await saved_key_provider()
    repo.delete_error = provider_error("provider_in_use")

    with pytest.raises(ProviderDomainError) as captured:
        service.delete("provider-1")

    assert captured.value.code == "provider_in_use"
    assert repo.get("provider-1") is not None
    assert secrets.values["provider:provider-1"] == "old-sentinel-key"


@pytest.mark.asyncio
async def test_delete_no_key_provider_never_touches_secret_store():
    service, repo, secrets, _ = build_service_fixture()
    await service.create(no_key_draft())
    secrets.calls.clear()

    service.delete("provider-1")

    assert repo.get("provider-1") is None
    assert secrets.calls == []


@pytest.mark.asyncio
async def test_delete_compensation_failure_is_sanitized():
    service, repo, secrets, _ = await saved_key_provider()
    repo.fail_next_delete = True
    secrets.failures.add("put")

    with pytest.raises(ProviderDomainError) as captured:
        service.delete("provider-1")

    assert captured.value.code == "secret_store_write_failed"
    assert "old-sentinel-key" not in str(captured.value)
    assert isinstance(captured.value.__cause__, RuntimeError)
