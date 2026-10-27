from verilex_runtime.storage.models import ProviderKind


def _lookup(kind: ProviderKind, base_url: str, model: str):
    from verilex_runtime.providers.model_catalog import curated_context_window

    return curated_context_window(kind, base_url, model)


def test_catalog_returns_exact_official_model_value_and_source_metadata():
    result = _lookup(
        ProviderKind.OPENAI_COMPATIBLE,
        "https://open.bigmodel.cn/api/paas/v4",
        "glm-5.2",
    )

    assert result is not None
    assert result.tokens == 1_000_000
    assert result.source_url == "https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2"
    assert result.verified_on == "2026-08-24"


def test_catalog_normalizes_only_a_trailing_base_url_slash():
    result = _lookup(
        ProviderKind.OPENAI_COMPATIBLE,
        "https://open.bigmodel.cn/api/paas/v4/",
        "glm-5.1",
    )
    assert result is not None
    assert result.tokens == 200_000


def test_catalog_never_guesses_model_family_case_or_provider():
    base_url = "https://open.bigmodel.cn/api/paas/v4"

    assert _lookup(ProviderKind.OPENAI_COMPATIBLE, base_url, "GLM-5.2") is None
    assert _lookup(ProviderKind.OPENAI_COMPATIBLE, base_url, "glm-5.3") is None
    assert _lookup(ProviderKind.OLLAMA, base_url, "glm-5.2") is None
    assert _lookup(
        ProviderKind.OPENAI_COMPATIBLE,
        "https://different.example.test/v4",
        "glm-5.2",
    ) is None


def test_catalog_keeps_coding_endpoint_separate_and_exact():
    result = _lookup(
        ProviderKind.OPENAI_COMPATIBLE,
        "https://open.bigmodel.cn/api/coding/paas/v4",
        "glm-5.2",
    )
    assert result is not None
    assert result.tokens == 1_000_000
