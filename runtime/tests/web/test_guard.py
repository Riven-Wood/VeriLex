from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address

import pytest

from verilex_runtime.tools.registry import ToolError
from verilex_runtime.web.guard import (
    NormalizedUrl,
    normalize_public_url,
    resolve_public_target,
)
from verilex_runtime.web_hosts import is_public_unicast


class FakeResolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        return self.addresses


def test_public_unicast_policy_is_stable_across_python_ipaddress_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        IPv4Address,
        "is_global",
        property(lambda _address: False),
    )
    monkeypatch.setattr(
        IPv6Address,
        "is_global",
        property(lambda _address: False),
    )

    for value in (
        "8.8.8.8",
        "192.0.0.9",
        "192.0.0.10",
        "192.88.99.1",
        "::ffff:8.8.8.8",
        "2000::1",
        "2001:1::1",
        "2001:3::1",
        "2001:4:112::1",
        "2001:20::1",
        "2001:30::1",
        "3fff:1000::1",
    ):
        assert is_public_unicast(ip_address(value)), value

    for value in (
        "192.0.0.8",
        "198.51.100.1",
        "240.0.0.1",
        "::ffff:127.0.0.1",
        "64:ff9b::1",
        "100::1",
        "2001:2::1",
        "2001:10::1",
        "2001:40::1",
        "2001:db8::1",
        "2002::1",
        "3fff::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
    ):
        assert not is_public_unicast(ip_address(value)), value


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.test/file",
        "https:///missing-host",
        "https://user@example.test/",
        "https://user:password@example.test/",
        "https://example.test:0/",
        "https://example.test:99999/",
        "https://exa mple.test/",
        "https://example.test/a\\b",
        "https://example.test/%0d%0aInjected:yes",
        "https://example.test/" + ("x" * 2049),
    ],
)
def test_malformed_or_unsupported_url_is_rejected(url: str) -> None:
    with pytest.raises(ToolError) as captured:
        normalize_public_url(url)

    assert captured.value.code == "web_url_invalid"


def test_url_is_normalized_without_fragment_or_default_port() -> None:
    normalized = normalize_public_url(" HTTPS://Example.Test:443/a?q=1#private ")

    assert normalized == NormalizedUrl(
        url="https://example.test/a?q=1",
        scheme="https",
        host="example.test",
        port=443,
        target="/a?q=1",
    )


def test_canonical_unicode_path_and_query_are_preserved() -> None:
    value = "https://example.test/法律?标题=合同"

    assert normalize_public_url(value).url == value


def test_url_limit_counts_unicode_code_points() -> None:
    prefix = "https://example.test/法律?标题="
    at_limit = prefix + ("😀" * (2048 - len(prefix)))

    assert normalize_public_url(at_limit).url == at_limit
    with pytest.raises(ToolError) as captured:
        normalize_public_url(at_limit + "字")

    assert captured.value.code == "web_url_invalid"


@pytest.mark.parametrize(
    "path",
    [
        "/../case",
        "/./case",
        "/%2e%2e/case",
        "/.%2E/case",
        "/%2e./case",
        "/%2F..%2fcase",
        "/name%2f.%2Fcase",
    ],
)
def test_single_decoded_dot_path_segment_is_rejected(path: str) -> None:
    with pytest.raises(ToolError) as captured:
        normalize_public_url(f"https://example.test{path}")

    assert captured.value.code == "web_url_invalid"


@pytest.mark.parametrize(
    "path",
    [
        "/法律/合同",
        "/%2Ename/case",
        "/name../case",
        "/%252e%252e/case",
    ],
)
def test_non_dot_path_segments_remain_valid(path: str) -> None:
    value = f"https://example.test{path}"

    assert normalize_public_url(value).url == value


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "192.0.2.1",
        "[::1]",
        "[fc00::1]",
        "[fe80::1]",
        "[fec0::1]",
        "[::]",
        "[ff02::1]",
        "[2001:db8::1]",
        "[::ffff:127.0.0.1]",
    ],
)
def test_non_public_literal_target_is_blocked_without_dns(host: str) -> None:
    resolver = FakeResolver("93.184.216.34")

    with pytest.raises(ToolError) as captured:
        resolve_public_target(normalize_public_url(f"http://{host}/x"), resolver)

    assert captured.value.code == "web_url_blocked"
    assert resolver.calls == []


def test_mixed_public_and_private_dns_answer_is_blocked() -> None:
    resolver = FakeResolver("93.184.216.34", "127.0.0.1")

    with pytest.raises(ToolError) as captured:
        resolve_public_target(
            normalize_public_url("https://example.test"), resolver
        )

    assert captured.value.code == "web_url_blocked"


def test_site_local_ipv6_dns_answer_is_blocked() -> None:
    resolver = FakeResolver("fec0::1")

    with pytest.raises(ToolError) as captured:
        resolve_public_target(
            normalize_public_url("https://example.test"), resolver
        )

    assert captured.value.code == "web_url_blocked"


def test_mixed_public_and_site_local_ipv6_dns_answer_is_blocked() -> None:
    resolver = FakeResolver("2606:2800:220:1:248:1893:25c8:1946", "fec0::1")

    with pytest.raises(ToolError) as captured:
        resolve_public_target(
            normalize_public_url("https://example.test"), resolver
        )

    assert captured.value.code == "web_url_blocked"


def test_all_dns_answers_are_verified_and_preserved_for_pinned_connection() -> None:
    resolver = FakeResolver("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")

    target = resolve_public_target(
        normalize_public_url("https://example.test:8443/case?id=7"), resolver
    )

    assert target.url.url == "https://example.test:8443/case?id=7"
    assert target.addresses == (
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    )
    assert target.connect_host == "93.184.216.34"
    assert target.host_header == "example.test:8443"
    assert target.sni_hostname == "example.test"
    assert resolver.calls == [("example.test", 8443)]


def test_duplicate_dns_answers_are_canonicalized() -> None:
    resolver = FakeResolver("93.184.216.34", "93.184.216.34")

    target = resolve_public_target(
        normalize_public_url("http://example.test"), resolver
    )

    assert target.addresses == ("93.184.216.34",)


def test_empty_or_failed_dns_resolution_maps_to_redacted_fetch_failure() -> None:
    def failing_resolver(host: str, port: int) -> tuple[str, ...]:
        raise OSError("resolver secret 127.0.0.1")

    with pytest.raises(ToolError) as captured:
        resolve_public_target(
            normalize_public_url("https://example.test"), failing_resolver
        )

    assert captured.value.code == "web_fetch_failed"
    assert "127.0.0.1" not in str(captured.value)
