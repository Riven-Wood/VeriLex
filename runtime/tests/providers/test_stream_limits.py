import json

import httpx
import pytest

from verilex_runtime.providers import http_limits, openai_compatible
from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.providers.models import ChatMessage
from verilex_runtime.providers.openai_compatible import OpenAICompatibleChatStream


class Body(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


def event(delta):
    return ("data: " + json.dumps({"id": "chat-long", "object": "chat.completion.chunk",
        "created": 0, "model": "fixture", "choices": [{"index": 0, "delta": delta,
        "finish_reason": None}]}) + "\n\n").encode()


async def collect(body, *, headers=None):
    def handler(request):
        return httpx.Response(200, stream=body,
            headers=headers or {"content-type": "text/event-stream"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stream = OpenAICompatibleChatStream(client, "https://model.test/v1", None, "fixture")
        return [delta async for delta in stream.stream([ChatMessage(role="user", content="test")])]


@pytest.mark.asyncio
async def test_token_envelopes_above_old_four_mib_limit_keep_small_answer():
    # 25k one-token envelopes previously killed an otherwise small answer.
    chunk = event({"content": "字"})
    assert len(chunk) * 25000 > 4 * 1024 * 1024
    body = Body([chunk] * 25000 + [b"data: [DONE]\n\n"])
    deltas = await collect(body)
    assert "".join(delta.text for delta in deltas) == "字" * 25000
    assert body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["wire", "event", "content", "reasoning", "arguments"])
async def test_all_stream_budgets_fail_with_specific_code_and_close(monkeypatch, kind):
    if kind == "wire":
        monkeypatch.setattr(http_limits, "MAX_PROVIDER_STREAM_BYTES", 500)
        chunks = [event({"content": "x"})] * 10
    elif kind == "event":
        monkeypatch.setattr(http_limits, "MAX_PROVIDER_STREAM_EVENT_BYTES", 100)
        chunks = [b"data: " + b"x" * 60, b"x" * 60]
    else:
        monkeypatch.setattr(openai_compatible, "MAX_PROVIDER_OUTPUT_BYTES", 64)
        delta = {kind if kind != "reasoning" else "reasoning_content": "字" * 12}
        if kind == "arguments":
            delta = {"tool_calls": [{"index": 0, "function": {"arguments": "字" * 12}}]}
        chunks = [event(delta)] * 3
    body = Body(chunks)
    with pytest.raises(ProviderDomainError) as captured:
        await collect(body)
    assert captured.value.code == "provider_response_too_large"
    assert captured.value.retryable is False  # No blind automatic replay.
    assert body.closed


@pytest.mark.asyncio
async def test_sse_boundary_split_across_http_chunks_resets_event_budget(monkeypatch):
    chunk = event({"content": "ok"})
    monkeypatch.setattr(http_limits, "MAX_PROVIDER_STREAM_EVENT_BYTES", len(chunk))
    body = Body([chunk[:-1], chunk[-1:] + chunk[:4], chunk[4:], b"data: [DONE]\n\n"])
    assert "".join(delta.text for delta in await collect(body)) == "okok"


@pytest.mark.asyncio
async def test_probe_cannot_gain_stream_limit_by_forging_content_type(monkeypatch):
    monkeypatch.setattr(http_limits, "MAX_PROVIDER_RESPONSE_BYTES", 16)
    body = Body([b"x" * 17])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, stream=body, headers={"content-type": "text/event-stream"}))) as client:
        http_limits.install_provider_response_limit(client)
        with pytest.raises(http_limits.ProviderResponseTooLarge):
            await client.get("https://model.test/models")


@pytest.mark.asyncio
async def test_compressed_stream_still_rejected_as_incompatible():
    body = Body([b"not-read"])
    with pytest.raises(ProviderDomainError) as captured:
        await collect(body, headers={"content-type": "text/event-stream", "content-encoding": "gzip"})
    assert captured.value.code == "provider_protocol_error"
    assert body.closed
