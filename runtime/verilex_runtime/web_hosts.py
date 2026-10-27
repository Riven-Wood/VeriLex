"""Pure hostname classification shared by web tools and audit metadata."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
import re


_LEGACY_IPV4_PART = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)", re.IGNORECASE)
_SPECIAL_HOST_SUFFIXES = (
    "localhost",
    "local",
    "localdomain",
    "internal",
    "lan",
    "test",
    "invalid",
    "example",
    "home.arpa",
    "onion",
)

# Keep public-literal behavior stable across every Python version supported by
# runtime/pyproject.toml. These ranges are the conservative contract shared by
# the bundled sidecar and the Desktop persisted-source validator; networking
# still resolves and verifies every address immediately before connecting.
_IPV4_SHARED = ip_network("100.64.0.0/10")
_IPV4_PUBLIC_EXCEPTIONS = (
    ip_network("192.0.0.9/32"),
    ip_network("192.0.0.10/32"),
)
_IPV4_BLOCKED = tuple(
    ip_network(value)
    for value in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)
_IPV6_PUBLIC = ip_network("2000::/3")
_IPV6_PROTOCOL_ASSIGNMENTS = ip_network("2001::/23")
_IPV6_PUBLIC_EXCEPTIONS = tuple(
    ip_network(value)
    for value in (
        "2001:1::1/128",
        "2001:1::2/128",
        "2001:3::/32",
        "2001:4:112::/48",
        "2001:20::/28",
        "2001:30::/28",
    )
)
_IPV6_BLOCKED = tuple(
    ip_network(value)
    for value in (
        "2001:db8::/32",
        "2002::/16",
        "3fff::/20",
    )
)


def is_obviously_nonpublic_host(host: str) -> bool:
    """Reject literal/local host forms without performing a DNS lookup.

    Callers must still validate ordinary hostname syntax and resolve DNS before
    connecting. This catches numeric forms that browsers and system resolvers
    may reinterpret as IPv4 even though ``ipaddress`` treats them as names.
    """
    try:
        address = ip_address(host)
    except ValueError:
        parts = host.split(".")
        legacy_ipv4 = 1 <= len(parts) <= 4 and all(
            _LEGACY_IPV4_PART.fullmatch(part) is not None for part in parts
        )
        special_name = any(
            host == suffix or host.endswith(f".{suffix}")
            for suffix in _SPECIAL_HOST_SUFFIXES
        )
        return legacy_ipv4 or special_name
    return not is_public_unicast(address)


def is_public_unicast(address: IPv4Address | IPv6Address) -> bool:
    """Return stable, conservative public-unicast policy for a parsed address."""
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if isinstance(address, IPv4Address):
        if any(address in network for network in _IPV4_PUBLIC_EXCEPTIONS):
            return True
        return address not in _IPV4_SHARED and not any(
            address in network for network in _IPV4_BLOCKED
        )
    if address not in _IPV6_PUBLIC:
        return False
    if address in _IPV6_PROTOCOL_ASSIGNMENTS and not any(
        address in network for network in _IPV6_PUBLIC_EXCEPTIONS
    ):
        return False
    return not any(address in network for network in _IPV6_BLOCKED)


__all__ = ["is_obviously_nonpublic_host", "is_public_unicast"]
