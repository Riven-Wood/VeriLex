from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import anyio
import httpx
import pytest

from verilex_runtime.tools.registry import ToolError
from verilex_runtime.web.client import FetchedPage, PublicWebClient


PUBLIC_V4 = "93.184.216.34"


class StaticResolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        return self.addresses


Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


class RecordingClientFactory:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.options: list[dict[str, Any]] = []

    def __call__(self, **options: Any) -> httpx.AsyncClient:
        self.options.append(options)
        client_options = {
            key: value for key, value in options.items() if key != "proxy"
        }
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler),
            **client_options,
        )


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def streamed_response(
    request: httpx.Request,
    body: bytes = b"ok",
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    stream: httpx.AsyncByteStream | None = None,
) -> httpx.Response:
    response_headers = {"Content-Type": "text/html; charset=utf-8"}
    response_headers.update(headers or {})
    return httpx.Response(
        status_code,
        headers=response_headers,
        stream=stream or BytesStream(body),
        request=request,
    )


@pytest.mark.asyncio
async def test_connection_is_pinned_to_verified_ip_with_host_and_tls_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    observed: dict[str, Any] = {}

    async def handle(request: httpx.Request) -> httpx.Response:
        observed.update(
            url=str(request.url),
            headers=dict(request.headers),
            extensions=request.extensions,
        )
        return streamed_response(request, b"<p>safe</p>")

    resolver = StaticResolver(PUBLIC_V4)
    factory = RecordingClientFactory(handle)
    page = await PublicWebClient(
        resolver=resolver, client_factory=factory
    ).fetch("https://example.test:8443/case?q=1#fragment")

    assert resolver.calls == [("example.test", 8443)]
    assert observed["url"] == f"https://{PUBLIC_V4}:8443/case?q=1"
    assert observed["headers"]["host"] == "example.test:8443"
    assert observed["extensions"]["sni_hostname"] == "example.test"
    assert factory.options == [
        {
            "trust_env": False,
            "follow_redirects": False,
            "timeout": 20,
            "headers": {
                "User-Agent": "VeriLex/0.3",
                "Accept-Encoding": "identity",
            },
        }
    ]
    for forbidden in ("cookie", "referer", "authorization"):
        assert forbidden not in observed["headers"]
    assert page.final_url == "https://example.test:8443/case?q=1"
    assert page.content_type == "text/html; charset=utf-8"
    assert page.body == b"<p>safe</p>"
    assert datetime.fromisoformat(page.fetched_at).tzinfo is not None


@pytest.mark.asyncio
async def test_environment_proxy_mode_keeps_the_public_dns_guard_before_using_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    observed: dict[str, Any] = {}

    async def handle(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["headers"] = dict(request.headers)
        return streamed_response(request, b"<title>OpenAI</title>")

    resolver = StaticResolver(PUBLIC_V4)
    factory = RecordingClientFactory(handle)
    page = await PublicWebClient(
        resolver=resolver,
        client_factory=factory,
        use_environment_proxy=True,
    ).fetch("https://openai.com/?source=acceptance#top")

    assert resolver.calls == [("openai.com", 443)]
    assert observed["url"] == "https://openai.com/?source=acceptance"
    assert observed["headers"]["host"] == "openai.com"
    assert factory.options[0]["trust_env"] is False
    assert factory.options[0]["proxy"] == "http://proxy.example:8080"
    for forbidden in ("cookie", "referer", "authorization"):
        assert forbidden not in observed["headers"]
    assert page.final_url == "https://openai.com/?source=acceptance"


@pytest.mark.asyncio
@pytest.mark.parametrize("no_proxy", ["public.test", "*"])
async def test_environment_proxy_mode_falls_back_to_pinned_direct_connection_when_bypassed(
    monkeypatch: pytest.MonkeyPatch,
    no_proxy: str,
) -> None:
    for name in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", no_proxy)
    requested: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return streamed_response(request)

    factory = RecordingClientFactory(handle)
    await PublicWebClient(
        resolver=StaticResolver(PUBLIC_V4),
        client_factory=factory,
        use_environment_proxy=True,
    ).fetch("https://public.test/path")

    assert requested == [f"https://{PUBLIC_V4}/path"]
    assert factory.options[0]["trust_env"] is False
    assert "proxy" not in factory.options[0]


@pytest.mark.asyncio
async def test_environment_proxy_mode_without_a_proxy_stays_ip_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)

    class RebindingResolver:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, host: str, port: int) -> tuple[str, ...]:
            self.calls += 1
            return (PUBLIC_V4,) if self.calls == 1 else ("127.0.0.1",)

    resolver = RebindingResolver()
    requested: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return streamed_response(request)

    factory = RecordingClientFactory(handle)
    await PublicWebClient(
        resolver=resolver,
        client_factory=factory,
        use_environment_proxy=True,
    ).fetch("https://public.test/")

    assert resolver.calls == 1
    assert requested == [f"https://{PUBLIC_V4}/"]
    assert factory.options[0]["trust_env"] is False
    assert "proxy" not in factory.options[0]


