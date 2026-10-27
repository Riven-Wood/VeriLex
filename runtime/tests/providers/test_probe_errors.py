import traceback
from dataclasses import replace

import httpx
import pytest

from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.providers.models import ProviderDraft, ProbeResult
from verilex_runtime.providers.ollama import OllamaProbe
from verilex_runtime.providers.openai_compatible import OpenAICompatibleProbe
from verilex_runtime.providers.probe_router import ProviderProbeRouter
from verilex_runtime.storage.models import AuthMode, ProviderKind


_MEBIBYTE = 1024 * 1024


class CountingResponseStream(httpx.AsyncByteStream):
    def __init__(self, chunks: int) -> None:
        self._chunks = chunks
        self._yielded = 0
        self.bytes_yielded = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._yielded >= self._chunks:
            raise StopAsyncIteration
        self._yielded += 1
        chunk = b"x" * _MEBIBYTE
        self.bytes_yielded += len(chunk)
        return chunk

    async def aclose(self) -> None:
        pass


def openai_draft() -> ProviderDraft:
    return ProviderDraft(
        name="Compat",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://models.example.test/v1",
        default_model="deepseek-chat",
        auth_mode=AuthMode.API_KEY,
    )


def ollama_draft() -> ProviderDraft:
    return ProviderDraft(
        name="Local Ollama",
        kind=ProviderKind.OLLAMA,
        base_url="http://127.0.0.1:11434",
        default_model="qwen3:8b",
        auth_mode=AuthMode.NONE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_type", ["openai", "ollama"])
@pytest.mark.parametrize("status", [401, 403])
async def test_probe_maps_auth_status_without_leaking_response_body(
    probe_type: str,
    status: int,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="rejected sentinel-key")

    error, requested_paths = await capture_probe_error(probe_type, handler)

    assert error.code == "provider_auth_failed"
    assert "sentinel-key" not in str(error)
    assert len(requested_paths) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_type", ["openai", "ollama"])
async def test_probe_maps_rate_limit(probe_type: str):
    error, requested_paths = await capture_probe_error(
        probe_type,
        lambda request: httpx.Response(429, text="limited sentinel-key"),
    )

    assert error.code == "provider_rate_limited"
    assert "sentinel-key" not in str(error)
    assert len(requested_paths) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_type", ["openai", "ollama"])
async def test_probe_maps_timeout(probe_type: str):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out sentinel-key", request=request)

    error, requested_paths = await capture_probe_error(probe_type, handler)

    assert error.code == "provider_timeout"
    assert "sentinel-key" not in str(error)
    assert len(requested_paths) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_type", ["openai", "ollama"])
async def test_probe_maps_connect_error(probe_type: str):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline sentinel-key", request=request)

    error, requested_paths = await capture_probe_error(probe_type, handler)

    assert error.code == "provider_unreachable"
    assert "sentinel-key" not in str(error)
    assert len(requested_paths) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_type", ["openai", "ollama"])
@pytest.mark.parametrize("failure_kind", ["response", "timeout", "connect"])
async def test_probe_error_traceback_omits_upstream_details(
    probe_type: str,
    failure_kind: str,
):
    upstream_marker = "TRACE_ONLY_PROVIDER_SENTINEL_42"

    def handler(request: httpx.Request) -> httpx.Response:
        if failure_kind == "response":
            return httpx.Response(401, text=f"rejected {upstream_marker}")
        if failure_kind == "timeout":
            raise httpx.ReadTimeout(
                f"timed out {upstream_marker}",
                request=request,
            )
        raise httpx.ConnectError(
            f"offline {upstream_marker}",
            request=request,
        )

    error, _ = await capture_probe_error(probe_type, handler)
    formatted = "".join(traceback.format_exception(error))

    assert upstream_marker not in formatted


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_type", ["openai", "ollama"])
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"unexpected": []}),
    ],
)
async def test_probe_maps_malformed_json_or_schema(
    probe_type: str,
    response: httpx.Response,
):
    error, requested_paths = await capture_probe_error(
        probe_type,
        lambda request: response,
    )

    assert error.code == "provider_protocol_error"
    assert len(requested_paths) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_type", ["openai", "ollama"])
async def test_probe_stops_reading_an_oversized_upstream_response(probe_type: str):
    stream = CountingResponseStream(chunks=8)
    error, requested_paths = await capture_probe_error(
        probe_type,
        lambda request: httpx.Response(200, stream=stream),
    )

    assert error.code == "provider_protocol_error"
    assert requested_paths == [
        "/v1/models" if probe_type == "openai" else "/api/tags"
    ]
    assert stream.bytes_yielded <= 5 * _MEBIBYTE


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_type", ["openai", "ollama"])
async def test_probe_does_not_follow_upstream_redirects(probe_type: str):
    error, requested_paths = await capture_probe_error(
        probe_type,
        lambda request: httpx.Response(
            307,
            headers={"Location": "http://127.0.0.1:9/private"},
        ),
    )

    assert error.code == "provider_protocol_error"
    assert requested_paths == [
        "/v1/models" if probe_type == "openai" else "/api/tags"
    ]


async def capture_probe_error(
    probe_type,
    handler,
) -> tuple[ProviderDomainError, list[str]]:
    requested_paths: list[str] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return handler(request)

    transport = httpx.MockTransport(recording_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        probe = (
            OpenAICompatibleProbe(client)
            if probe_type == "openai"
            else OllamaProbe(client)
        )
        draft = openai_draft() if probe_type == "openai" else ollama_draft()
        with pytest.raises(ProviderDomainError) as captured:
            await probe.probe(draft, "sentinel-key")
    return captured.value, requested_paths


class RecordingProbe:
    def __init__(self, result: ProbeResult):
        self.result = result
        self.calls: list[tuple[ProviderDraft, str | None]] = []

    async def probe(
        self,
        draft: ProviderDraft,
        api_key: str | None,
    ) -> ProbeResult:
        self.calls.append((draft, api_key))
        return self.result


@pytest.mark.asyncio
async def test_probe_router_dispatches_each_provider_kind():
    openai_result = ProbeResult(("compat-model",), "compat-model")
    ollama_result = ProbeResult(("local-model",), "local-model")
    openai_probe = RecordingProbe(openai_result)
    ollama_probe = RecordingProbe(ollama_result)
    router = ProviderProbeRouter(openai_probe, ollama_probe)
    compat = openai_draft()
    local = replace(ollama_draft(), default_model="local-model")

    assert await router.probe(compat, "sentinel-key") == openai_result
    assert await router.probe(local, None) == ollama_result
    assert openai_probe.calls == [(compat, "sentinel-key")]
    assert ollama_probe.calls == [(local, None)]
