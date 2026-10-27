"""Public URL validation for controlled web reads.

This module adapts the public-address guard design from OpenWorker
``coworker/web/guard.py`` at commit
``86c57f0692a5a318e55d1b9e0188d798b9fc5690`` (MIT). VeriLex additionally
returns the complete verified address set so the HTTP layer can pin each
connection without performing a second DNS lookup.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from ipaddress import ip_address
import re
from urllib.parse import SplitResult, unquote_to_bytes, urlsplit, urlunsplit

from verilex_runtime.tools.registry import ToolError
from verilex_runtime.web_hosts import is_public_unicast


_MAX_URL_LENGTH = 2048
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")
_ENCODED_LINE_BREAK = re.compile(r"%(?:0a|0d)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class NormalizedUrl:
    url: str
    scheme: str
    host: str
    port: int
    target: str


@dataclass(frozen=True, slots=True)
class VerifiedTarget:
    url: NormalizedUrl
    addresses: tuple[str, ...]
    connect_host: str
    host_header: str
    sni_hostname: str


Resolver = Callable[[str, int], Iterable[str]]


def normalize_public_url(url: str) -> NormalizedUrl:
    if not isinstance(url, str):
        raise _invalid_url()
    candidate = url.strip()
    if (
        not candidate
        or len(candidate) > _MAX_URL_LENGTH
        or _CONTROL_OR_SPACE.search(candidate)
        or _ENCODED_LINE_BREAK.search(candidate)
        or "\\" in candidate
    ):
        raise _invalid_url()

    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except (TypeError, ValueError):
        raise _invalid_url() from None

    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or _has_single_decoded_dot_segment(parsed.path)
    ):
        raise _invalid_url()

    raw_host = parsed.hostname.rstrip(".")
    if not raw_host or "%" in raw_host:
        raise _invalid_url()
    try:
        address = ip_address(raw_host)
    except ValueError:
        try:
            host = raw_host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise _invalid_url() from None
        if (
            len(host) > 253
            or any(not label or len(label) > 63 for label in host.split("."))
            or any(
                not re.fullmatch(r"[a-z0-9-]+", label)
                or label.startswith("-")
                or label.endswith("-")
                for label in host.split(".")
            )
        ):
            raise _invalid_url()
    else:
        host = address.compressed

    if port is not None and port < 1:
        raise _invalid_url()
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    default_port = effective_port == (443 if scheme == "https" else 80)
    url_host = f"[{host}]" if ":" in host else host
    netloc = url_host if default_port else f"{url_host}:{effective_port}"
    path = parsed.path or "/"
    target = path + (f"?{parsed.query}" if parsed.query else "")
    normalized = urlunsplit(
        SplitResult(
            scheme=scheme,
            netloc=netloc,
            path=path,
            query=parsed.query,
            fragment="",
        )
    )
    if len(normalized) > _MAX_URL_LENGTH:
        raise _invalid_url()
    return NormalizedUrl(
        url=normalized,
        scheme=scheme,
        host=host,
        port=effective_port,
        target=target,
    )


def _has_single_decoded_dot_segment(path: str) -> bool:
    decoded_path = unquote_to_bytes(path)
    return any(
        segment in {b".", b".."} for segment in decoded_path.split(b"/")
    )


def resolve_public_target(url: NormalizedUrl, resolver: Resolver) -> VerifiedTarget:
    try:
        literal = ip_address(url.host)
    except ValueError:
        try:
            raw_addresses = tuple(resolver(url.host, url.port))
        except Exception:
            raise _fetch_failed() from None
    else:
        raw_addresses = (str(literal),)

    if not raw_addresses:
        raise _fetch_failed()

    addresses: list[str] = []
    for raw_address in raw_addresses:
        try:
            address = ip_address(raw_address)
        except ValueError:
            raise _fetch_failed() from None
        if not is_public_unicast(address):
            raise ToolError(
                "web_url_blocked",
                "The URL points to a local or otherwise blocked address.",
            )
        canonical = address.compressed
        if canonical not in addresses:
            addresses.append(canonical)

    default_port = url.port == (443 if url.scheme == "https" else 80)
    display_host = f"[{url.host}]" if ":" in url.host else url.host
    host_header = display_host if default_port else f"{display_host}:{url.port}"
    return VerifiedTarget(
        url=url,
        addresses=tuple(addresses),
        connect_host=addresses[0],
        host_header=host_header,
        sni_hostname=url.host,
    )


def _invalid_url() -> ToolError:
    return ToolError("web_url_invalid", "The URL format is not supported.")


def _fetch_failed() -> ToolError:
    return ToolError("web_fetch_failed", "The web page could not be fetched.")
