from __future__ import annotations

from verilex_runtime.context_usage.models import (
    ContextSegment,
    ContextUsageBreakdown,
    EstimatedContextUsage,
)

_PROTOCOL_TOKENS_PER_SEGMENT = 4
_TOKENS_PER_IMAGE = 256


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def _text_tokens(text: str) -> int:
    """Conservative, deterministic fallback used when Provider counts are absent."""
    tokens = 0
    ascii_run = 0
    punctuation_run = 0
    whitespace_run = 0

    def flush() -> None:
        nonlocal tokens, ascii_run, punctuation_run, whitespace_run
        tokens += _ceil_div(ascii_run, 4)
        tokens += _ceil_div(punctuation_run, 2)
        tokens += _ceil_div(whitespace_run, 8)
        ascii_run = punctuation_run = whitespace_run = 0

    for character in text:
        if ord(character) > 127:
            flush()
            tokens += 1
        elif character.isalnum() or character == "_":
            if punctuation_run or whitespace_run:
                flush()
            ascii_run += 1
        elif character.isspace():
            if ascii_run or punctuation_run:
                flush()
            whitespace_run += 1
        else:
            if ascii_run or whitespace_run:
                flush()
            # Control characters are structural overhead too; they must not
            # disappear from an estimate simply because they are not visible.
            punctuation_run += 1
    flush()
    return tokens


def _segment_tokens(segment: ContextSegment) -> int:
    return (
        _text_tokens(segment.text)
        + _PROTOCOL_TOKENS_PER_SEGMENT
        + segment.image_count * _TOKENS_PER_IMAGE
    )


def _capacity(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("context_window_tokens must be a positive integer or None")
    return value


def estimate_context_usage(
    segments: tuple[ContextSegment, ...] | list[ContextSegment],
    context_window_tokens: int | None,
) -> EstimatedContextUsage:
    raw_window = _capacity(context_window_tokens)
    counts = {
        "messages": 0,
        "system_and_tools": 0,
        "skills": 0,
        "attachments": 0,
        "other": 0,
    }
    for segment in segments:
        if not isinstance(segment, ContextSegment):
            raise ValueError("segments must contain ContextSegment values")
        counts[segment.category] += _segment_tokens(segment)

    breakdown = ContextUsageBreakdown(**counts)
    used_tokens = sum(counts.values())
    effective_window = (
        None if raw_window is None else max(1, raw_window * 90 // 100)
    )
    if effective_window is None:
        used_percent = None
        warning = "normal"
    else:
        used_percent = min(100.0, round(used_tokens * 100 / effective_window, 1))
        if used_tokens * 100 >= effective_window * 90:
            warning = "critical"
        elif used_tokens * 100 >= effective_window * 75:
            warning = "near_limit"
        else:
            warning = "normal"

    return EstimatedContextUsage(
        used_tokens=used_tokens,
        context_window_tokens=raw_window,
        effective_window_tokens=effective_window,
        used_percent=used_percent,
        estimated=True,
        warning=warning,
        breakdown=breakdown,
    )
