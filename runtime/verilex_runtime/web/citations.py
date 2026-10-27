"""Verify model-authored web citations against persisted local tool facts.

This module never performs network I/O. A source exists only when a successful
``web_read`` ToolCall in the same thread contains a result that still passes
the strict Task 6 stored-result validator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from verilex_runtime.storage.models import ToolCallRecord, ToolCallStatus
from verilex_runtime.storage.tool_repository import ToolCallRepository
from verilex_runtime.web.tools import validated_stored_web_read_character_count


_CITATION_PREFIX = "[[cite:"
_OFFER_MARKER = "[[offer_web_access]]"
_MAX_SOURCE_ID_CHARS = 128
_MAX_UNIQUE_MARKERS = 256
_MAX_STORED_RESULT_CHARS = 128 * 1024


class WebOfferStreamFilter:
    """Hold only a possible internal marker suffix before publishing deltas."""

    def __init__(self) -> None:
        self._pending = ""

    def feed(self, delta: str) -> str:
        text = (self._pending + delta).replace(_OFFER_MARKER, "")
        self._pending = ""
        for count in range(min(len(text), len(_OFFER_MARKER) - 1), 0, -1):
            if _OFFER_MARKER.startswith(text[-count:]):
                self._pending = text[-count:]
                return text[:-count]
        return text

    def finish(self) -> str:
        # Preserve ordinary Markdown brackets, but not truncated control words.
        result = self._pending if len(self._pending) <= 2 else ""
        self._pending = ""
        return result


def strip_web_offer_marker(text: str) -> str:
    stream_filter = WebOfferStreamFilter()
    return stream_filter.feed(text) + stream_filter.finish()


@dataclass(frozen=True, slots=True)
class CitationSource:
    number: int
    source_id: str
    title: str
    url: str
    site: str
    fetched_at: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class CitationResolution:
    text: str
    sources: tuple[CitationSource, ...]
    warning: bool
    needs_repair: bool
    web_access_offer: bool


@dataclass(frozen=True, slots=True)
class _VerifiedRead:
    source_id: str
    title: str
    url: str
    site: str
    fetched_at: str
    truncated: bool
    content_sha256: str


@dataclass(frozen=True, slots=True)
class _CitationMarker:
    start: int
    end: int
    source_id: str
    syntactically_valid: bool


class CitationResolver:
    def __init__(self, tool_calls: ToolCallRepository) -> None:
        self._tool_calls = tool_calls

    def resolve(
        self,
        text: str,
        *,
        thread_id: str,
        web_access_enabled: bool = False,
        final_attempt: bool = False,
    ) -> CitationResolution:
        if not isinstance(text, str):
            raise TypeError("citation text must be a string")

        offer_requested = _OFFER_MARKER in text
        text = strip_web_offer_marker(text)
        web_access_offer = offer_requested and not web_access_enabled
        markers = _scan_citation_markers(text)

        unique_ids: list[str] = []
        seen_ids: set[str] = set()
        for marker in markers:
            source_id = marker.source_id
            if not marker.syntactically_valid or source_id in seen_ids:
                continue
            seen_ids.add(source_id)
            if len(unique_ids) < _MAX_UNIQUE_MARKERS:
                unique_ids.append(source_id)

        verified = {
            item.source_id: item
            for record in self._tool_calls.find_successful_web_reads(
                thread_id, unique_ids
            )
            if (item := _verify_record(record)) is not None
        }

        sources: list[CitationSource] = []
        source_by_content: dict[tuple[str, str], CitationSource] = {}
        marker_replacements: list[str] = []
        invalid = False
        for marker in markers:
            source_id = marker.source_id
            if not marker.syntactically_valid:
                invalid = True
                marker_replacements.append(
                    "〔引用未验证〕"
                    if final_attempt
                    else text[marker.start : marker.end]
                )
                continue
            item = verified.get(source_id)
            if item is None:
                invalid = True
                marker_replacements.append(
                    "〔引用未验证〕"
                    if final_attempt
                    else text[marker.start : marker.end]
                )
                continue
            key = (item.url, item.content_sha256)
            source = source_by_content.get(key)
            if source is None:
                source = CitationSource(
                    number=len(sources) + 1,
                    source_id=item.source_id,
                    title=item.title,
                    url=item.url,
                    site=item.site,
                    fetched_at=item.fetched_at,
                    truncated=item.truncated,
                )
                sources.append(source)
                source_by_content[key] = source
            marker_replacements.append(f"[[cite:{source.source_id}]]")

        if markers:
            text = _replace_markers(text, markers, marker_replacements)

        needs_repair = invalid
        return CitationResolution(
            text=text,
            sources=tuple(sources),
            warning=final_attempt and needs_repair,
            needs_repair=needs_repair,
            web_access_offer=web_access_offer,
        )


def sources_block(sources: tuple[CitationSource, ...]) -> dict:
    return {
        "type": "sources",
        "sources": [
            {
                "number": source.number,
                "sourceId": source.source_id,
                "title": source.title,
                "url": source.url,
                "site": source.site,
                "fetchedAt": source.fetched_at,
                "truncated": source.truncated,
            }
            for source in sources
        ],
    }


def web_access_offer_block() -> dict:
    return {"type": "web_access_offer"}


def citation_warning_block() -> dict:
    return {"type": "citation_warning"}


def _valid_source_id(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= _MAX_SOURCE_ID_CHARS
        and value == value.strip()
        and "[" not in value
        and "]" not in value
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _verify_record(record: ToolCallRecord) -> _VerifiedRead | None:
    if record.name != "web_read" or record.status is not ToolCallStatus.SUCCEEDED:
        return None
    stored_result = record.result_json
    if (
        not isinstance(stored_result, str)
        or not stored_result
        or len(stored_result) > _MAX_STORED_RESULT_CHARS
    ):
        return None
    try:
        stored = json.loads(stored_result)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return None
    if (
        type(stored) is not dict
        or set(stored) != {"ok", "output"}
        or stored["ok"] is not True
    ):
        return None
    output = stored["output"]
    try:
        validated_stored_web_read_character_count(output, source_id=record.id)
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return None
    return _VerifiedRead(
        source_id=record.id,
        title=payload["title"],
        url=payload["url"],
        site=payload["site"],
        fetched_at=payload["fetched_at"],
        truncated=payload["truncated"],
        content_sha256=payload["content_sha256"],
    )


def _scan_citation_markers(text: str) -> list[_CitationMarker]:
    markers: list[_CitationMarker] = []
    offset = 0
    while True:
        start = text.find(_CITATION_PREFIX, offset)
        if start < 0:
            break
        content_start = start + len(_CITATION_PREFIX)
        close = text.find("]]", content_start)
        if close < 0:
            markers.append(
                _CitationMarker(
                    start=start,
                    end=len(text),
                    source_id=text[content_start:],
                    syntactically_valid=False,
                )
            )
            break
        source_id = text[content_start:close]
        markers.append(
            _CitationMarker(
                start=start,
                end=close + 2,
                source_id=source_id,
                syntactically_valid=_valid_source_id(source_id),
            )
        )
        offset = close + 2
    return markers


def _replace_markers(
    text: str, markers: list[_CitationMarker], replacements: list[str]
) -> str:
    parts: list[str] = []
    offset = 0
    for marker, replacement in zip(markers, replacements, strict=True):
        parts.append(text[offset : marker.start])
        parts.append(replacement)
        offset = marker.end
    parts.append(text[offset:])
    return "".join(parts)
