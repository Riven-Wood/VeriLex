from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qs

import anyio
import httpx
import pytest

from verilex_runtime.web.provider import (
    DuckDuckGoSearchProvider,
    SearchResult,
    WebSearchError,
)


SEARCH_HTML = """
<!doctype html>
<html><body>
  <div class="result results_links web-result">
    <h2>
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.test%2Fcourt%23top">
        最高人民<b>法院</b>
      </a>
    </h2>
    <a class="result__snippet"> 示例 <b>摘要</b> </a>
  </div>
</body></html>
"""


class RecordingClientFactory:
    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self.transport = transport
        self.options: dict[str, Any] = {}

    def __call__(self, **options: Any) -> httpx.AsyncClient:
        self.options = options
        client_options = {
            key: value for key, value in options.items() if key != "proxy"
        }
        return httpx.AsyncClient(transport=self.transport, **client_options)


class BytesAsyncStream(httpx.AsyncByteStream):
    def __init__(self, content: str | bytes) -> None:
        self.content = content.encode("utf-8") if isinstance(content, str) else content

    async def __aiter__(self):
        yield self.content


def response_transport(
    content: str | bytes = SEARCH_HTML,
    *,
    status_code: int = 200,
) -> httpx.MockTransport:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            stream=BytesAsyncStream(content),
            request=request,
        )

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
async def test_search_uses_only_fixed_duckduckgo_endpoint_and_ignores_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    observed_request: dict[str, Any] = {}

    async def handle(request: httpx.Request) -> httpx.Response:
        observed_request.update(
            {
                "method": request.method,
                "url": str(request.url),
                "form": parse_qs(
                    request.content.decode("utf-8"), keep_blank_values=True
                ),
                "headers": dict(request.headers),
            }
        )
        return httpx.Response(
            200,
            stream=BytesAsyncStream(SEARCH_HTML),
            request=request,
        )

    factory = RecordingClientFactory(httpx.MockTransport(handle))
    provider = DuckDuckGoSearchProvider(client_factory=factory)

    results = await provider.search("最高人民法院", 5)

    assert factory.options["trust_env"] is False
    assert factory.options["follow_redirects"] is False
    assert factory.options["timeout"] == 20
    assert factory.options["headers"] == {
        "User-Agent": "VeriLex/0.3",
        "Accept-Encoding": "identity",
    }
    assert observed_request["method"] == "POST"
    assert observed_request["url"] == "https://html.duckduckgo.com/html/"
    assert observed_request["form"] == {
        "q": ["最高人民法院"],
        "b": [""],
        "l": ["wt-wt"],
        "kp": ["-1"],
    }
    for forbidden in ("cookie", "referer", "authorization"):
        assert forbidden not in observed_request["headers"]
    assert results == (
        SearchResult(
            title="最高人民法院",
            url="https://example.test/court",
            snippet="示例 摘要",
        ),
    )


@pytest.mark.asyncio
async def test_search_can_use_the_host_environment_proxy_when_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    observed_request: dict[str, Any] = {}

    async def handle(request: httpx.Request) -> httpx.Response:
        observed_request["url"] = str(request.url)
        observed_request["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            stream=BytesAsyncStream(SEARCH_HTML),
            request=request,
        )

    factory = RecordingClientFactory(httpx.MockTransport(handle))
    provider = DuckDuckGoSearchProvider(
        client_factory=factory,
        use_environment_proxy=True,
    )

    await provider.search("OpenAI official website", 5)

    assert factory.options["trust_env"] is False
    assert factory.options["proxy"] == "http://proxy.example:8080"
    assert observed_request["url"] == "https://html.duckduckgo.com/html/"
    for forbidden in ("cookie", "referer", "authorization"):
        assert forbidden not in observed_request["headers"]


@pytest.mark.asyncio
async def test_search_proxy_opt_in_honors_no_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "html.duckduckgo.com")
    factory = RecordingClientFactory(response_transport())

    await DuckDuckGoSearchProvider(
        client_factory=factory,
        use_environment_proxy=True,
    ).search("最高人民法院", 5)

    assert factory.options["trust_env"] is False
    assert "proxy" not in factory.options


@pytest.mark.asyncio
async def test_search_filters_invalid_rows_and_deduplicates_normalized_urls() -> None:
    html = """
    <a class="result__a" href="HTTPS://Example.Test:443/a#one">首条</a>
    <div class="result__snippet">A</div>
    <a class="result__a" href="https://example.test/a#two">重复</a>
    <div class="result__snippet">B</div>
    <a class="result__a" href="file:///tmp/private">文件</a>
    <a class="result__a" href="https:///missing-host">无主机</a>
    <a class="result__a" href="http://example.test/b">第二条</a>
    <div class="result__snippet"></div>
    <a class="result__a" href="https://example.test/no-title"> </a>
    """
    provider = DuckDuckGoSearchProvider(
        client_factory=RecordingClientFactory(response_transport(html))
    )

    results = await provider.search("query", 10)

    assert [(item.title, item.url, item.snippet) for item in results] == [
        ("首条", "https://example.test/a", "A"),
        ("第二条", "http://example.test/b", ""),
    ]


