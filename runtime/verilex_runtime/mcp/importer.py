from __future__ import annotations

import base64
import ipaddress
import json
import shlex
import unicodedata
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping, Sequence
from urllib.parse import parse_qsl, unquote_to_bytes, urlsplit, urlunsplit

import httpx

from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.security import (
    secret_placement_codes,
    stable_query_pairs,
)


MAX_IMPORT_BYTES = 256 * 1024
MAX_IMPORT_SERVERS = 32
MAX_JSON_DEPTH = 64
MAX_FIELD_BYTES = 64 * 1024
MAX_COLLECTION_ITEMS = 512
DEFAULT_CONNECT_TIMEOUT_MS = 10_000
DEFAULT_REQUEST_TIMEOUT_MS = 30_000
MAX_TIMEOUT_MS = 10 * 60 * 1000

_MCP_TRANSPORTS = {"stdio", "http", "https", "sse", "streamable-http", "streamable_http"}
_SERVER_FIELDS = {
    "name",
    "displayName",
    "display_name",
    "type",
    "transport",
    "command",
    "args",
    "cwd",
    "url",
    "env",
    "headers",
    "connectTimeoutMs",
    "connect_timeout_ms",
    "requestTimeoutMs",
    "request_timeout_ms",
    "enabled",
    "requires_approval",
}
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _invalid() -> MCPDomainError:
    return MCPDomainError("mcp_config_invalid", retryable=False)


