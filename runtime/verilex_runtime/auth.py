"""Per-launch authentication for loopback API requests."""

from __future__ import annotations

import secrets


def launch_token_matches(provided: str | None, expected: str) -> bool:
    """Compare launch tokens without data-dependent string comparison timing."""

    return bool(provided) and secrets.compare_digest(provided, expected)
