"""Unified event protocol for the streaming agent engine.

Events are the single source of truth for what a Turn produced. Every event is
persisted to the ``events`` table before it is broadcast over WebSocket, so a
disconnected client can always replay from its last known sequence.

This module owns the event type vocabulary, the payload schemas, and the
size/redaction guards that keep event payloads bounded and free of secrets.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from verilex_runtime.attachments.errors import attachment_error
from verilex_runtime.redaction import redact_sensitive_text
from verilex_runtime.storage.models import EventRecord


class EventType(str, Enum):
    TURN_RETRY = "turn_retry"
    TURN_COMPACTED = "turn_compacted"
    TURN_STARTED = "turn_started"
    MESSAGE_DELTA = "message_delta"
    MESSAGE_RESET = "message_reset"
    MESSAGE_COMPLETED = "message_completed"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"
    TURN_CANCELLED = "turn_cancelled"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    ATTACHMENT_DEGRADED = "attachment_degraded"
    REASONING_EFFORT_DEGRADED = "reasoning_effort_degraded"


class DeltaKind(str, Enum):
    TEXT = "text"
    REASONING = "reasoning"


# Upper bound for a single serialized event payload. Guard against unbounded
# provider output or tool output entering SQLite, memory, or the model context.
MAX_EVENT_PAYLOAD_CHARS = 64 * 1024

# Upper bound for the accumulated final assistant message in a single Turn.
MAX_ASSISTANT_MESSAGE_CHARS = 256 * 1024


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    thread_id: str
    turn_id: str
    sequence: int
    type: str
    payload: dict
    timestamp: str


def validate_payload(payload: dict) -> dict:
    """Validate an event payload before persistence.

    Payloads are small structured dictionaries built by the engine. We reject
    unknown shapes early rather than silently persisting malformed data.
    """
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > MAX_EVENT_PAYLOAD_CHARS:
        raise ValueError("event payload exceeds size limit")
    return payload


def to_envelope(record: EventRecord) -> EventEnvelope:
    """Convert a persisted event row into the wire envelope sent to clients."""
    payload = json.loads(record.payload_json)
    return EventEnvelope(
        thread_id=record.thread_id,
        turn_id=record.turn_id,
        sequence=record.sequence,
        type=record.type,
        payload=payload,
        timestamp=record.created_at,
    )


def wire_event(record: EventRecord) -> dict:
    """Render a persisted event row as the JSON object broadcast to clients."""
    return {
        "threadId": record.thread_id,
        "turnId": record.turn_id,
        "sequence": record.sequence,
        "type": record.type,
        "timestamp": record.created_at,
        "payload": json.loads(record.payload_json),
    }


def wire_view(view) -> dict:
    """Render a stored EventView (already-decoded payload) for replay."""
    return {
        "threadId": view.thread_id,
        "turnId": view.turn_id,
        "sequence": view.sequence,
        "type": view.type,
        "timestamp": view.created_at,
        "payload": view.payload,
    }


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def reasoning_block(text: str) -> dict:
    return {"type": "reasoning", "text": text}


def tool_call_block(tool_call_id: str, name: str, arguments: dict) -> dict:
    """A content block recording a model-proposed tool call on an assistant message."""
    return {
        "type": "tool_call",
        "id": tool_call_id,
        "name": name,
        "arguments": arguments,
    }


def tool_result_block(tool_call_id: str, name: str, content: str) -> dict:
    """A content block recording a tool result on a ``tool`` role message."""
    return {
        "type": "tool_result",
        "toolCallId": tool_call_id,
        "name": name,
        "content": content,
    }


# Upper bound for a single string field inside a tool-arguments summary. Long
# values (e.g. file content passed to ``write_file``) are truncated so event
# payloads never leak full document text.
MAX_ARGUMENT_FIELD_CHARS = 200
MAX_MCP_ARGUMENT_FIELDS = 64
MAX_MCP_ARGUMENT_NAME_CHARS = 128


def arguments_summary(arguments: dict) -> dict:
    """Return a bounded, redacted summary of tool arguments for event payloads."""
    summary: dict = {}
    for key, value in arguments.items():
        if not isinstance(value, str):
            summary[key] = value
            continue
        redacted = redact_sensitive_text(value)
        summary[key] = (
            redacted[:MAX_ARGUMENT_FIELD_CHARS] + "…"
            if len(redacted) > MAX_ARGUMENT_FIELD_CHARS
            else redacted
        )
    return summary


def _json_type_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def mcp_arguments_summary(arguments: dict) -> dict:
    """Summarize MCP arguments without persisting any argument value.

    Property names are useful for the desktop tool card, but values may be
    legal material or credentials. Keep a deterministic, bounded name/type
    list and a single truncation bit only.
    """
    fields = []
    truncated = len(arguments) > MAX_MCP_ARGUMENT_FIELDS
    for key in sorted(arguments)[:MAX_MCP_ARGUMENT_FIELDS]:
        name = redact_sensitive_text(key)
        if len(name) > MAX_MCP_ARGUMENT_NAME_CHARS:
            name = name[:MAX_MCP_ARGUMENT_NAME_CHARS]
            truncated = True
        fields.append({"name": name, "type": _json_type_name(arguments[key])})
    return {"fields": fields, "truncated": truncated}


def document_arguments_summary(arguments: dict) -> dict:
    """Summarize document_create arguments without any user content.

    The draft body, title, and output name can all be
    confidential legal material: only hashes and character counts enter
    events, approvals, and the audit trail.
    """
    content = arguments.get("content_markdown", "")
    output_name = arguments.get("output_name", "")
    title = arguments.get("title", "")
    return {
        "output_name_sha256": hashlib.sha256(
            output_name.encode("utf-8")
        ).hexdigest(),
        "title_char_count": len(title),
        "content_char_count": len(content),
    }


# Upper bound for the tool-result preview carried on completion events. The
# full result stays in the persisted tool message; the preview only feeds the
# desktop timeline card, so it is bounded independently of the 64 KiB event
# payload ceiling. Truncation is character-based and never tears a glyph.
MAX_RESULT_PREVIEW_CHARS = 400


def result_preview(content: str) -> tuple[str, bool]:
    """Return ``(preview, truncated)`` for a tool-result event payload field."""
    if len(content) <= MAX_RESULT_PREVIEW_CHARS:
        return content, False
    return content[:MAX_RESULT_PREVIEW_CHARS], True


def _safe_web_summary_url(
    value: object,
    *,
    preserve_location: bool = False,
) -> tuple[str, str] | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    url = (
        parsed.geturl()
        if preserve_location
        else parsed._replace(query="", fragment="").geturl()
    )
    return url, parsed.hostname


def _bounded_plain_text(value: object, maximum: int) -> str | None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return None
    return value


def web_tool_result_summary(name: str, content: str) -> dict | None:
    """Build a bounded desktop summary without query text, body, or source IDs."""
    if name not in {"web_search", "web_read"} or len(content) > 128 * 1024:
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if name == "web_search":
        values = payload.get("results")
        result_count = payload.get("result_count")
        if (
            not isinstance(values, list)
            or len(values) > 10
            or type(result_count) is not int
            or result_count != len(values)
        ):
            return None
        results = []
        for value in values:
            if not isinstance(value, dict):
                return None
            title = _bounded_plain_text(value.get("title"), 512)
            snippet_value = value.get("snippet")
            snippet = (
                ""
                if snippet_value == ""
                else _bounded_plain_text(snippet_value, 2_000)
            )
            target = _safe_web_summary_url(
                value.get("url"),
                preserve_location=True,
            )
            if title is None or snippet is None or target is None:
                return None
            url, site = target
            results.append(
                {"title": title, "url": url, "site": site, "snippet": snippet}
            )
        return {
            "kind": "web_search",
            "resultCount": result_count,
            "results": results,
        }

    title = _bounded_plain_text(payload.get("title"), 512)
    target = _safe_web_summary_url(payload.get("url"))
    site = payload.get("site")
    character_count = payload.get("character_count")
    truncated = payload.get("truncated")
    if (
        title is None
        or target is None
        or not isinstance(site, str)
        or site != target[1]
        or type(character_count) is not int
        or not 1 <= character_count <= 20_000
        or type(truncated) is not bool
    ):
        return None
    return {
        "kind": "web_read",
        "title": title,
        "site": site,
        "characterCount": character_count,
        "truncated": truncated,
    }


def law_tool_result_summary(name: str, content: str) -> dict | None:
    """Build a bounded desktop summary for law tool results (statute cards).

    Mirrors the web summary discipline: bounded plain text, no control
    characters, strict type checks, and no full body text — the desktop card
    shows the located clause window only.
    """
    if name not in {"law_search", "law_read"} or len(content) > 128 * 1024:
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if name == "law_search":
        rows_value = payload.get("rows")
        total = payload.get("total")
        page = payload.get("page")
        if (
            not isinstance(rows_value, list)
            or len(rows_value) > 10
            or type(total) is not int
            or type(page) is not int
        ):
            return None
        rows = []
        for value in rows_value:
            if not isinstance(value, dict):
                return None
            row = _law_row_summary(value)
            if row is None:
                return None
            rows.append(row)
        return {"kind": "law_search", "rows": rows, "total": total, "page": page}

    title = _bounded_plain_text(payload.get("title"), 512)
    bbbs = _bounded_plain_text(payload.get("bbbs"), 64)
    effectivity = _bounded_plain_text(payload.get("effectivity"), 32)
    fetched_at = _bounded_plain_text(payload.get("fetched_at"), 64)
    source = _bounded_plain_text(payload.get("source"), 64)
    gbrq = payload.get("gbrq")
    sxrq = payload.get("sxrq")
    cached = payload.get("cached")
    truncated = payload.get("truncated")
    if (
        title is None
        or bbbs is None
        or effectivity is None
        or fetched_at is None
        or source is None
        or type(cached) is not bool
        or type(truncated) is not bool
    ):
        return None
    dates: dict[str, str] = {}
    for key, value in (("gbrq", gbrq), ("sxrq", sxrq)):
        if not isinstance(value, str) or len(value) > 32:
            return None
        dates[key] = value
    summary: dict = {
        "kind": "law_read",
        "bbbs": bbbs,
        "title": title,
        "effectivity": effectivity,
        "gbrq": dates["gbrq"],
        "sxrq": dates["sxrq"],
        "fetchedAt": fetched_at,
        "cached": cached,
        "truncated": truncated,
        "source": source,
    }
    clause = payload.get("clause")
    if clause is not None:
        bounded_clause = _bounded_plain_text(clause, 32)
        heading = payload.get("heading", "")
        if (
            bounded_clause is None
            or not isinstance(heading, str)
            or len(heading) > 128
        ):
            return None
        window = _law_clause_window(payload)
        if window is None:
            return None
        summary["clause"] = bounded_clause
        summary["heading"] = heading
        summary.update(window)
    return summary


def _law_row_summary(value: dict) -> dict | None:
    title = _bounded_plain_text(value.get("title"), 512)
    bbbs = _bounded_plain_text(value.get("bbbs"), 64)
    flxz = value.get("flxz")
    zdjg = value.get("zdjg")
    effectivity = _bounded_plain_text(value.get("effectivity"), 32)
    gbrq = value.get("gbrq")
    sxrq = value.get("sxrq")
    if (
        title is None
        or bbbs is None
        or effectivity is None
        or not isinstance(flxz, str)
        or len(flxz) > 64
        or not isinstance(zdjg, str)
        or len(zdjg) > 128
        or not isinstance(gbrq, str)
        or len(gbrq) > 32
        or not isinstance(sxrq, str)
        or len(sxrq) > 32
    ):
        return None
    return {
        "bbbs": bbbs,
        "title": title,
        "flxz": flxz,
        "zdjg": zdjg,
        "effectivity": effectivity,
        "gbrq": gbrq,
        "sxrq": sxrq,
    }


def _law_clause_window(payload: dict) -> dict | None:
    window: dict[str, list[str]] = {}
    for key, source in (
        ("clauseText", "clause_text"),
        ("before", "before"),
        ("after", "after"),
    ):
        values = payload.get(source)
        if not isinstance(values, list) or len(values) > (200 if key == "clauseText" else 3):
            return None
        bounded: list[str] = []
        for value in values:
            text = _bounded_plain_text(value, 12_000 if key == "clauseText" else 2_000)
            if text is None:
                return None
            bounded.append(text)
        if key == "clauseText" and sum(map(len, bounded)) > 12_000:
            return None
        window[key] = bounded
    return window


def normalize_user_content(content: str) -> list[dict]:
    """Normalize a plain user message into the structured content-block array."""
    return [text_block(content)]


def attachment_user_content(text: str, attachment_ids: Sequence[str]) -> dict:
    """Build the versioned persisted shape for a user message with attachments."""
    if not isinstance(text, str) or isinstance(attachment_ids, (str, bytes)):
        raise attachment_error("attachment_blob_missing")
    ids = tuple(attachment_ids)
    if (
        not ids
        or any(not isinstance(value, str) or not value.strip() for value in ids)
        or len(set(ids)) != len(ids)
    ):
        raise attachment_error("attachment_blob_missing")
    return {"version": 1, "text": text, "attachmentIds": list(ids)}


def litigation_material_paths(content: object) -> list[str] | None:
    value = content.get('litigationMaterials') if isinstance(content, dict) else next(
        (block.get('paths') for block in content if isinstance(block, dict) and block.get('type') == 'litigation_materials'), None
    ) if isinstance(content, list) else None
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 1000 or any(not isinstance(path, str) or not path or len(path) > 1000 for path in value):
        raise attachment_error('attachment_blob_missing')
    return list(value)


def with_litigation_materials(content: list | dict, paths: list[str] | None):
    if paths is None:
        return content
    if isinstance(content, dict):
        return {**content, 'litigationMaterials': list(paths)}
    return [*content, {'type': 'litigation_materials', 'paths': list(paths)}]


def user_message_parts(content: object) -> tuple[str, tuple[str, ...]]:
    """Parse old user content or the exact version-one attachment shape."""
    if isinstance(content, str):
        return content, ()
    if isinstance(content, list):
        return extract_text(content), ()
    if isinstance(content, dict) and "litigationMaterials" in content:
        litigation_material_paths(content)
        content = {key: value for key, value in content.items() if key != "litigationMaterials"}
    if isinstance(content, dict) and set(content) == {
        "version",
        "text",
        "attachmentIds",
    }:
        version = content["version"]
        text = content["text"]
        values = content["attachmentIds"]
        if (
            type(version) is int
            and version == 1
            and isinstance(text, str)
            and isinstance(values, list)
            and bool(values)
            and all(
                isinstance(value, str) and bool(value.strip()) for value in values
            )
            and len(set(values)) == len(values)
        ):
            return text, tuple(values)
    raise attachment_error("attachment_blob_missing")


def extract_text(content: object) -> str:
    """Flatten a structured content-block array into plain text for the model.

    Reasoning blocks are skipped: they are the model's private chain of thought
    and must not be replayed back into a subsequent model context.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""
