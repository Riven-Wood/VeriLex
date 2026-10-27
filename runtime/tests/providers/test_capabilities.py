from dataclasses import replace

import pytest

import verilex_runtime.providers.capabilities as capability_module
from verilex_runtime.providers.capabilities import ModelCapabilityService
from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.providers.model_context_windows import ModelContextWindowService
from verilex_runtime.providers.models import (
    DiscoveredContextWindow,
    ModelCapabilities,
    ProbeResult,
)
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.errors import SessionDomainError
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.model_capability_repository import (
    ModelCapabilityRepository,
)
from verilex_runtime.storage.model_context_window_repository import (
    ModelContextWindowRepository,
)
from verilex_runtime.storage.models import (
    AuthMode,
    ModelCapabilityRecord,
    ProviderKind,
    ProviderRecord,
    ReasoningEffort,
)
from verilex_runtime.storage.provider_repository import ProviderRepository


def _provider(provider_id: str = "provider-1") -> ProviderRecord:
    return ProviderRecord(
        id=provider_id,
        name="Local Provider",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://models.example.test/v1",
        default_model="Exact-Vision",
        auth_mode=AuthMode.NONE,
        secret_ref=None,
        enabled=True,
        created_at="2026-08-14T00:00:00Z",
        updated_at="2026-08-14T00:00:00Z",
    )


def _service(tmp_path, *, now: str = "2026-08-14T01:00:00Z"):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    providers = ProviderRepository(database)
    providers.create(_provider())
    repository = ModelCapabilityRepository(database)
    context_windows = ModelContextWindowService(
        providers,
        ModelContextWindowRepository(database),
        now=lambda: now,
    )
    service = ModelCapabilityService(
        providers,
        repository,
        context_windows=context_windows,
        now=lambda: now,
    )
    return service, repository, database


def test_unknown_models_remain_unknown_without_name_heuristics(tmp_path):
    service, repository, _ = _service(tmp_path)

    assert service.resolve("provider-1", "gpt-vision-ultra") == ModelCapabilities(
        vision=None,
        native_pdf=False,
        source="unknown",
    )
    assert repository.list_by_provider("provider-1") == []
    assert service.resolve("provider-1", "gpt-vision-ultra").context_window_tokens is None
    assert service.resolve("provider-1", "gpt-vision-ultra").supported_reasoning_efforts is None


def test_context_and_reasoning_metadata_are_exact_and_independently_sourced(tmp_path):
    service, _, _ = _service(tmp_path)

    saved = service.put(
        "provider-1",
        "Exact-Reasoner",
        False,
        context_window_tokens=128_000,
        supported_reasoning_efforts=(ReasoningEffort.LOW, ReasoningEffort.HIGH),
    )

    resolved = service.resolve("provider-1", "Exact-Reasoner")
    assert saved.context_window_tokens == 128_000
    assert resolved.context_window_tokens == 128_000
    assert resolved.supported_reasoning_efforts == (
        ReasoningEffort.LOW,
        ReasoningEffort.HIGH,
    )
    assert resolved.context_window_source == "user_declared"
    assert resolved.reasoning_efforts_source == "user_declared"
    assert service.resolve("provider-1", "exact-reasoner").context_window_tokens is None


def test_context_window_row_does_not_create_a_false_vision_declaration(tmp_path):
    service, repository, database = _service(tmp_path)
    context_windows = ModelContextWindowService(
        ProviderRepository(database),
        ModelContextWindowRepository(database),
        now=lambda: "2026-08-14T02:00:00Z",
    )
    context_windows.sync(
        "provider-1",
        ProbeResult(
            models=("metadata-only",),
            selected_model="metadata-only",
            context_windows=(
                DiscoveredContextWindow("metadata-only", 200_000, "provider"),
            ),
        ),
    )

    resolved = service.resolve("provider-1", "metadata-only")

    assert resolved.vision is None
    assert resolved.context_window_tokens == 200_000
    assert resolved.context_window_source == "provider"
    assert repository.get("provider-1", "metadata-only") is None


def test_legacy_vision_only_update_preserves_context_metadata(tmp_path):
    service, _, _ = _service(tmp_path)
    original = service.put(
        "provider-1",
        "Exact-Reasoner",
        True,
        context_window_tokens=128_000,
        supported_reasoning_efforts=(ReasoningEffort.LOW, ReasoningEffort.HIGH),
    )

    updated = service.put("provider-1", "Exact-Reasoner", False)

    assert updated.supports_vision is False
    assert updated.context_window_tokens == 128_000
    assert updated.supported_reasoning_efforts == (
        ReasoningEffort.LOW,
        ReasoningEffort.HIGH,
    )
    assert updated.context_window_source == "user_declared"
    assert updated.reasoning_efforts_source == "user_declared"
    assert updated.created_at == original.created_at


