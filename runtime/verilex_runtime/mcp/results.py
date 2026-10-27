"""Bound and mark untrusted MCP tool results before model injection."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from types import SimpleNamespace

from pydantic import BaseModel


_ROLE_PATTERN = "|".join(
    "\\s*".join(role) for role in ("system", "assistant", "tool", "developer")
)
_CONTROL_LABEL = re.compile(
    rf"<\s*(?P<closing>/?)\s*(?P<role>{_ROLE_PATTERN})\s*>",
    flags=re.IGNORECASE,
)
_BOUNDARY_START = "⟦UNTRUSTED_MCP_RESULT_START⟧\n"
_BOUNDARY_END = "\n⟦UNTRUSTED_MCP_RESULT_END⟧"
_MIN_ENVELOPE_BYTES = 256


@dataclass(frozen=True, slots=True)
class MCPResultEnvelope:
    code: str | None
    text: str
    byte_count: int | None
    sha256: str
    truncated: bool
    retained_byte_count: int | None
    omitted_bytes: int
    result_type: str
    declared_size: int | None = None
    untrusted: bool = True

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "code": self.code,
            "text": self.text,
            "byteCount": self.byte_count,
            "sha256": self.sha256,
            "truncated": self.truncated,
            "retainedByteCount": self.retained_byte_count,
            "omittedBytes": self.omitted_bytes,
            "resultType": self.result_type,
            "untrusted": self.untrusted,
        }
        if self.declared_size is not None:
            value["declaredSize"] = self.declared_size
        return value


def _encoded_envelope_size(envelope: MCPResultEnvelope) -> int:
    return len(
        json.dumps(
            envelope.as_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    )


def neutralize_mcp_control_labels(text: str) -> str:
    """Make prompt-role XML-like labels inert, including spaced variants."""

    def replace(match: re.Match[str]) -> str:
        role = re.sub(r"\s+", "", match.group("role")).casefold()
        direction = "close" if match.group("closing") else "open"
        return f"⟦untrusted-mcp-label:{role}-{direction}⟧"

    return _CONTROL_LABEL.sub(replace, text)


def _safe_type_name(value: object, fallback: str | None = None) -> str:
    try:
        candidate = (
            fallback
            if type(fallback) is str
            else object.__getattribute__(type(value), "__name__")
        )
        normalized = re.sub(
            r"[^a-zA-Z0-9_-]+", "_", candidate[:256]
        )[:64].strip("_")
    except Exception:
        return "unknown"
    return normalized.casefold() or "unknown"


def _binary_payload(value: object) -> tuple[bytes, str] | None:
    if type(value) in {bytes, bytearray, memoryview}:
        return bytes(value), "binary"

    block_type: object = None
    block_data: object = None
    if type(value) is dict:
        block_type = value.get("type")
        block_data = value.get("data", value.get("blob"))
        if block_type is None and "blob" in value:
            block_type = "blob"
    else:
        return None
    if type(block_type) is str and block_type.casefold() in {
        "image",
        "audio",
        "blob",
        "binary",
    }:
        if type(block_data) in {bytes, bytearray, memoryview}:
            return bytes(block_data), _safe_type_name(value, block_type)
        if type(block_data) is str:
            try:
                return (
                    base64.b64decode(block_data, validate=True),
                    _safe_type_name(value, block_type),
                )
            except (ValueError, binascii.Error):
                return block_data.encode("utf-8", "replace"), _safe_type_name(
                    value, block_type
                )
    return None


def _find_binary(value: object) -> tuple[bytes, str] | None:
    stack = [value]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        binary = _binary_payload(current)
        if binary is not None:
            return binary
        if type(current) is dict:
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend(current.values())
        elif type(current) in {list, tuple}:
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend(current)
    return None


def _metadata_only(
    *, code: str, result_type: str, payload: bytes | None, declared_size: int | None
) -> MCPResultEnvelope:
    digest_source = payload
    if digest_source is None:
        digest_source = json.dumps(
            {"code": code, "declaredSize": declared_size, "resultType": result_type},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    return MCPResultEnvelope(
        code=code,
        text="",
        byte_count=None if payload is None else len(payload),
        sha256=hashlib.sha256(digest_source).hexdigest(),
        truncated=False,
        retained_byte_count=0 if payload is not None else None,
        omitted_bytes=len(payload) if payload is not None else 0,
        result_type=result_type,
        declared_size=declared_size,
    )


def _utf8_head_tail(encoded: bytes, keep_bytes: int) -> tuple[str, str, int]:
    if keep_bytes >= len(encoded):
        return encoded.decode("utf-8", "strict"), "", 0
    head_budget = keep_bytes // 2
    tail_budget = keep_bytes - head_budget
    head = encoded[:head_budget]
    tail = encoded[len(encoded) - tail_budget :] if tail_budget else b""
    while head:
        try:
            head_text = head.decode("utf-8")
            break
        except UnicodeDecodeError:
            head = head[:-1]
    else:
        head_text = ""
    while tail:
        try:
            tail_text = tail.decode("utf-8")
            break
        except UnicodeDecodeError:
            tail = tail[1:]
    else:
        tail_text = ""
    kept = len(head) + len(tail)
    return head_text, tail_text, len(encoded) - kept


def _bounded_text_envelope(
    *, raw: bytes, text: str, result_type: str, max_bytes: int, code: str | None = None
) -> MCPResultEnvelope:
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) <= max_bytes:
        sanitized = neutralize_mcp_control_labels(text)
        complete = MCPResultEnvelope(
            code=code,
            text=f"{_BOUNDARY_START}{sanitized}{_BOUNDARY_END}",
            byte_count=len(raw),
            sha256=digest,
            truncated=False,
            retained_byte_count=len(raw),
            omitted_bytes=0,
            result_type=result_type,
        )
        if _encoded_envelope_size(complete) <= max_bytes:
            return complete

    low = 0
    high = min(len(raw), max_bytes)
    best: MCPResultEnvelope | None = None
    while low <= high:
        keep = (low + high) // 2
        head, tail, omitted = _utf8_head_tail(raw, keep)
        marker = f"\n…[{omitted} bytes omitted]…\n"
        candidate_text = (
            _BOUNDARY_START
            + neutralize_mcp_control_labels(head)
            + marker
            + neutralize_mcp_control_labels(tail)
            + _BOUNDARY_END
        )
        candidate = MCPResultEnvelope(
            code="mcp_output_too_large",
            text=candidate_text,
            byte_count=len(raw),
            sha256=digest,
            truncated=True,
            retained_byte_count=len(raw) - omitted,
            omitted_bytes=omitted,
            result_type=result_type,
        )
        if _encoded_envelope_size(candidate) <= max_bytes:
            best = candidate
            low = keep + 1
        else:
            high = keep - 1
    if best is None:
        raise ValueError("max_bytes is too small for an MCP result envelope")
    return best


def sanitize_mcp_result(result: object, max_bytes: int = 262_144) -> MCPResultEnvelope:
    """Return an untrusted, bounded envelope without creating artifacts."""
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes < _MIN_ENVELOPE_BYTES
    ):
        raise ValueError("max_bytes must be at least 256")

    serializable: object = result
    if isinstance(result, BaseModel):
        try:
            serializable = result.model_dump(mode="json")
        except Exception:
            serializable = result
    elif type(result) is SimpleNamespace:
        serializable = vars(result).copy()

    binary = _find_binary(serializable)
    if binary is not None:
        payload, result_type = binary
        envelope = _metadata_only(
            code="mcp_binary_output_omitted",
            result_type=result_type,
            payload=payload,
            declared_size=len(payload),
        )
        if _encoded_envelope_size(envelope) > max_bytes:
            raise ValueError("max_bytes is too small for an MCP result envelope")
        return envelope

    result_type = _safe_type_name(result, "text" if isinstance(result, str) else None)
    try:
        if type(serializable) is str:
            raw = serializable.encode("utf-8", "strict")
            text = serializable
        else:
            text = json.dumps(
                serializable,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
                check_circular=True,
            )
            raw = text.encode("utf-8", "strict")
    except Exception:
        declared_size = (
            len(result)
            if type(result) in {dict, list, tuple, str, bytes, bytearray}
            else None
        )
        envelope = _metadata_only(
            code="mcp_output_too_large",
            result_type=result_type,
            payload=None,
            declared_size=declared_size,
        )
        if _encoded_envelope_size(envelope) > max_bytes:
            raise ValueError("max_bytes is too small for an MCP result envelope")
        return envelope

    return _bounded_text_envelope(
        raw=raw,
        text=text,
        result_type=result_type,
        max_bytes=max_bytes,
        code="mcp_tool_error" if type(serializable) is dict and serializable.get("isError") is True else None,
    )