@pytest.mark.asyncio
async def test_dns_is_not_repeated_by_transport_after_guard_validation() -> None:
    class RebindingResolver:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, host: str, port: int) -> tuple[str, ...]:
            self.calls += 1
            return (PUBLIC_V4,) if self.calls == 1 else ("127.0.0.1",)

    resolver = RebindingResolver()
    requested: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return streamed_response(request)

    await PublicWebClient(
        resolver=resolver,
        client_factory=RecordingClientFactory(handle),
    ).fetch("https://public.test/")

    assert resolver.calls == 1
    assert requested == [f"https://{PUBLIC_V4}/"]


@pytest.mark.asyncio
async def test_public_ip_literal_does_not_call_dns_resolver() -> None:
    async def forbidden_resolver(host: str, port: int) -> tuple[str, ...]:
        raise AssertionError("literal addresses must not use DNS")

    requested: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return streamed_response(request)

    page = await PublicWebClient(
        resolver=forbidden_resolver,
        client_factory=RecordingClientFactory(handle),
    ).fetch(f"http://{PUBLIC_V4}/page")

    assert requested == [f"http://{PUBLIC_V4}/page"]
    assert page.final_url == f"http://{PUBLIC_V4}/page"


@pytest.mark.asyncio
async def test_redirect_is_revalidated_before_second_request() -> None:
    requested: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return streamed_response(
            request,
            status_code=302,
            headers={"Location": "http://127.0.0.1/admin"},
        )

    client = PublicWebClient(
        resolver=StaticResolver(PUBLIC_V4),
        client_factory=RecordingClientFactory(handle),
    )

    with pytest.raises(ToolError) as captured:
        await client.fetch("https://public.test")

    assert captured.value.code == "web_redirect_blocked"
    assert requested == [f"https://{PUBLIC_V4}/"]


@pytest.mark.asyncio
async def test_relative_redirects_are_followed_without_forwarding_cookies() -> None:
    requests: list[dict[str, str]] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.headers))
        if len(requests) == 1:
            return streamed_response(
                request,
                status_code=302,
                headers={"Location": "/final", "Set-Cookie": "secret=value"},
            )
        return streamed_response(
            request,
            b"done",
            headers={"Content-Type": "text/plain"},
        )

    page = await PublicWebClient(
        resolver=StaticResolver(PUBLIC_V4),
        client_factory=RecordingClientFactory(handle),
    ).fetch("https://public.test/start")

    assert len(requests) == 2
    assert all("cookie" not in headers for headers in requests)
    assert page.final_url == "https://public.test/final"
    assert page.body == b"done"


@pytest.mark.asyncio
async def test_sixth_redirect_is_not_followed() -> None:
    request_count = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return streamed_response(
            request,
            status_code=302,
            headers={"Location": f"/hop-{request_count}"},
        )

    with pytest.raises(ToolError) as captured:
        await PublicWebClient(
            resolver=StaticResolver(PUBLIC_V4),
            client_factory=RecordingClientFactory(handle),
        ).fetch("https://public.test/start")

    assert captured.value.code == "web_fetch_failed"
    assert request_count == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Length": str(2 * 1024 * 1024 + 1)},
        {"Content-Encoding": "gzip"},
        {"Content-Disposition": "attachment; filename=payload.html"},
    ],
)
async def test_unsafe_response_headers_stop_before_body(
    headers: dict[str, str],
) -> None:
    stream = BytesStream(b"must not be consumed")

    async def handle(request: httpx.Request) -> httpx.Response:
        return streamed_response(request, headers=headers, stream=stream)

    with pytest.raises(ToolError) as captured:
        await PublicWebClient(
            resolver=StaticResolver(PUBLIC_V4),
            client_factory=RecordingClientFactory(handle),
        ).fetch("https://public.test")

    assert captured.value.code in {"web_fetch_too_large", "web_content_unsupported"}
    assert stream.closed is True


