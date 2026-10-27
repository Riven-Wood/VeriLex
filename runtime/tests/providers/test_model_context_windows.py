import pytest

from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.providers.models import DiscoveredContextWindow, ProbeResult
from verilex_runtime.sessions.errors import SessionDomainError
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.model_context_window_repository import (
    ModelContextWindowRepository,
)
from verilex_runtime.storage.models import ProviderKind
from verilex_runtime.storage.provider_repository import ProviderRepository


def _service(tmp_path, *, base_url="https://models.example.test/v1"):
    from verilex_runtime.providers.model_context_windows import (
        ModelContextWindowService,
    )

    database = Database(tmp_path / "db.sqlite3")
    run_migrations(database)
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO providers (
                   id, name, kind, base_url, default_model, auth_mode,
                   secret_ref, enabled, created_at, updated_at
               ) VALUES ('p1', 'P1', 'openai_compatible', ?, 'model-a', 'none',
                         NULL, 1, '2026-08-24T00:00:00Z',
                         '2026-08-24T00:00:00Z')""",
            (base_url,),
        )
    service = ModelContextWindowService(
        ProviderRepository(database),
        ModelContextWindowRepository(database),
        now=lambda: "2026-08-24T00:10:00Z",
    )
    return service


def _probe(tokens=128_000):
    hints = (
        ()
        if tokens is None
        else (DiscoveredContextWindow("model-a", tokens, "provider"),)
    )
    return ProbeResult(
        models=("model-a",),
        selected_model="model-a",
        context_windows=hints,
    )


def test_provider_manual_refresh_and_reset_follow_exact_priority(tmp_path):
    service = _service(tmp_path)
    enriched = service.enrich_probe(
        ProviderKind.OPENAI_COMPATIBLE,
        "https://models.example.test/v1",
        _probe(128_000),
    )
    service.sync("p1", enriched)
    assert service.resolve("p1", "model-a").tokens == 128_000
    assert service.resolve("p1", "model-a").source == "provider"

    service.set_override("p1", "model-a", 64_000)
    service.sync(
        "p1",
        service.enrich_probe(
            ProviderKind.OPENAI_COMPATIBLE,
            "https://models.example.test/v1",
            _probe(200_000),
        ),
    )
    manual = service.resolve("p1", "model-a")
    assert manual.tokens == 64_000
    assert manual.source == "user_declared"
    assert manual.automatic_tokens == 200_000

    reset = service.clear_override("p1", "model-a")
    assert reset.tokens == 200_000
    assert reset.source == "provider"


def test_enrich_probe_uses_exact_curated_fallback_then_unknown(tmp_path):
    service = _service(
        tmp_path, base_url="https://open.bigmodel.cn/api/paas/v4"
    )
    probe = ProbeResult(
        models=("glm-5.2", "GLM-5.2", "glm-5.3"),
        selected_model="glm-5.2",
    )

    enriched = service.enrich_probe(
        ProviderKind.OPENAI_COMPATIBLE,
        "https://open.bigmodel.cn/api/paas/v4/",
        probe,
    )

    assert [
        (hint.model, hint.tokens, hint.source) for hint in enriched.context_windows
    ] == [
        ("glm-5.2", 1_000_000, "curated"),
        ("GLM-5.2", None, "unknown"),
        ("glm-5.3", None, "unknown"),
    ]
    service.sync("p1", enriched)
    assert service.resolve("p1", "glm-5.2").source == "curated"
    assert service.resolve("p1", "glm-5.3").tokens is None
    assert service.resolve("p1", "glm-5.3").source == "unknown"


def test_reset_unknown_model_returns_to_unknown(tmp_path):
    service = _service(tmp_path)
    service.sync(
        "p1",
        service.enrich_probe(
            ProviderKind.OPENAI_COMPATIBLE,
            "https://models.example.test/v1",
            _probe(tokens=None),
        ),
    )
    service.set_override("p1", "model-a", 32_000)

    reset = service.clear_override("p1", "model-a")

    assert reset.tokens is None
    assert reset.source == "unknown"
    assert reset.is_available is True


def test_missing_provider_and_invalid_exact_model_are_rejected(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(SessionDomainError) as missing:
        service.resolve("missing", "model-a")
    assert missing.value.code == "provider_not_found"

    with pytest.raises(ProviderDomainError) as invalid:
        service.resolve("p1", "bad\nmodel")
    assert invalid.value.code == "provider_model_invalid"
