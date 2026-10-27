"""Atomic per-Turn limits for successfully extracted public web pages."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from verilex_runtime.tools.registry import ToolError


@dataclass(frozen=True, slots=True)
class _Usage:
    pages: int = 0
    characters: int = 0


class WebTurnBudget:
    def __init__(
        self,
        *,
        max_pages: int = 8,
        max_characters: int = 80_000,
    ) -> None:
        if (
            not isinstance(max_pages, int)
            or isinstance(max_pages, bool)
            or max_pages < 1
            or not isinstance(max_characters, int)
            or isinstance(max_characters, bool)
            or max_characters < 1
        ):
            raise ValueError("web budget limits must be positive integers")
        self._max_pages = max_pages
        self._max_characters = max_characters
        self._usage: dict[str, _Usage] = {}
        self._lock = Lock()

    def reserve(self, turn_id: str, character_count: int) -> None:
        turn_key = _require_turn_id(turn_id)
        if (
            not isinstance(character_count, int)
            or isinstance(character_count, bool)
            or character_count < 0
        ):
            raise _context_error()
        with self._lock:
            current = self._usage.get(turn_key, _Usage())
            if (
                current.pages >= self._max_pages
                or current.characters + character_count > self._max_characters
            ):
                raise _limit_error()
            self._usage[turn_key] = _Usage(
                pages=current.pages + 1,
                characters=current.characters + character_count,
            )

    def clear(self, turn_id: str) -> None:
        turn_key = _require_turn_id(turn_id)
        with self._lock:
            self._usage.pop(turn_key, None)

    def restore(self, turn_id: str, character_counts: object) -> None:
        """Atomically replace usage from strictly validated persisted reads."""
        turn_key = _require_turn_id(turn_id)
        if isinstance(character_counts, (str, bytes)):
            raise _context_error()
        try:
            counts = tuple(character_counts)
        except TypeError:
            raise _context_error() from None
        if any(
            type(count) is not int or count < 1 for count in counts
        ):
            raise _context_error()
        pages = len(counts)
        characters = sum(counts)
        if pages > self._max_pages or characters > self._max_characters:
            raise _limit_error()
        with self._lock:
            if pages:
                self._usage[turn_key] = _Usage(
                    pages=pages, characters=characters
                )
            else:
                self._usage.pop(turn_key, None)


def _require_turn_id(turn_id: object) -> str:
    if (
        not isinstance(turn_id, str)
        or not turn_id
        or turn_id != turn_id.strip()
    ):
        raise _context_error()
    return turn_id


def _context_error() -> ToolError:
    return ToolError(
        "web_tool_context_invalid",
        "The web tool execution context is invalid.",
    )


def _limit_error() -> ToolError:
    return ToolError(
        "web_turn_limit_reached",
        "This turn has reached its web reading limit.",
    )


__all__ = ["WebTurnBudget"]