@pytest.mark.asyncio
async def test_search_honors_max_results_after_filtering() -> None:
    html = "".join(
        f'<a class="result__a" href="https://example.test/{index}">结果 {index}</a>'
        for index in range(4)
    )
    provider = DuckDuckGoSearchProvider(
        client_factory=RecordingClientFactory(response_transport(html))
    )

    results = await provider.search("query", 2)

    assert [item.title for item in results] == ["结果 0", "结果 1"]


@pytest.mark.asyncio
async def test_empty_search_results_are_not_an_error() -> None:
    provider = DuckDuckGoSearchProvider(
        client_factory=RecordingClientFactory(response_transport("<html></html>"))
    )

    assert await provider.search("no matches", 5) == ()


@pytest.mark.asyncio
async def test_http_429_maps_to_rate_limit_without_response_details() -> None:
    provider = DuckDuckGoSearchProvider(
        client_factory=RecordingClientFactory(
            response_transport(
                "proxy http://127.0.0.1:9 rate limited",
                status_code=429,
            )
        )
    )

    with pytest.raises(WebSearchError) as captured:
        await provider.search("query", 5)

    assert captured.value.code == "web_search_rate_limited"
    assert captured.value.retryable is True
    assert captured.value.message == "Web search is rate limited. Try again later."
    assert "127.0.0.1" not in str(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [302, 403, 500])
async def test_non_success_status_maps_to_stable_unavailable_error(
    status_code: int,
) -> None:
    provider = DuckDuckGoSearchProvider(
        client_factory=RecordingClientFactory(
            response_transport("secret response detail", status_code=status_code)
        )
    )

    with pytest.raises(WebSearchError) as captured:
        await provider.search("query", 5)

    assert captured.value.code == "web_search_unavailable"
    assert captured.value.message == "Web search is temporarily unavailable."
    assert "secret response detail" not in str(captured.value)


@pytest.mark.asyncio
async def test_http_failure_maps_to_stable_unavailable_error() -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "proxy http://127.0.0.1:9 failed with secret detail",
            request=request,
        )

    provider = DuckDuckGoSearchProvider(
        client_factory=RecordingClientFactory(httpx.MockTransport(fail))
    )

    with pytest.raises(WebSearchError) as captured:
        await provider.search("query", 5)

    assert captured.value.code == "web_search_unavailable"
    assert captured.value.message == "Web search is temporarily unavailable."
    assert "127.0.0.1" not in str(captured.value)
    assert "secret detail" not in str(captured.value)


class HangingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.started.set()
        try:
            await anyio.sleep_forever()
        finally:
            self.cancelled.set()


@pytest.mark.asyncio
async def test_total_timeout_cancels_the_in_flight_request() -> None:
    transport = HangingTransport()
    provider = DuckDuckGoSearchProvider(
        client_factory=RecordingClientFactory(transport),
        total_timeout_seconds=0.01,
    )

    with pytest.raises(WebSearchError) as captured:
        await provider.search("query", 5)

    assert captured.value.code == "web_search_unavailable"
    assert transport.started.is_set()
    assert transport.cancelled.is_set()


@pytest.mark.asyncio
async def test_caller_cancellation_closes_the_in_flight_request() -> None:
    transport = HangingTransport()
    provider = DuckDuckGoSearchProvider(
        client_factory=RecordingClientFactory(transport)
    )
    task = asyncio.create_task(provider.search("query", 5))
    await transport.started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.cancelled.is_set()


@pytest.mark.asyncio
async def test_oversized_search_response_fails_without_partial_results() -> None:
    provider = DuckDuckGoSearchProvider(
        client_factory=RecordingClientFactory(
            response_transport(b"x" * (2 * 1024 * 1024 + 1))
        )
    )

    with pytest.raises(WebSearchError) as captured:
        await provider.search("query", 5)

    assert captured.value.code == "web_search_unavailable"


@pytest.mark.asyncio
async def test_compressed_search_response_is_rejected() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=BytesAsyncStream(b"compressed"),
            request=request,
        )

    provider = DuckDuckGoSearchProvider(
        client_factory=RecordingClientFactory(httpx.MockTransport(handle))
    )

    with pytest.raises(WebSearchError) as captured:
        await provider.search("query", 5)

    assert captured.value.code == "web_search_unavailable"