@pytest.mark.asyncio
async def test_stream_over_two_mib_stops_and_returns_no_partial_page() -> None:
    stream = BytesStream(b"a" * (2 * 1024 * 1024), b"b")

    async def handle(request: httpx.Request) -> httpx.Response:
        return streamed_response(request, stream=stream)

    with pytest.raises(ToolError) as captured:
        await PublicWebClient(
            resolver=StaticResolver(PUBLIC_V4),
            client_factory=RecordingClientFactory(handle),
        ).fetch("https://public.test")

    assert captured.value.code == "web_fetch_too_large"
    assert stream.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type",
    [
        "image/png",
        "application/pdf",
        "text/css; charset=utf-8",
        "text/html; charset=gb18030",
        "",
    ],
)
async def test_unsupported_content_type_is_rejected_before_body(
    content_type: str,
) -> None:
    stream = BytesStream(b"must not be consumed")

    async def handle(request: httpx.Request) -> httpx.Response:
        headers = {"Content-Type": content_type} if content_type else {}
        return httpx.Response(200, headers=headers, stream=stream, request=request)

    with pytest.raises(ToolError) as captured:
        await PublicWebClient(
            resolver=StaticResolver(PUBLIC_V4),
            client_factory=RecordingClientFactory(handle),
        ).fetch("https://public.test")

    assert captured.value.code == "web_content_unsupported"
    assert stream.closed is True


class HangingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def __aiter__(self):
        yield b"partial"
        self.started.set()
        await anyio.sleep_forever()

    async def aclose(self) -> None:
        self.closed.set()


@pytest.mark.asyncio
async def test_total_deadline_closes_in_flight_stream_without_partial_success() -> None:
    stream = HangingStream()

    async def handle(request: httpx.Request) -> httpx.Response:
        return streamed_response(request, stream=stream)

    with pytest.raises(ToolError) as captured:
        await PublicWebClient(
            resolver=StaticResolver(PUBLIC_V4),
            client_factory=RecordingClientFactory(handle),
            total_timeout_seconds=0.01,
        ).fetch("https://public.test")

    assert captured.value.code == "web_fetch_timeout"
    assert stream.started.is_set()
    assert stream.closed.is_set()


@pytest.mark.asyncio
async def test_cancellation_is_propagated_after_closing_response_stream() -> None:
    stream = HangingStream()

    async def handle(request: httpx.Request) -> httpx.Response:
        return streamed_response(request, stream=stream)

    task = asyncio.create_task(
        PublicWebClient(
            resolver=StaticResolver(PUBLIC_V4),
            client_factory=RecordingClientFactory(handle),
        ).fetch("https://public.test")
    )
    await stream.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [403, 429])
async def test_http_failure_is_stable_and_redacted(status_code: int) -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return streamed_response(
            request,
            b"secret 127.0.0.1",
            status_code=status_code,
        )

    with pytest.raises(ToolError) as captured:
        await PublicWebClient(
            resolver=StaticResolver(PUBLIC_V4),
            client_factory=RecordingClientFactory(handle),
        ).fetch("https://public.test")

    assert captured.value.code == "web_fetch_failed"
    assert "127.0.0.1" not in str(captured.value)


def test_fetched_page_is_immutable() -> None:
    page = FetchedPage(
        final_url="https://example.test/",
        content_type="text/plain",
        body=b"body",
        fetched_at="2026-08-27T00:00:00+00:00",
    )

    with pytest.raises(AttributeError):
        page.body = b"changed"  # type: ignore[misc]
