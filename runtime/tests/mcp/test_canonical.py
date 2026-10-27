from __future__ import annotations

from dataclasses import replace
import hashlib
from types import MappingProxyType

import pytest

from verilex_runtime.mcp.canonical import (
    canonical_schema_bytes,
    canonical_connection_config_bytes,
    connection_config_fingerprint,
    schema_hash,
)
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.importer import MCPServerDraft


def _draft(**changes: object) -> MCPServerDraft:
    values: dict[str, object] = {
        "display_name": "法律工具",
        "transport": "stdio",
        "command": "npx",
        "args": ("-y", "law-mcp"),
        "cwd": None,
        "url": None,
        "env": {"TOKEN": "secret", "LANG": "zh_CN.UTF-8"},
        "headers": {},
        "secret_env_keys": ("TOKEN",),
        "secret_header_keys": (),
        "connect_timeout_ms": 10_000,
        "request_timeout_ms": 30_000,
    }
    values.update(changes)
    return MCPServerDraft(**values)


def test_canonical_bytes_are_exact_sorted_compact_utf8_and_secret_free():
    draft = _draft()
    canonical = canonical_connection_config_bytes(draft)
    assert canonical == (
        b'{"args":["-y","law-mcp"],"command":"npx","connectTimeoutMs":10000,'
        b'"cwd":null,"env":{"LANG":"zh_CN.UTF-8"},"headers":{},'
        b'"requestTimeoutMs":30000,"secretEnvKeys":["TOKEN"],'
        b'"secretHeaderKeys":[],"transport":"stdio","url":null}'
    )
    assert b'"TOKEN":"secret"' not in canonical
    assert b"\\u" not in canonical


def test_canonical_ignores_display_name_secret_values_and_mapping_order():
    first = _draft()
    renamed = replace(
        first,
        display_name="另一个名字",
        env={"LANG": "zh_CN.UTF-8", "TOKEN": "replacement"},
    )
    assert canonical_connection_config_bytes(first) == canonical_connection_config_bytes(
        renamed
    )


def test_fingerprint_is_sha256_of_shared_canonical_bytes():
    draft = _draft()
    assert connection_config_fingerprint(draft) == hashlib.sha256(
        canonical_connection_config_bytes(draft)
    ).hexdigest()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonical_rejects_non_finite_timeouts_without_leaking_values(value):
    with pytest.raises(MCPDomainError) as raised:
        canonical_connection_config_bytes(_draft(request_timeout_ms=value))
    assert raised.value.code == "mcp_config_invalid"


@pytest.mark.parametrize(
    "draft",
    [
        _draft(connect_timeout_ms=999),
        _draft(connect_timeout_ms=120_001),
        _draft(request_timeout_ms=999),
        _draft(request_timeout_ms=600_001),
        _draft(transport="stdio", command=None),
        _draft(transport="http", command="npx", url="https://example.test/mcp"),
        _draft(args=("ok", 7)),
        _draft(env={"LANG": 7, "TOKEN": "secret"}),
        _draft(args=tuple("x" for _ in range(513))),
        _draft(headers={"Accept": "application/json"}),
        _draft(
            transport="http",
            command=None,
            args=(),
            url="https://example.test/mcp",
            env={"TOKEN": "secret"},
        ),
    ],
)
def test_canonical_revalidates_constructed_drafts_instead_of_trusting_callers(draft):
    with pytest.raises(MCPDomainError) as raised:
        canonical_connection_config_bytes(draft)
    assert raised.value.code == "mcp_config_invalid"


@pytest.mark.parametrize(
    ("connect_timeout_ms", "request_timeout_ms"),
    ((1_000, 1_000), (120_000, 600_000)),
)
def test_canonical_accepts_the_database_timeout_boundaries(
    connect_timeout_ms,
    request_timeout_ms,
):
    assert canonical_connection_config_bytes(
        _draft(
            connect_timeout_ms=connect_timeout_ms,
            request_timeout_ms=request_timeout_ms,
        )
    )


def test_draft_defensively_copies_mutable_connection_inputs():
    args = ["-y", "law-mcp"]
    env = {"LANG": "zh_CN.UTF-8", "TOKEN": "first"}
    headers: dict[str, str] = {}
    secret_env_keys = ["TOKEN"]
    draft = MCPServerDraft(
        display_name="law",
        transport="stdio",
        command="npx",
        args=args,  # type: ignore[arg-type]
        env=env,
        headers=headers,
        secret_env_keys=secret_env_keys,  # type: ignore[arg-type]
    )
    before = canonical_connection_config_bytes(draft)

    args.append("mutated")
    env["TOKEN"] = "replacement"
    env["NEW"] = "late"
    headers["Authorization"] = "late-secret"
    secret_env_keys.clear()

    assert draft.args == ("-y", "law-mcp")
    assert isinstance(draft.env, MappingProxyType)
    assert isinstance(draft.headers, MappingProxyType)
    assert canonical_connection_config_bytes(draft) == before
    with pytest.raises(TypeError):
        draft.env["LATE"] = "value"  # type: ignore[index]


