import json

import httpx
import openai
import pytest

from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.providers.models import ProviderDraft, ProbeResult
from verilex_runtime.providers.openai_compatible import (
    OpenAICompatibleProbe,
    _vision_rejection,
)
from verilex_runtime.storage.models import AuthMode, ProviderKind


def draft(model: str = "deepseek-chat") -> ProviderDraft:
    return ProviderDraft(
        name="Compat",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://models.example.test/v1",
        default_model=model,
        auth_mode=AuthMode.API_KEY,
        api_key=None,
    )


@pytest.mark.asyncio
async def test_probe_accepts_model_directory_match():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer sentinel-key"
        assert request.extensions["timeout"] == {
            "connect": 5.0,
            "read": 20.0,
            "write": 20.0,
            "pool": 20.0,
        }
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "deepseek-chat",
                        "object": "model",
                        "created": 0,
                        "owned_by": "test",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await OpenAICompatibleProbe(client).probe(draft(), "sentinel-key")

    assert result.selected_model == "deepseek-chat"
    assert result.models == ("deepseek-chat",)
    assert result.context_windows == ()


def test_context_window_value_accepts_only_bounded_strict_integers():
    from verilex_runtime.providers.openai_compatible import context_window_value

    assert context_window_value(128_000) == 128_000
    for invalid in (True, 128_000.0, "128000", 0, -1, 10_000_001):
        assert context_window_value(invalid) is None


@pytest.mark.asyncio
async def test_probe_extracts_explicit_provider_context_metadata_by_priority():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "provider-model",
                        "object": "model",
                        "created": 0,
                        "owned_by": "test",
                        "context_length": 131_072,
                        "context_window": 64_000,
                        "top_provider": {"context_length": 32_000},
                    },
                    {
                        "id": "nested-model",
                        "object": "model",
                        "created": 0,
                        "owned_by": "test",
                        "top_provider": {"context_length": 200_000},
                    },
                    {
                        "id": "invalid-model",
                        "object": "model",
                        "created": 0,
                        "owned_by": "test",
                        "context_length": "128000",
                    },
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await OpenAICompatibleProbe(client).probe(
            draft(model="provider-model"), "sentinel-key"
        )

    assert [
        (hint.model, hint.tokens, hint.source) for hint in result.context_windows
    ] == [
        ("provider-model", 131_072, "provider"),
        ("nested-model", 200_000, "provider"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("directory_status", [404, 405])
async def test_probe_falls_back_to_fixed_minimal_chat(directory_status: int):
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        assert request.headers["authorization"] == "Bearer verilex-no-key"
        if request.url.path == "/v1/models":
            return httpx.Response(
                directory_status,
                json={"error": {"message": "model directory unsupported"}},
            )

        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-chat"
        assert payload["messages"] == [
            {"role": "user", "content": "Reply with OK."}
        ]
        assert payload["max_tokens"] == 1
        assert payload["stream"] is False
        assert "tools" not in payload
        assert "Compat" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "id": "probe-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await OpenAICompatibleProbe(client).probe(draft(), None)

    assert requested_paths == ["/v1/models", "/v1/chat/completions"]
    assert result == ProbeResult(
        models=("deepseek-chat",),
        selected_model="deepseek-chat",
    )


@pytest.mark.asyncio
async def test_probe_rejects_missing_selected_model_without_chat_fallback():
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "another-model",
                        "object": "model",
                        "created": 0,
                        "owned_by": "test",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ProviderDomainError) as captured:
            await OpenAICompatibleProbe(client).probe(draft(), "sentinel-key")

    assert captured.value.code == "provider_model_not_found"
    assert requested_paths == ["/v1/models"]


def _bad_request(message: str):
    """A minimal stand-in carrying only the attributes _vision_rejection reads."""
    from types import SimpleNamespace

    body = {"error": {"message": message, "type": "invalid_request_error"}}
    return SimpleNamespace(body=body, status_code=400, message=message)


@pytest.mark.parametrize(
    "message",
    [
        "This model does not support image",
        "This model does not support vision",
        "image input is not supported by this model",
        "The model does not accept image content.",
    ],
)
def test_vision_rejection_matches_image_unsupported_messages(message):
    assert _vision_rejection(_bad_request(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "Invalid parameter: temperature",
        "The server is busy",
        "Model not found",
        "invalid api key",
    ],
)
def test_vision_rejection_ignores_unrelated_errors(message):
    assert _vision_rejection(_bad_request(message)) is False


def _sse_chunk(text: str) -> bytes:
    payload = {
        "id": "chunk-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": text},
                "finish_reason": None,
            }
        ],
    }
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


@pytest.mark.asyncio
async def test_stream_disconnect_mid_response_maps_to_stream_interrupted():
    """长输出中途断流应归类为可重试的网络中断，而不是“不兼容的响应”。"""
    import anyio

    from verilex_runtime.providers.models import ChatMessage
    from verilex_runtime.providers.openai_compatible import (
        OpenAICompatibleChatStream,
    )

    async def dying_stream():
        yield _sse_chunk("部分输出")
        await anyio.sleep(0)
        raise httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body"
        )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=dying_stream(),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://api.example.test/v1", "key", "deepseek-chat"
        )
        received = []
        with pytest.raises(ProviderDomainError) as captured:
            async for delta in stream.stream(
                [ChatMessage(role="user", content="写一份长合同")]
            ):
                received.append(delta)

    assert captured.value.code == "provider_stream_interrupted"
    assert any(delta.kind == "text" for delta in received)


@pytest.mark.asyncio
async def test_stream_disconnect_before_any_output_maps_to_unreachable():
    from verilex_runtime.providers.models import ChatMessage
    from verilex_runtime.providers.openai_compatible import (
        OpenAICompatibleChatStream,
    )

    async def dead_stream():
        raise httpx.ConnectError("connection refused")
        yield b""  # pragma: no cover - makes this an async generator

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=dead_stream())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://api.example.test/v1", "key", "deepseek-chat"
        )
        with pytest.raises(ProviderDomainError) as captured:
            async for _delta in stream.stream(
                [ChatMessage(role="user", content="hi")]
            ):
                pass

    assert captured.value.code in ("provider_unreachable", "provider_stream_interrupted")
