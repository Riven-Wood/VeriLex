from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ContextCategory = Literal[
    "messages",
    "system_and_tools",
    "skills",
    "attachments",
    "other",
]
ContextWarning = Literal["normal", "near_limit", "critical"]

_CATEGORIES = {
    "messages",
    "system_and_tools",
    "skills",
    "attachments",
    "other",
}


@dataclass(frozen=True, slots=True)
class ContextSegment:
    category: ContextCategory
    text: str
    image_count: int = 0

    def __post_init__(self) -> None:
        if self.category not in _CATEGORIES:
            raise ValueError("unsupported context category")
        if not isinstance(self.text, str):
            raise ValueError("context segment text must be a string")
        if (
            isinstance(self.image_count, bool)
            or not isinstance(self.image_count, int)
            or self.image_count < 0
        ):
            raise ValueError("image_count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ContextUsageBreakdown:
    messages: int = 0
    system_and_tools: int = 0
    skills: int = 0
    attachments: int = 0
    other: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "messages": self.messages,
            "system_and_tools": self.system_and_tools,
            "skills": self.skills,
            "attachments": self.attachments,
            "other": self.other,
        }


@dataclass(frozen=True, slots=True)
class EstimatedContextUsage:
    used_tokens: int
    context_window_tokens: int | None
    effective_window_tokens: int | None
    used_percent: float | None
    estimated: bool
    warning: ContextWarning
    breakdown: ContextUsageBreakdown


@dataclass(frozen=True, slots=True)
class ContextUsageView:
    used_tokens: int
    context_window_tokens: int | None
    context_window_source: str
    effective_window_tokens: int | None
    used_percent: float | None
    estimated: bool
    warning: ContextWarning
    breakdown: ContextUsageBreakdown
    updated_at: str