@pytest.mark.parametrize(
    "draft",
    [
        _draft(
            transport="http",
            command=None,
            args=(),
            url="https://example.test/mcp",
            env={},
            secret_env_keys=(),
            headers={"Authorization": "Bearer must-not-appear"},
            secret_header_keys=(),
        ),
    ],
)
def test_canonical_rejects_sensitive_fields_not_declared_as_secret(draft):
    with pytest.raises(MCPDomainError) as raised:
        canonical_connection_config_bytes(draft)
    assert raised.value.code == "mcp_config_invalid"


def test_canonical_rejects_case_insensitive_duplicate_constructed_headers():
    draft = _draft(
        transport="http",
        command=None,
        args=(),
        url="https://example.test/mcp",
        env={},
        secret_env_keys=(),
        headers={"Authorization": "one", "authorization": "two"},
        secret_header_keys=("Authorization", "authorization"),
    )
    with pytest.raises(MCPDomainError) as raised:
        canonical_connection_config_bytes(draft)
    assert raised.value.code == "mcp_config_invalid"


def test_canonical_rejects_case_insensitive_duplicate_secret_header_keys():
    draft = _draft(
        transport="http",
        command=None,
        args=(),
        url="https://example.test/mcp",
        env={},
        secret_env_keys=(),
        headers={"Authorization": "one"},
        secret_header_keys=("Authorization", "authorization"),
    )
    with pytest.raises(MCPDomainError) as raised:
        canonical_connection_config_bytes(draft)
    assert raised.value.code == "mcp_config_invalid"


@pytest.mark.parametrize(
    "text",
    [
        "https://user:must-not-appear@example.test/mcp",
        "https://example.test/mcp?access_token=must-not-appear",
        "https://example.test/mcp?%74OkEn=must-not-appear",
        "npx pkg --client-secret=must-not-appear",
        "npx pkg --header=Authorization:must-not-appear",
    ],
)
def test_canonical_rejects_secrets_embedded_in_url_or_arguments(text):
    from verilex_runtime.mcp.importer import parse_mcp_import

    [draft] = parse_mcp_import(text)
    with pytest.raises(MCPDomainError) as raised:
        canonical_connection_config_bytes(draft)
    assert raised.value.code == "mcp_config_invalid"
    assert "must-not-appear" not in str(raised.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/mcp?%2574oken=canonical-sentinel",
        "https://example.test/mcp?%252574OkEn=canonical-sentinel",
    ],
)
def test_canonical_rejects_residual_percent_escapes_in_query_names(url):
    draft = _draft(
        transport="http",
        command=None,
        args=(),
        url=url,
        env={},
        secret_env_keys=(),
    )
    with pytest.raises(MCPDomainError) as raised:
        canonical_connection_config_bytes(draft)
    assert raised.value.code == "mcp_config_invalid"
    assert "canonical-sentinel" not in str(raised.value)


def test_canonical_uses_the_importers_httpx_normalized_receiver():
    from verilex_runtime.mcp.importer import parse_mcp_import

    [draft] = parse_mcp_import("https://faß.de/mcp?q=100%25legal")
    canonical = canonical_connection_config_bytes(draft)
    assert b"https://xn--fa-hia.de/mcp?q=100%25legal" in canonical
    assert "faß.de".encode() not in canonical


def test_schema_canonical_bytes_are_sorted_compact_utf8_and_shared_with_hash():
    schema = {
        "type": "object",
        "title": "案件查询",
        "properties": {"b": {"type": "string"}, "a": {"type": "integer"}},
    }

    canonical = canonical_schema_bytes(schema)

    assert canonical == (
        '{"properties":{"a":{"type":"integer"},"b":{"type":"string"}},'
        '"title":"案件查询","type":"object"}'
    ).encode("utf-8")
    assert schema_hash(schema) == hashlib.sha256(canonical).hexdigest()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_schema_canonical_bytes_reject_non_finite_values_without_echo(value):
    with pytest.raises(MCPDomainError) as raised:
        canonical_schema_bytes({"maximum": value})

    assert raised.value.code == "mcp_schema_unsupported"
    assert str(value) not in str(raised.value)


@pytest.mark.parametrize(
    "schema",
    [
        {"properties": {1: {"type": "string"}}},
        {"enum": ("a", "b")},
        {"enum": {"a", "b"}},
    ],
)
def test_schema_canonical_bytes_accept_only_native_json_with_string_keys(schema):
    with pytest.raises(MCPDomainError) as raised:
        canonical_schema_bytes(schema)

    assert raised.value.code == "mcp_schema_unsupported"


def test_schema_canonical_bytes_reject_recursive_mapping():
    schema: dict[str, object] = {}
    schema["self"] = schema

    with pytest.raises(MCPDomainError) as raised:
        canonical_schema_bytes(schema)

    assert raised.value.code == "mcp_schema_unsupported"
