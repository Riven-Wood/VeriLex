"""Explicit environment-proxy selection for controlled web requests."""

from __future__ import annotations

from urllib.parse import urlsplit
from urllib.request import getproxies_environment, proxy_bypass_environment


def environment_proxy_for(
    scheme: str,
    host: str,
    port: int,
) -> str | None:
    """Return the concrete HTTP(S) proxy selected for one destination.

    Callers keep ``trust_env`` disabled and pass this value explicitly.  That
    makes the transport decision observable: a missing proxy or a NO_PROXY
    match can safely fall back to the already-verified, IP-pinned route.
    """

    proxies = getproxies_environment()
    host_for_bypass = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme.lower() == "https" else 80
    if port != default_port:
        host_for_bypass = f"{host_for_bypass}:{port}"
    if proxy_bypass_environment(host_for_bypass, proxies):
        return None

    candidate = proxies.get(scheme.lower()) or proxies.get("all")
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    return candidate


__all__ = ["environment_proxy_for"]
