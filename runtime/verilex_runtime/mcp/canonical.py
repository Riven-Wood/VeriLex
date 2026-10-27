from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.security import (
    is_sensitive_header_name,
    secret_placement_codes,
)

if TYPE_CHECKING:
    from verilex_runtime.mcp.importer import MCPServerDraft


def _invalid() -> MCPDomainError:
    return MCPDomainError("mcp_config_invalid", retryable=False)


def _valid_text(
    value: object, *, allow_empty: bool = False, allow_none: bool = False
) -> bool:
    if value is None:
        return allow_none
    if not isinstance(value, str) or (not value and not allow_empty):
        return False
    try:
        if len(value.encode("utf-8", "strict")) > 64 * 1024:
            return False
    except UnicodeError:
        return False
    return not any(ord(character) < 32 or ord(character) == 127 for character in value)


def canonical_connection_config_bytes(draft: MCPServerDraft) -> bytes:
    try:
        if draft.transport not in {"stdio", "http", "sse"}:
            raise _invalid()
        timeout_bounds = (
            (draft.connect_timeout_ms, 120_000),
            (draft.request_timeout_ms, 600_000),
        )
        for timeout, maximum in timeout_bounds:
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, int)
                or timeout < 1_000
                or timeout > maximum
            ):
                raise _invalid()
        if (
            len(draft.args) > 512
            or len(draft.env) > 512
            or len(draft.headers) > 512
            or len(draft.secret_env_keys) > 512
            or len(draft.secret_header_keys) > 512
        ):
            raise _invalid()
        if not isinstance(draft.args, tuple) or not all(
            isinstance(item, str) and _valid_text(item, allow_empty=True) for item in draft.args
        ):
            raise _invalid()
        if not isinstance(draft.env, Mapping) or not isinstance(draft.headers, Mapping):
            raise _invalid()
        if not all(
            _valid_text(key)
            and isinstance(item, str)
            and _valid_text(item, allow_empty=True)
            for mapping in (draft.env, draft.headers)
            for key, item in mapping.items()
        ):
            raise _invalid()
        header_names = [key.casefold() for key in draft.headers]
        if len(header_names) != len(set(header_names)):
            raise _invalid()
        if draft.transport == "stdio":
            if not _valid_text(draft.command) or draft.url is not None or draft.headers:
                raise _invalid()
        else:
            if draft.command is not None or draft.args or draft.cwd is not None or draft.env:
                raise _invalid()
            if not _valid_text(draft.url):
                raise _invalid()
            parsed = urlsplit(draft.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise _invalid()
        if draft.cwd is not None and not _valid_text(draft.cwd):
            raise _invalid()
        secret_env_keys = tuple(sorted(set(draft.secret_env_keys)))
        secret_header_keys = tuple(sorted(set(draft.secret_header_keys)))
        if len(secret_env_keys) != len(draft.secret_env_keys) or len(
            secret_header_keys
        ) != len(draft.secret_header_keys):
            raise _invalid()
        folded_secret_headers = [key.casefold() for key in secret_header_keys]
        if len(folded_secret_headers) != len(set(folded_secret_headers)):
            raise _invalid()
        if not all(_valid_text(key) for key in (*secret_env_keys, *secret_header_keys)):
            raise _invalid()
        if any(key not in draft.env for key in secret_env_keys):
            raise _invalid()
        header_lookup = {key.casefold() for key in draft.headers}
        if any(key.casefold() not in header_lookup for key in secret_header_keys):
            raise _invalid()
        secret_header_lookup = {key.casefold() for key in secret_header_keys}
        if any(
            is_sensitive_header_name(key) and key.casefold() not in secret_header_lookup
            for key in draft.headers
        ):
            raise _invalid()
        if secret_placement_codes(draft):
            raise _invalid()
        value = {
            "transport": draft.transport,
            "command": draft.command,
            "args": list(draft.args),
            "cwd": draft.cwd,
            "url": draft.url,
            "env": draft.plain_env,
            "headers": draft.plain_headers,
            "secretEnvKeys": list(secret_env_keys),
            "secretHeaderKeys": list(secret_header_keys),
            "connectTimeoutMs": draft.connect_timeout_ms,
            "requestTimeoutMs": draft.request_timeout_ms,
        }
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8", "strict")
    except MCPDomainError:
        raise
    except (AttributeError, TypeError, ValueError, UnicodeError):
        raise _invalid() from None


def connection_config_fingerprint(draft: MCPServerDraft) -> str:
    return hashlib.sha256(canonical_connection_config_bytes(draft)).hexdigest()


def _unsupported_schema() -> MCPDomainError:
    return MCPDomainError("mcp_schema_unsupported", retryable=False)


def canonical_schema_bytes(schema: Mapping[str, object]) -> bytes:
    """Return the one canonical byte representation for an MCP tool schema."""
    if not isinstance(schema, Mapping):
        raise _unsupported_schema()
    try:
        root = dict(schema)
        active: set[int] = set()
        stack: list[tuple[object, bool]] = [(root, False)]
        while stack:
            current, leaving = stack.pop()
            if leaving:
                active.remove(id(current))
                continue
            if type(current) is dict:
                identity = id(current)
                if identity in active:
                    raise _unsupported_schema()
                active.add(identity)
                stack.append((current, True))
                for key, item in reversed(tuple(current.items())):
                    if type(key) is not str:
                        raise _unsupported_schema()
                    stack.append((item, False))
            elif type(current) is list:
                identity = id(current)
                if identity in active:
                    raise _unsupported_schema()
                active.add(identity)
                stack.append((current, True))
                for item in reversed(current):
                    stack.append((item, False))
            elif current is None or type(current) in {str, bool, int}:
                continue
            elif type(current) is float and math.isfinite(current):
                continue
            else:
                raise _unsupported_schema()
        return json.dumps(
            root,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8", "strict")
    except MCPDomainError:
        raise
    except (RecursionError, TypeError, ValueError, UnicodeError):
        raise _unsupported_schema() from None
    except Exception:
        raise _unsupported_schema() from None


def schema_hash(schema: Mapping[str, object]) -> str:
    """Hash the shared canonical bytes instead of reserializing a schema."""
    return hashlib.sha256(canonical_schema_bytes(schema)).hexdigest()
