from __future__ import annotations

import json

import httpx


MAX_PROVIDER_RESPONSE_BYTES = 4 * 1024 * 1024
# Streaming responses repeat a JSON envelope for every small token delta.
# Bound that overhead separately from both one event and decoded model output.
MAX_PROVIDER_STREAM_BYTES = 32 * 1024 * 1024
MAX_PROVIDER_STREAM_EVENT_BYTES = 1024 * 1024
MAX_PROVIDER_OUTPUT_BYTES = 1024 * 1024


class ProviderResponseTooLarge(Exception):
    def __init__(self, message: str, *, size_limit: bool = True) -> None:
        super().__init__(message)
        self.size_limit = size_limit


class _LimitedResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, *, streaming: bool = False) -> None:
        self._stream = stream
        self._streaming = streaming

    async def __aiter__(self):
        received = 0
        event_bytes = 0
        line_bytes = 0
        limit = MAX_PROVIDER_STREAM_BYTES if self._streaming else MAX_PROVIDER_RESPONSE_BYTES
        iterator = self._stream.__aiter__()
        try:
            async for chunk in iterator:
                received += len(chunk)
                if received > limit:
                    raise ProviderResponseTooLarge(
                        "provider response exceeded size limit"
                    )
                if self._streaming:
                    lines = chunk.split(b"\n")
                    for index, part in enumerate(lines):
                        ended = index < len(lines) - 1
                        event_bytes += len(part) + ended
                        line_bytes += len(part.rstrip(b"\r"))
                        if event_bytes > MAX_PROVIDER_STREAM_EVENT_BYTES:
                            raise ProviderResponseTooLarge("provider stream event exceeded size limit")
                        if ended:
                            # The preceding line ended. A blank line is an SSE
                            # event boundary; count across HTTP chunk splits.
                            if line_bytes == 0:
                                event_bytes = 0
                            line_bytes = 0
                yield chunk
        finally:
            close_iterator = getattr(iterator, "aclose", None)
            if close_iterator is not None:
                await close_iterator()

    async def aclose(self) -> None:
        await self._stream.aclose()


async def _limit_provider_response(response: httpx.Response) -> None:
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding and encoding != "identity":
        await response.aclose()
        raise ProviderResponseTooLarge(
            "compressed provider responses are not accepted", size_limit=False)

    streaming = False
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "text/event-stream":
        try:
            request_body = json.loads(response.request.content)
            streaming = isinstance(request_body, dict) and request_body.get("stream") is True
        except (ValueError, httpx.RequestNotRead):
            pass
    limit = MAX_PROVIDER_STREAM_BYTES if streaming else MAX_PROVIDER_RESPONSE_BYTES
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except ValueError:
            declared_bytes = -1
        if declared_bytes < 0 or declared_bytes > limit:
            await response.aclose()
            raise ProviderResponseTooLarge("provider response exceeded size limit")

    response.stream = _LimitedResponseStream(response.stream, streaming=streaming)


def install_provider_response_limit(client: httpx.AsyncClient) -> None:
    hooks = client.event_hooks.setdefault("response", [])
    if _limit_provider_response not in hooks:
        hooks.append(_limit_provider_response)


def caused_by_oversized_response(error: BaseException, *, actual_size_only: bool = False) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, ProviderResponseTooLarge):
            return current.size_limit or not actual_size_only
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False
