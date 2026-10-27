"""Bounded HTTP reader for already-authorized public web pages."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
from ipaddress import ip_address
import re
import socket
from typing import Any
from urllib.parse import urljoin

import anyio
import httpx

from verilex_runtime.tools.registry import ToolError
from verilex_runtime.web.guard import (
    NormalizedUrl,
    VerifiedTarget,
    normalize_public_url,
    resolve_public_target,
)
from verilex_runtime.web.proxy import environment_proxy_for


_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 5
_TOTAL_TIMEOUT_SECONDS = 20
_USER_AGENT = "VeriLex/0.3"
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_CHARSET = re.compile(r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\"'\s]+)", re.I)


@dataclass(frozen=True, slots=True)
class FetchedPage:
    final_url: str
    content_type: str
    body: bytes
    fetched_at: str


ResolverResult = Iterable[str] | Awaitable[Iterable[str]]
Resolver = Callable[[str, int], ResolverResult]
ClientFactory = Callable[..., httpx.AsyncClient]


async def _system_resolver(host: str, port: int) -> tuple[str, ...]:
    records = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(record[4][0] for record in records)


def _default_client_factory(**options: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(**options)


class PublicWebClient:
    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        client_factory: ClientFactory | None = None,
        total_timeout_seconds: float = _TOTAL_TIMEOUT_SECONDS,
        use_environment_proxy: bool = False,
    ) -> None:
        self._resolver = resolver or _system_resolver
        self._client_factory = client_factory or _default_client_factory
        self._total_timeout_seconds = total_timeout_seconds
        self._use_environment_proxy = use_environment_proxy

    async def fetch(self, url: str) -> FetchedPage:
        try:
            with anyio.fail_after(self._total_timeout_seconds):
                return await self._fetch(url)
        except ToolError:
            raise
        except TimeoutError:
            raise ToolError(
                "web_fetch_timeout", "The web page request timed out."
            ) from None
        except Exception:
            raise _fetch_failed() from None

    async def _fetch(self, url: str) -> FetchedPage:
        current = normalize_public_url(url)
        redirects = 0
        while True:
            try:
                target = await self._resolve(current)
            except ToolError as error:
                if redirects and error.code in {"web_url_invalid", "web_url_blocked"}:
                    raise ToolError(
                        "web_redirect_blocked",
                        "The web page redirected to a blocked address.",
                    ) from None
                raise

            proxy = (
                environment_proxy_for(current.scheme, current.host, current.port)
                if self._use_environment_proxy
                else None
            )
            client_options: dict[str, Any] = {
                "trust_env": False,
                "follow_redirects": False,
                "timeout": _TOTAL_TIMEOUT_SECONDS,
                "headers": {
                    "User-Agent": _USER_AGENT,
                    "Accept-Encoding": "identity",
                },
            }
            if proxy is not None:
                client_options["proxy"] = proxy

            async with self._client_factory(**client_options) as client:
                async with client.stream(
                    "GET",
                    current.url if proxy is not None else _pinned_url(target),
                    headers=(
                        {} if proxy is not None
                        else {"Host": target.host_header}
                    ),
                    extensions=(
                        {} if proxy is not None
                        else {"sni_hostname": target.sni_hostname}
                    ),
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location or redirects >= _MAX_REDIRECTS:
                            raise _fetch_failed()
                        redirects += 1
                        try:
                            current = normalize_public_url(
                                urljoin(current.url, location)
                            )
                        except ToolError:
                            raise ToolError(
                                "web_redirect_blocked",
                                "The web page redirected to a blocked address.",
                            ) from None
                        continue

                    if not 200 <= response.status_code < 300:
                        raise _fetch_failed()
                    _validate_response_headers(response.headers)
                    body = await _read_bounded_body(response)
                    return FetchedPage(
                        final_url=current.url,
                        content_type=response.headers.get("content-type", ""),
                        body=body,
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                    )

    async def _resolve(self, url: NormalizedUrl) -> VerifiedTarget:
        try:
            ip_address(url.host)
        except ValueError:
            pass
        else:
            return resolve_public_target(url, lambda _host, _port: ())
        try:
            result = self._resolver(url.host, url.port)
            addresses = await result if inspect.isawaitable(result) else result
        except Exception:
            raise _fetch_failed() from None
        return resolve_public_target(url, lambda _host, _port: addresses)


def _pinned_url(target: VerifiedTarget) -> str:
    connect_host = target.connect_host
    if ":" in connect_host:
        connect_host = f"[{connect_host}]"
    default_port = target.url.port == (
        443 if target.url.scheme == "https" else 80
    )
    authority = (
        connect_host
        if default_port
        else f"{connect_host}:{target.url.port}"
    )
    return f"{target.url.scheme}://{authority}{target.url.target}"


def _validate_response_headers(headers: httpx.Headers) -> None:
    content_type = headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    charset_match = _CHARSET.search(content_type)
    charset = charset_match.group(1).lower() if charset_match else None
    if media_type not in {"text/html", "text/plain"} or charset not in {
        None,
        "utf-8",
        "utf8",
    }:
        raise ToolError(
            "web_content_unsupported",
            "This web content type is not supported.",
        )
    content_encoding = headers.get("content-encoding", "identity").strip().lower()
    if content_encoding not in {"", "identity"}:
        raise ToolError(
            "web_content_unsupported",
            "The web page uses an unsupported encoded response.",
        )
    if "attachment" in headers.get("content-disposition", "").lower():
        raise ToolError(
            "web_content_unsupported",
            "Downloaded attachments are not supported.",
        )
    content_length = headers.get("content-length")
    if content_length is not None:
        try:
            parsed_length = int(content_length)
        except ValueError:
            raise _fetch_failed() from None
        if parsed_length < 0:
            raise _fetch_failed()
        if parsed_length > _MAX_RESPONSE_BYTES:
            raise ToolError(
                "web_fetch_too_large", "The web page exceeds the size limit."
            )


async def _read_bounded_body(response: httpx.Response) -> bytes:
    body = bytearray()
    chunks = response.aiter_raw(chunk_size=64 * 1024)
    try:
        async for chunk in chunks:
            body.extend(chunk)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise ToolError(
                    "web_fetch_too_large", "The web page exceeds the size limit."
                )
    finally:
        await chunks.aclose()
    return bytes(body)


def _fetch_failed() -> ToolError:
    return ToolError("web_fetch_failed", "The web page could not be fetched.")
