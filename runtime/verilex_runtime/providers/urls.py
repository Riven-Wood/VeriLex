from ipaddress import ip_address
from urllib.parse import urlsplit

from verilex_runtime.providers.errors import provider_error
from verilex_runtime.storage.models import ProviderKind


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_provider_url(kind: ProviderKind, value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
        or any(delimiter in value for delimiter in ("\\", "?", "#"))
    ):
        raise provider_error("invalid_provider_url")

    try:
        parsed = urlsplit(value)
        parsed.port
    except (TypeError, ValueError):
        raise provider_error("invalid_provider_url") from None

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or parsed.query
        or parsed.fragment
    ):
        raise provider_error("invalid_provider_url")

    if parsed.scheme == "http" and not is_loopback_host(parsed.hostname):
        raise provider_error("insecure_remote_url")

    if parsed.scheme == "http" and parsed.hostname.lower() == "localhost":
        netloc = "127.0.0.1"
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        value = parsed._replace(netloc=netloc).geturl()

    return value.rstrip("/")
