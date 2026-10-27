import httpx
import pytest

from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.providers.models import ProviderDraft, ProbeResult
from verilex_runtime.providers.ollama import OllamaProbe
from verilex_runtime.storage.models import AuthMode, ProviderKind


def draft(
    model: str = "qwen3:8b",
    base_url: str = "http://127.0.0.1:11434",
) -> ProviderDraft:
    return ProviderDraft(
        name="Local Ollama",
        kind=ProviderKind.OLLAMA,
        base_url=base_url,
        default_model=model,
        auth_mode=AuthMode.NONE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    ["http://127.0.0.1:11434", "http://127.0.0.1:11434/v1"],
)
async def test_probe_reads_root_tags_and_sorts_unique_model_names(base_url: str):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"] == {
            "connect": 5.0,
            "read": 20.0,
            "write": 20.0,
            "pool": 20.0,
        }
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {}})
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "qwen3:8b"},
                    {"name": "llama3.2:latest"},
                    {"name": "qwen3:8b"},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await OllamaProbe(client).probe(draft(base_url=base_url), None)

    assert result == ProbeResult(
        models=("llama3.2:latest", "qwen3:8b"),
        selected_model="qwen3:8b",
    )


@pytest.mark.asyncio
async def test_probe_isolates_show_failures_and_returns_valid_context_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "gemma3:latest"},
                        {"name": "broken:latest"},
                    ]
                },
            )
        assert request.url.path == "/api/show"
        requested = request.read().decode()
        if '"model":"gemma3:latest"' in requested:
            return httpx.Response(
                200,
                json={"model_info": {"gemma3.context_length": 131_072}},
            )
        return httpx.Response(500, json={"error": "unavailable"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await OllamaProbe(client).probe(draft(model="gemma3:latest"), None)

    assert result.models == ("broken:latest", "gemma3:latest")
    assert [
        (hint.model, hint.tokens, hint.source) for hint in result.context_windows
    ] == [("gemma3:latest", 131_072, "provider")]


@pytest.mark.asyncio
async def test_probe_selects_the_first_model_when_discovering_without_a_selection():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "models": [
                    {"name": "qwen3:8b"},
                    {"name": "llama3.2:latest"},
                ]
            },
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await OllamaProbe(client).probe(draft(model=""), None)

    assert result == ProbeResult(
        models=("llama3.2:latest", "qwen3:8b"),
        selected_model="llama3.2:latest",
    )


@pytest.mark.asyncio
async def test_probe_rejects_empty_model_directory():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"models": []})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ProviderDomainError) as captured:
            await OllamaProbe(client).probe(draft(), None)

    assert captured.value.code == "provider_has_no_models"


@pytest.mark.asyncio
async def test_probe_rejects_missing_selected_model():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"models": [{"name": "llama3.2:latest"}]},
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ProviderDomainError) as captured:
            await OllamaProbe(client).probe(draft(), None)

    assert captured.value.code == "provider_model_not_found"
