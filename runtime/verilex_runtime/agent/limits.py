"""Resource limits for a single Agent Turn."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TurnLimits:
    # Maximum model steps (one model round-trip). Guards the tool loop against
    # a provider that keeps requesting tool calls without converging.
    max_steps: int = 30
    # Maximum number of tool calls across all steps in a single Turn.
    max_tool_calls: int = 50
    # Maximum wall-clock seconds for any single command invocation across
    # foreground and background command tools. Foreground ``run_command``
    # additionally keeps its own 120-second schema cap; this valve spans the
    # background command range (up to 120 minutes) and clamps oversized
    # duration arguments when a stricter engine is injected.
    max_command_seconds: int = 7200
    # Maximum wall-clock time in seconds for the whole Turn.
    wall_clock_seconds: float = 30 * 60


DEFAULT_TURN_LIMITS = TurnLimits()