@dataclass(frozen=True, slots=True)
class MCPServerDraft:
    display_name: str
    transport: Literal["stdio", "http", "sse"]
    command: str | None = field(default=None, repr=False)
    args: tuple[str, ...] = field(default_factory=tuple, repr=False)
    cwd: str | None = field(default=None, repr=False)
    url: str | None = field(default=None, repr=False)
    env: Mapping[str, str] = field(default_factory=dict, repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    secret_env_keys: tuple[str, ...] = ()
    secret_header_keys: tuple[str, ...] = ()
    connect_timeout_ms: int = DEFAULT_CONNECT_TIMEOUT_MS
    request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(self, "secret_env_keys", tuple(self.secret_env_keys))
        object.__setattr__(self, "secret_header_keys", tuple(self.secret_header_keys))

    @property
    def plain_env(self) -> dict[str, str]:
        secret = frozenset(self.secret_env_keys)
        return {key: value for key, value in self.env.items() if key not in secret}

    @property
    def secret_env(self) -> dict[str, str]:
        secret = frozenset(self.secret_env_keys)
        return {key: value for key, value in self.env.items() if key in secret}

    @property
    def plain_headers(self) -> dict[str, str]:
        secret = {key.casefold() for key in self.secret_header_keys}
        return {
            key: value for key, value in self.headers.items() if key.casefold() not in secret
        }

    @property
    def secret_headers(self) -> dict[str, str]:
        secret = {key.casefold() for key in self.secret_header_keys}
        return {
            key: value for key, value in self.headers.items() if key.casefold() in secret
        }


@dataclass(frozen=True, slots=True)
class LegacyMCPEntry:
    source_name: str
    draft: MCPServerDraft
    legacy_enabled: bool | None
    legacy_requires_approval: bool | None


class _DuplicateKey(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKey
        value[key] = item
    return value


def _load_json(text: str) -> object:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
        raise _invalid() from None
    _validate_json_tree(value)
    return value


def _validate_json_tree(root: object) -> None:
    stack: list[tuple[object, int]] = [(root, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > 100_000:
            raise _invalid()
        if isinstance(value, dict):
            if len(value) > MAX_COLLECTION_ITEMS:
                raise _invalid()
            for key, child in value.items():
                if not isinstance(key, str):
                    raise _invalid()
                _validate_text(key, allow_empty=False)
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            if len(value) > MAX_COLLECTION_ITEMS:
                raise _invalid()
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, str):
            _validate_text(value, allow_empty=True)
        elif value is None or isinstance(value, (bool, int, float)):
            continue
        else:
            raise _invalid()


def _validate_text(value: str, *, allow_empty: bool) -> str:
    if not allow_empty and not value:
        raise _invalid()
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        raise _invalid() from None
    if len(encoded) > MAX_FIELD_BYTES:
        raise _invalid()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _invalid()
    return value


def _name(value: str) -> str:
    normalized = unicodedata.normalize("NFC", _validate_text(value, allow_empty=False).strip())
    if not normalized or len(normalized.encode("utf-8")) > 256:
        raise _invalid()
    return normalized


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid()
    return _validate_text(value, allow_empty=False)


def _string_sequence(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > MAX_COLLECTION_ITEMS:
        raise _invalid()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise _invalid()
        result.append(_validate_text(item, allow_empty=True))
    return tuple(result)


def _string_mapping(value: object, *, case_insensitive_keys: bool = False) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_COLLECTION_ITEMS:
        raise _invalid()
    result: dict[str, str] = {}
    seen: set[str] = set()
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise _invalid()
        normalized_key = _validate_text(key, allow_empty=False)
        comparable = normalized_key.casefold() if case_insensitive_keys else normalized_key
        if comparable in seen:
            raise _invalid()
        seen.add(comparable)
        result[normalized_key] = _validate_text(item, allow_empty=True)
    return result


def _timeout(raw: Mapping[str, object], camel: str, snake: str, default: int) -> int:
    value = raw.get(camel, raw.get(snake, default))
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid()
    if value <= 0 or value > MAX_TIMEOUT_MS:
        raise _invalid()
    return value


def _normalize_url(value: str) -> str:
    if (
        "\\" in value
        or any(character.isspace() for character in value)
        or not _has_valid_percent_encoding(value)
    ):
        raise _invalid()
    try:
        decoded_once = unquote_to_bytes(value).decode("utf-8", "strict")
    except UnicodeError:
        raise _invalid() from None
    if "\\" in decoded_once or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in decoded_once
    ):
        raise _invalid()
    try:
        parsed = urlsplit(_validate_text(value, allow_empty=False))
        port = parsed.port
    except ValueError:
        raise _invalid() from None
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.netloc.count("@") > 1
    ):
        raise _invalid()
    if parsed.fragment:
        raise _invalid()
    authority = parsed.netloc.rsplit("@", 1)[-1]
    if authority.endswith(":"):
        raise _invalid()
    if port is not None and port <= 0:
        raise _invalid()
    if stable_query_pairs(parsed.query) is None:
        raise _invalid()
    hostname = _normalize_hostname(value)
    if ":" in hostname:
        hostname = f"[{hostname}]"
    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo += ":" + parsed.password
        userinfo += "@"
    netloc = userinfo + hostname
    if port is not None:
        netloc += f":{port}"
    return urlunsplit(
        (parsed.scheme.casefold(), netloc, parsed.path or "", parsed.query, parsed.fragment)
    )


def _has_valid_percent_encoding(value: str) -> bool:
    index = 0
    while index < len(value):
        if value[index] != "%":
            index += 1
            continue
        if (
            index + 2 >= len(value)
            or value[index + 1] not in _HEX_DIGITS
            or value[index + 2] not in _HEX_DIGITS
        ):
            return False
        index += 3
    return True


def _normalize_hostname(url: str) -> str:
    try:
        hostname = httpx.URL(url).raw_host.decode("ascii", "strict").casefold()
    except (httpx.InvalidURL, UnicodeError):
        raise _invalid() from None
    if not hostname:
        raise _invalid()
    if "%" in hostname:
        raise _invalid()
    if ":" in hostname:
        try:
            return ipaddress.IPv6Address(hostname).compressed
        except ipaddress.AddressValueError:
            raise _invalid() from None
    if hostname.replace(".", "").isdigit():
        try:
            return str(ipaddress.IPv4Address(hostname))
        except ipaddress.AddressValueError:
            raise _invalid() from None
    labels = hostname.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not all(character.isalnum() or character == "-" for character in label)
        for label in labels
    ):
        raise _invalid()
    return hostname


def _secret_env_keys(env: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(env))


def _secret_header_keys(headers: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(headers))


def _transport(raw: Mapping[str, object], has_url: bool) -> Literal["stdio", "http", "sse"]:
    declared = raw.get("transport", raw.get("type"))
    if declared is None:
        return "http" if has_url else "stdio"
    if not isinstance(declared, str) or declared.casefold() not in _MCP_TRANSPORTS:
        raise _invalid()
    lowered = declared.casefold()
    if lowered == "sse":
        return "sse"
    if lowered in {"http", "https", "streamable-http", "streamable_http"}:
        return "http"
    return "stdio"


def _infer_name(raw: Mapping[str, object]) -> str:
    for key in ("name", "displayName", "display_name"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return _name(value)
    command = raw.get("command")
    args = raw.get("args")
    if isinstance(args, list):
        for item in args:
            if isinstance(item, str) and item and not item.startswith("-"):
                return _name(item.rsplit("/", 1)[-1])
    if isinstance(command, str) and command:
        return _name(command.rsplit("/", 1)[-1])
    url = raw.get("url")
    if isinstance(url, str):
        try:
            host = urlsplit(url).hostname
        except ValueError:
            host = None
        if host:
            return _name(host)
    return "MCP 服务器"


def _draft_from_raw(raw: object, *, supplied_name: str | None = None) -> MCPServerDraft:
    if not isinstance(raw, dict):
        raise _invalid()
    if any(not isinstance(key, str) for key in raw):
        raise _invalid()
    if set(raw) - _SERVER_FIELDS:
        raise _invalid()
    for key in ("enabled", "requires_approval"):
        if key in raw and not isinstance(raw[key], bool):
            raise _invalid()
    for key in ("name", "displayName", "display_name"):
        if key in raw and not isinstance(raw[key], str):
            raise _invalid()
    for aliases in (
        ("type", "transport"),
        ("name", "displayName", "display_name"),
        ("connectTimeoutMs", "connect_timeout_ms"),
        ("requestTimeoutMs", "request_timeout_ms"),
    ):
        if sum(key in raw for key in aliases) > 1:
            raise _invalid()
    command = _optional_string(raw.get("command"))
    url_value = _optional_string(raw.get("url"))
    if bool(command) == bool(url_value):
        raise _invalid()
    url = _normalize_url(url_value) if url_value is not None else None
    transport = _transport(raw, url is not None)
    if transport == "stdio" and command is None:
        raise _invalid()
    if transport != "stdio" and url is None:
        raise _invalid()
    env = _string_mapping(raw.get("env"))
    headers = _string_mapping(raw.get("headers"), case_insensitive_keys=True)
    draft = MCPServerDraft(
        display_name=_name(supplied_name) if supplied_name is not None else _infer_name(raw),
        transport=transport,
        command=command,
        args=_string_sequence(raw.get("args")),
        cwd=_optional_string(raw.get("cwd")),
        url=url,
        env=env,
        headers=headers,
        secret_env_keys=_secret_env_keys(env),
        secret_header_keys=_secret_header_keys(headers),
        connect_timeout_ms=_timeout(
            raw, "connectTimeoutMs", "connect_timeout_ms", DEFAULT_CONNECT_TIMEOUT_MS
        ),
        request_timeout_ms=_timeout(
            raw, "requestTimeoutMs", "request_timeout_ms", DEFAULT_REQUEST_TIMEOUT_MS
        ),
    )
    if not secret_placement_codes(draft):
        from verilex_runtime.mcp.canonical import canonical_connection_config_bytes

        canonical_connection_config_bytes(draft)
    return draft


def _entries_from_json(value: object) -> list[tuple[str, Mapping[str, object]]]:
    if not isinstance(value, dict):
        raise _invalid()
    if "mcpServers" in value:
        if set(value) != {"mcpServers"}:
            raise _invalid()
        servers = value["mcpServers"]
        if not isinstance(servers, dict):
            raise _invalid()
        entries = list(servers.items())
    elif isinstance(value.get("command"), str) or isinstance(value.get("url"), str):
        name = _infer_name(value)
        entries = [(name, value)]
    else:
        entries = list(value.items())
    if not entries or len(entries) > MAX_IMPORT_SERVERS:
        raise _invalid()
    normalized: list[tuple[str, Mapping[str, object]]] = []
    for source_name, raw in entries:
        if not isinstance(source_name, str) or not isinstance(raw, dict):
            raise _invalid()
        normalized.append((_name(source_name), raw))
    return normalized


def _parse_cursor_deep_link(text: str) -> MCPServerDraft | None:
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme.casefold() != "cursor":
        return None
    if (
        parsed.netloc.casefold() != "anysphere.cursor-deeplink"
        or parsed.path != "/mcp/install"
        or parsed.fragment
        or not _has_valid_percent_encoding(text)
    ):
        raise _invalid()
    query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    if len(query) != 2 or {key for key, _value in query} != {"name", "config"}:
        raise _invalid()
    values = dict(query)
    name = values["name"]
    config_text = values["config"]
    if not config_text.lstrip().startswith("{"):
        try:
            padding = "=" * (-len(config_text) % 4)
            config_text = base64.b64decode(
                config_text + padding, altchars=b"-_", validate=True
            ).decode("utf-8")
        except (ValueError, UnicodeError):
            raise _invalid() from None
    if len(config_text.encode("utf-8", "strict")) > MAX_IMPORT_BYTES:
        raise _invalid()
    raw = _load_json(config_text)
    return _draft_from_raw(raw, supplied_name=name)


def _parse_claude_command(text: str) -> MCPServerDraft | None:
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        raise _invalid() from None
    if tokens[:3] != ["claude", "mcp", "add"]:
        return None
    remainder = tokens[3:]
    declared_transport: str | None = None
    env: dict[str, str] = {}
    headers: dict[str, str] = {}
    positional: list[str] = []
    index = 0
    saw_delimiter = False
    while index < len(remainder):
        token = remainder[index]
        if token == "--":
            saw_delimiter = True
            positional.extend(remainder[index + 1 :])
            break
        inline_value: str | None = None
        option = token
        if token.startswith("--") and "=" in token:
            option, inline_value = token.split("=", 1)
        if option in {"--transport", "-t", "--env", "-e", "--header", "--scope", "-s"}:
            if inline_value is None and index + 1 >= len(remainder):
                raise _invalid()
            value = inline_value if inline_value is not None else remainder[index + 1]
            if option in {"--transport", "-t"}:
                declared_transport = value
            elif option in {"--scope", "-s"}:
                if value not in {"local", "project", "user"}:
                    raise _invalid()
            else:
                separator = (
                    "="
                    if "=" in value
                    else ":"
                    if option == "--header" and ":" in value
                    else None
                )
                if separator is None:
                    raise _invalid()
                key, item = value.split(separator, 1)
                key = key.strip()
                item = item.lstrip()
                _validate_text(key, allow_empty=False)
                _validate_text(item, allow_empty=True)
                target = headers if option == "--header" else env
                comparable = key.casefold() if option == "--header" else key
                if any(
                    (present.casefold() if option == "--header" else present) == comparable
                    for present in target
                ):
                    raise _invalid()
                target[key] = item
            index += 1 if inline_value is not None else 2
            continue
        if token.startswith("-"):
            raise _invalid()
        positional.append(token)
        index += 1
    if not saw_delimiter and len(positional) > 2:
        raise _invalid()
    if len(positional) < 2:
        raise _invalid()
    name, target, *args = positional
    lowered = (declared_transport or "").casefold()
    is_remote = lowered in {"http", "https", "sse", "streamable-http", "streamable_http"}
    if not declared_transport:
        is_remote = target.casefold().startswith(("http://", "https://"))
    raw: dict[str, object] = {"env": env, "headers": headers}
    if declared_transport:
        raw["type"] = declared_transport
    if is_remote:
        if args:
            raise _invalid()
        raw["url"] = target
    else:
        raw["command"] = target
        raw["args"] = args
    return _draft_from_raw(raw, supplied_name=name)


def _parse_stdio_command(text: str) -> MCPServerDraft | None:
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        raise _invalid() from None
    if not tokens or tokens[0].casefold().startswith(("http://", "https://")):
        return None
    return _draft_from_raw(
        {"command": tokens[0], "args": tokens[1:]},
        supplied_name=_infer_name({"command": tokens[0], "args": tokens[1:]}),
    )


def parse_mcp_import(text: str) -> Sequence[MCPServerDraft]:
    if not isinstance(text, str):
        raise _invalid()
    try:
        encoded_size = len(text.encode("utf-8", "strict"))
    except UnicodeError:
        raise _invalid() from None
    if encoded_size == 0 or encoded_size > MAX_IMPORT_BYTES:
        raise _invalid()
    stripped = text.strip()
    if not stripped or any(ord(character) == 0 for character in stripped):
        raise _invalid()
    if stripped.startswith(("{", "[")):
        value = _load_json(stripped)
        return tuple(
            _draft_from_raw(raw, supplied_name=name)
            for name, raw in _entries_from_json(value)
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in stripped):
        raise _invalid()
    cursor = _parse_cursor_deep_link(stripped)
    if cursor is not None:
        return (cursor,)
    claude = _parse_claude_command(stripped)
    if claude is not None:
        return (claude,)
    stdio = _parse_stdio_command(stripped)
    if stdio is not None:
        return (stdio,)
    if stripped.casefold().startswith(("http://", "https://")):
        return (_draft_from_raw({"url": stripped}),)
    raise _invalid()


def parse_legacy_mcp_json(raw_text: str) -> Sequence[LegacyMCPEntry]:
    if not isinstance(raw_text, str):
        raise _invalid()
    try:
        size = len(raw_text.encode("utf-8", "strict"))
    except UnicodeError:
        raise _invalid() from None
    if size == 0 or size > MAX_IMPORT_BYTES:
        raise _invalid()
    value = _load_json(raw_text)
    entries = _entries_from_json(value)
    result: list[LegacyMCPEntry] = []
    for source_name, raw in entries:
        enabled = raw.get("enabled")
        approval = raw.get("requires_approval")
        if enabled is not None and not isinstance(enabled, bool):
            raise _invalid()
        if approval is not None and not isinstance(approval, bool):
            raise _invalid()
        result.append(
            LegacyMCPEntry(
                source_name=source_name,
                draft=_draft_from_raw(raw, supplied_name=source_name),
                legacy_enabled=enabled,
                legacy_requires_approval=approval,
            )
        )
    return tuple(result)