def _deepseek_service(tmp_path):
    """A ModelCapabilityService whose provider maps to api.deepseek.com."""
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    providers = ProviderRepository(database)
    providers.create(
        ProviderRecord(
            id="deepseek-provider",
            name="DeepSeek",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://api.deepseek.com",
            default_model="deepseek-v4-flash",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-26T00:00:00Z",
            updated_at="2026-08-26T00:00:00Z",
        )
    )
    repository = ModelCapabilityRepository(database)
    service = ModelCapabilityService(
        providers,
        repository,
        context_windows=ModelContextWindowService(
            providers,
            ModelContextWindowRepository(database),
            now=lambda: "2026-08-26T00:00:00Z",
        ),
        now=lambda: "2026-08-26T00:00:00Z",
    )
    return service


def test_curated_deepseek_flash_is_known_non_vision(tmp_path):
    service = _deepseek_service(tmp_path)

    resolved = service.resolve("deepseek-provider", "deepseek-v4-flash")

    assert resolved.vision is False
    assert resolved.native_pdf is False
    assert resolved.source == "curated"


@pytest.mark.parametrize("invalid", [0, -1, 10_000_001, True])
def test_context_window_bounds_are_validated_before_storage(tmp_path, invalid):
    service, _, _ = _service(tmp_path)

    with pytest.raises(ValueError):
        service.put(
            "provider-1",
            "Exact-Reasoner",
            False,
            context_window_tokens=invalid,
        )


def test_invalid_unicode_is_rejected_before_storage_encoding(tmp_path):
    service, _, _ = _service(tmp_path)

    with pytest.raises(ProviderDomainError) as captured:
        service.resolve("provider-1", "bad\ud800model")

    assert captured.value.code == "provider_model_invalid"


def test_user_declaration_has_priority_and_matching_is_case_sensitive(tmp_path):
    service, repository, _ = _service(tmp_path)
    repository.put(
        ModelCapabilityRecord(
            provider_id="provider-1",
            model="Exact-Vision",
            supports_vision=True,
            supports_native_pdf=False,
            source="user_declared",
            created_at="2026-08-14T00:00:00Z",
            updated_at="2026-08-14T00:00:00Z",
        )
    )

    assert service.resolve("provider-1", "  Exact-Vision  ").vision is True
    assert service.resolve("provider-1", "exact-vision") == ModelCapabilities()


def test_curated_capability_is_exact_and_is_not_persisted(tmp_path, monkeypatch):
    service, repository, _ = _service(tmp_path)
    monkeypatch.setitem(
        capability_module._CURATED_EXACT_CAPABILITIES,
        (
            ProviderKind.OPENAI_COMPATIBLE,
            "https://models.example.test/v1",
            "Curated-Vision",
        ),
        ModelCapabilities(vision=True, native_pdf=True, source="curated"),
    )

    assert service.resolve("provider-1", "Curated-Vision") == ModelCapabilities(
        vision=True,
        native_pdf=False,
        source="curated",
    )
    assert service.resolve("provider-1", "curated-vision") == ModelCapabilities()
    assert repository.list_by_provider("provider-1") == []


def test_explicit_false_user_declaration_overrides_curated_true(
    tmp_path,
    monkeypatch,
):
    service, _, _ = _service(tmp_path)
    monkeypatch.setitem(
        capability_module._CURATED_EXACT_CAPABILITIES,
        (
            ProviderKind.OPENAI_COMPATIBLE,
            "https://models.example.test/v1",
            "Curated-Vision",
        ),
        ModelCapabilities(vision=True, native_pdf=False, source="curated"),
    )

    service.put("provider-1", "Curated-Vision", False)

    assert service.resolve("provider-1", "Curated-Vision") == ModelCapabilities(
        vision=False,
        native_pdf=False,
        source="user_declared",
    )


@pytest.mark.parametrize(
    "model",
    ["", "   ", "x" * 201, "bad\x00model", "bad\nmodel", "bad\ud800model"],
)
def test_model_validation_rejects_empty_or_more_than_200_unicode_characters(
    tmp_path,
    model,
):
    service, _, _ = _service(tmp_path)

    with pytest.raises(ProviderDomainError) as captured:
        service.resolve("provider-1", model)

    assert captured.value.code == "provider_model_invalid"


def test_model_validation_accepts_exactly_200_unicode_characters(tmp_path):
    service, _, _ = _service(tmp_path)

    assert service.resolve("provider-1", "法" * 200) == ModelCapabilities()


def test_model_normalization_preserves_internal_spacing_and_unicode_form(tmp_path):
    service, _, _ = _service(tmp_path)
    decomposed = "Cafe\u0301  Model"

    saved = service.put("provider-1", f"  {decomposed}  ", True)

    assert saved.model == decomposed
    assert service.resolve("provider-1", "Café  Model") == ModelCapabilities()


