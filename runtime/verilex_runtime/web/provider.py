"""Fixed DuckDuckGo search provider for controlled public-web research.

The provider abstraction and keyless DuckDuckGo adapter structure are adapted
from OpenWorker ``coworker/web/providers.py`` at commit
``86c57f0692a5a318e55d1b9e0188d798b9fc5690`` (MIT). VeriLex narrows that
design to one disclosed endpoint, a cancellable async client, and stable errors.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import SplitResult, parse_qs, urlsplit, urlunsplit

import anyio
import httpx

from verilex_runtime.web.proxy import environment_proxy_for


_DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TOTAL_TIMEOUT_SECONDS = 20
_USER_AGENT = "VeriLex/0.3"
_VOID_HTML_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class WebSearchProvider(Protocol):
    async def search(
        self, query: str, max_results: int
    ) -> tuple[SearchResult, ...]: ...


class WebSearchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = True


ClientFactory = Callable[..., httpx.AsyncClient]


@dataclass(slots=True)
class _ParsedResult:
    title: str = ""
    href: str = ""
    snippet: str = ""


class _DuckDuckGoHtmlParser(HTMLParser):
    """Extract only DuckDuckGo's disclosed result title/link/snippet fields."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[_ParsedResult] = []
        self._current: _ParsedResult | None = None
        self._capture: str | None = None
        self._capture_depth = 0
        self._parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if self._capture is not None:
            if tag not in _VOID_HTML_ELEMENTS:
                self._capture_depth += 1
            return

        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if "result__a" in classes:
            self._current = _ParsedResult(href=attributes.get("href") or "")
            self.results.append(self._current)
            self._begin_capture("title")
        elif "result__snippet" in classes and self._current is not None:
            self._begin_capture("snippet")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if self._capture is None:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self._capture is None:
            return
        self._capture_depth -= 1
        if self._capture_depth > 0:
            return
        text = " ".join("".join(self._parts).split())
        if self._current is not None:
            if self._capture == "title":
                self._current.title = text
            else:
                self._current.snippet = text
        self._capture = None
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._parts.append(data)

    def _begin_capture(self, field: str) -> None:
        self._capture = field
        self._capture_depth = 1
        self._parts = []


def _default_client_factory(**options: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(**options)


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None

    if (
        parsed.hostname in {"duckduckgo.com", "www.duckduckgo.com"}
        and parsed.path.startswith("/l/")
    ):
        targets = parse_qs(parsed.query).get("uddg")
        if not targets:
            return None
        candidate = targets[0].strip()

    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    if port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        hostname = f"{hostname}:{port}"

    return urlunsplit(
        SplitResult(
            scheme=scheme,
            netloc=hostname,
            path=parsed.path or "/",
            query=parsed.query,
            fragment="",
        )
    )


def _normalize_results(
    rows: Iterable[_ParsedResult], max_results: int
) -> tuple[SearchResult, ...]:
    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    for row in rows:
        title = " ".join(row.title.split())
        url = _safe_url(row.href)
        if not title or url is None or url in seen_urls:
            continue
        seen_urls.add(url)
        results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=" ".join(row.snippet.split()),
            )
        )
        if len(results) >= max_results:
            break
    return tuple(results)


def _parse_results(html_text: str, max_results: int) -> tuple[SearchResult, ...]:
    parser = _DuckDuckGoHtmlParser()
    parser.feed(html_text)
    parser.close()
    return _normalize_results(parser.results, max_results)


def _rate_limit_error() -> WebSearchError:
    return WebSearchError(
        "web_search_rate_limited",
        "Web search is rate limited. Try again later.",
    )


def _unavailable_error() -> WebSearchError:
    return WebSearchError(
        "web_search_unavailable",
        "Web search is temporarily unavailable.",
    )


class DuckDuckGoSearchProvider:
    def __init__(
        self,
        *,
        client_factory: ClientFactory | None = None,
        total_timeout_seconds: float = _TOTAL_TIMEOUT_SECONDS,
        use_environment_proxy: bool = False,
    ) -> None:
        self._client_factory = client_factory or _default_client_factory
        self._total_timeout_seconds = total_timeout_seconds
        self._use_environment_proxy = use_environment_proxy

    async def search(
        self, query: str, max_results: int
    ) -> tuple[SearchResult, ...]:
        try:
            with anyio.fail_after(self._total_timeout_seconds):
                return await self._search(query, max_results)
        except WebSearchError:
            raise
        except TimeoutError:
            raise _unavailable_error() from None
        except Exception:
            raise _unavailable_error() from None

    async def _search(
        self, query: str, max_results: int
    ) -> tuple[SearchResult, ...]:
        client_options: dict[str, Any] = {
            "trust_env": False,
            "follow_redirects": False,
            "timeout": _TOTAL_TIMEOUT_SECONDS,
            "headers": {
                "User-Agent": _USER_AGENT,
                "Accept-Encoding": "identity",
            },
        }
        proxy = (
            environment_proxy_for("https", "html.duckduckgo.com", 443)
            if self._use_environment_proxy
            else None
        )
        if proxy is not None:
            client_options["proxy"] = proxy
        async with self._client_factory(**client_options) as client:
            async with client.stream(
                "POST",
                _DUCKDUCKGO_HTML_URL,
                data={
                    "q": query,
                    "b": "",
                    "l": "wt-wt",
                    "kp": "-1",
                },
            ) as response:
                if response.status_code == 429:
                    raise _rate_limit_error()
                if response.status_code != 200:
                    raise _unavailable_error()
                if response.headers.get("content-encoding", "identity").lower() not in {
                    "",
                    "identity",
                }:
                    raise _unavailable_error()
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        if int(content_length) > _MAX_RESPONSE_BYTES:
                            raise _unavailable_error()
                    except ValueError:
                        raise _unavailable_error() from None

                content = bytearray()
                chunks = response.aiter_raw(chunk_size=64 * 1024)
                try:
                    async for chunk in chunks:
                        content.extend(chunk)
                        if len(content) > _MAX_RESPONSE_BYTES:
                            raise _unavailable_error()
                finally:
                    await chunks.aclose()

        return _parse_results(content.decode("utf-8", errors="replace"), max_results)


__all__ = [
    "DuckDuckGoSearchProvider",
    "SearchResult",
    "WebSearchError",
    "WebSearchProvider",
]