def test_public_model_validator_returns_the_exact_normalized_name(tmp_path):
    service, _, _ = _service(tmp_path)

    assert service.validate_model("  Exact Model  ") == "Exact Model"
    with pytest.raises(ProviderDomainError) as captured:
        service.validate_model("bad\nmodel")
    assert captured.value.code == "provider_model_invalid"


def test_missing_provider_always_uses_provider_not_found(tmp_path):
    service, _, _ = _service(tmp_path)

    with pytest.raises(SessionDomainError) as captured:
        service.resolve("missing", "model")

    assert captured.value.code == "provider_not_found"


def test_disabled_provider_remains_configurable(tmp_path):
    service, _, database = _service(tmp_path)
    providers = ProviderRepository(database)
    providers.update(replace(_provider(), enabled=False))

    saved = service.put("provider-1", "disabled-model", True)

    assert saved.supports_vision is True
    assert service.resolve("provider-1", "disabled-model").vision is True


def test_put_uses_user_declared_source_and_preserves_created_at(tmp_path):
    service, _, _ = _service(tmp_path)

    created = service.put("provider-1", "  Exact-Vision  ", True)
    assert created.model == "Exact-Vision"
    assert created.supports_vision is True
    assert created.supports_native_pdf is False
    assert created.source == "user_declared"
    assert created.created_at == "2026-08-14T01:00:00Z"

    restarted = ModelCapabilityService(
        ProviderRepository(Database(tmp_path / "verilex.sqlite3")),
        ModelCapabilityRepository(Database(tmp_path / "verilex.sqlite3")),
        now=lambda: "2026-08-14T02:00:00Z",
    )
    updated = restarted.put("provider-1", "Exact-Vision", False)
    assert updated.created_at == "2026-08-14T01:00:00Z"
    assert updated.updated_at == "2026-08-14T02:00:00Z"
    assert restarted.resolve("provider-1", "Exact-Vision").vision is False


def test_list_is_persistent_exact_and_checks_provider_existence(tmp_path):
    service, _, _ = _service(tmp_path)
    service.put("provider-1", "Beta", True)
    service.put("provider-1", "alpha", False)

    restarted = ModelCapabilityService(
        ProviderRepository(Database(tmp_path / "verilex.sqlite3")),
        ModelCapabilityRepository(Database(tmp_path / "verilex.sqlite3")),
    )
    assert [record.model for record in restarted.list("provider-1")] == [
        "Beta",
        "alpha",
    ]

    with pytest.raises(SessionDomainError) as captured:
        restarted.list("missing")
    assert captured.value.code == "provider_not_found"


def test_resolve_preserves_a_persisted_curated_source(tmp_path):
    service, repository, _ = _service(tmp_path)
    repository.put(
        ModelCapabilityRecord(
            provider_id="provider-1",
            model="curated-exact",
            supports_vision=True,
            supports_native_pdf=True,
            source="curated",
            created_at="2026-08-14T00:00:00Z",
            updated_at="2026-08-14T00:00:00Z",
        )
    )

    assert service.resolve("provider-1", "curated-exact") == ModelCapabilities(
        vision=True,
        native_pdf=False,
        source="curated",
    )


def test_list_never_exposes_native_pdf_support_in_the_first_release(tmp_path):
    service, repository, _ = _service(tmp_path)
    repository.put(
        ModelCapabilityRecord(
            provider_id="provider-1",
            model="legacy-native-pdf",
            supports_vision=False,
            supports_native_pdf=True,
            source="user_declared",
            created_at="2026-08-14T00:00:00Z",
            updated_at="2026-08-14T00:00:00Z",
        )
    )

    assert service.list("provider-1")[0].supports_native_pdf is False


def test_put_audits_created_and_updated_without_provider_configuration(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    providers = ProviderRepository(database)
    providers.create(_provider())
    audit_repository = AuditRepository(database)
    service = ModelCapabilityService(
        providers,
        ModelCapabilityRepository(database),
        now=lambda: "2026-08-14T01:00:00Z",
        audit=AuditRecorder(audit_repository),
    )

    service.put("provider-1", "Exact-Vision", True)
    service.put("provider-1", "Exact-Vision", False)

    entries = audit_repository.list_recent()
    assert [entry.outcome for entry in entries] == ["updated", "created"]
    for entry in entries:
        assert entry.action == "model_capability"
        assert entry.subject == "provider-1"
        assert set(entry.details) == {"model", "supportsVision"}
        assert entry.details["model"] == "Exact-Vision"
        serialized = repr(entry)
        assert "models.example.test" not in serialized
        assert "request" not in serialized
