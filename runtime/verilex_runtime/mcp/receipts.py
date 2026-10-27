from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.models import MCPDiscoveredTool
from verilex_runtime.mcp.schema import validate_mcp_input_schema


_DIGEST_BYTES = 32
_MAX_RECEIPTS = 4096
_MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
_MAX_TTL_SECONDS = 600
_MAX_TOOLS = 1000
_MAX_RISKS = 64
_MAX_TOOL_SCHEMA_BYTES = 2 * 1024 * 1024
_HEX_DIGITS = frozenset("0123456789abcdef")


def _invalid(*, expired: bool = False) -> MCPDomainError:
    return MCPDomainError(
        "mcp_test_receipt_expired" if expired else "mcp_test_receipt_invalid",
        retryable=False,
    )


def _frame(label: bytes, parts: Sequence[bytes]) -> bytes:
    output = bytearray()
    output.extend(len(label).to_bytes(4, "big"))
    output.extend(label)
    output.extend(len(parts).to_bytes(4, "big"))
    for part in parts:
        output.extend(len(part).to_bytes(8, "big"))
        output.extend(part)
    return bytes(output)


def _text_bytes(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        raise _invalid()
    try:
        return value.encode("utf-8", "strict")
    except UnicodeError:
        raise _invalid() from None


def _hash_description(value: object) -> str:
    if not isinstance(value, str):
        raise _invalid()
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        raise _invalid() from None
    return hashlib.sha256(encoded).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value)
    )


@dataclass(frozen=True, slots=True)
class ToolDecision:
    tool_name: str
    enabled: bool
    expected_description_hash: str
    expected_schema_hash: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tool_name, str)
            or not self.tool_name
            or type(self.enabled) is not bool
            or not _valid_sha256(self.expected_description_hash)
            or not _valid_sha256(self.expected_schema_hash)
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class _ToolSnapshot:
    tool_name: str
    description_hash: str
    schema_hash: str

    def binding_bytes(self) -> bytes:
        return _frame(
            b"mcp-receipt-tool-v1",
            (
                _text_bytes(self.tool_name),
                self.description_hash.encode("ascii"),
                self.schema_hash.encode("ascii"),
            ),
        )


@dataclass(frozen=True, slots=True)
class _StoredTool:
    tool_name: str
    description: str
    canonical_schema: bytes

    def materialize(self) -> MCPDiscoveredTool:
        schema = json.loads(self.canonical_schema)
        if not isinstance(schema, dict):
            raise _invalid()
        return MCPDiscoveredTool(
            tool_name=self.tool_name,
            description=self.description,
            input_schema=schema,
        )


@dataclass(slots=True)
class _ReceiptRecord:
    binding_digest: bytes = field(repr=False)
    tools: tuple[_StoredTool, ...]
    risk_codes: tuple[str, ...]
    expires_at: float
    snapshot_bytes: int
    occupied_generation: int | None = None
    next_generation: int = 1


@dataclass(slots=True)
class VerifiedTest:
    tools: tuple[MCPDiscoveredTool, ...]
    risk_codes: tuple[str, ...]
    decisions: tuple[ToolDecision, ...]
    _store: "TestReceiptStore" = field(repr=False)
    _receipt: str = field(repr=False)
    _generation: int = field(repr=False)
    _binding_digest: bytes = field(repr=False)
    _finalized: Literal["committed", "rolled_back"] | None = field(
        default=None, repr=False
    )

    def commit_consumption(self) -> None:
        if self._finalized is not None:
            return
        self._store._commit(
            self._receipt,
            self._generation,
            self._binding_digest,
        )
        self._finalized = "committed"

    def rollback_consumption(self) -> None:
        if self._finalized is not None:
            return
        self._store._rollback(
            self._receipt,
            self._generation,
            self._binding_digest,
        )
        self._finalized = "rolled_back"


class TestReceiptStore:
    """Process-local one-time proof that a precise MCP snapshot was probed."""

    __test__ = False

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        random_bytes: Callable[[int], bytes] | None = None,
        hmac_key: bytes | None = None,
        max_receipts: int = _MAX_RECEIPTS,
        max_snapshot_bytes: int = _MAX_SNAPSHOT_BYTES,
    ) -> None:
        key = secrets.token_bytes(_DIGEST_BYTES) if hmac_key is None else hmac_key
        if not isinstance(key, bytes) or len(key) < _DIGEST_BYTES:
            raise ValueError("test receipt HMAC key must contain at least 32 bytes")
        if isinstance(max_receipts, bool) or not isinstance(max_receipts, int) or max_receipts <= 0:
            raise ValueError("test receipt capacity must be a positive integer")
        if (
            isinstance(max_snapshot_bytes, bool)
            or not isinstance(max_snapshot_bytes, int)
            or max_snapshot_bytes <= 0
        ):
            raise ValueError("test receipt byte budget must be a positive integer")
        self._clock = clock or time.monotonic
        self._random_bytes = random_bytes or secrets.token_bytes
        self._hmac_key = key
        self._max_receipts = max_receipts
        self._max_snapshot_bytes = max_snapshot_bytes
        self._snapshot_bytes = 0
        self._records: dict[str, _ReceiptRecord] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        canonical_config: bytes,
        secret_values: Mapping[str, Mapping[str, str]],
        tools: Sequence[MCPDiscoveredTool],
        risks: Sequence[str],
        *,
        ttl_seconds: int = _MAX_TTL_SECONDS,
    ) -> str:
        try:
            return self._issue(
                canonical_config,
                secret_values,
                tools,
                risks,
                ttl_seconds=ttl_seconds,
            )
        except MCPDomainError:
            raise
        except Exception:
            raise _invalid() from None

    def _issue(
        self,
        canonical_config: bytes,
        secret_values: Mapping[str, Mapping[str, str]],
        tools: Sequence[MCPDiscoveredTool],
        risks: Sequence[str],
        *,
        ttl_seconds: int,
    ) -> str:
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or ttl_seconds <= 0
            or ttl_seconds > _MAX_TTL_SECONDS
        ):
            raise _invalid()
        now = self._now()
        snapshots, normalized_tools = self._tool_snapshots(tools)
        risk_codes = self._risk_codes(risks)
        digest = self._binding_digest(
            canonical_config,
            secret_values,
            snapshots,
            risk_codes,
        )
        snapshot_bytes = (
            len(canonical_config)
            + sum(
                len(tool.tool_name.encode("utf-8", "strict"))
                + len(tool.description.encode("utf-8", "strict"))
                + len(tool.canonical_schema)
                for tool in normalized_tools
            )
            + sum(len(code.encode("utf-8", "strict")) for code in risk_codes)
            + len(digest)
        )
        with self._lock:
            self._purge_expired_locked(now)
            if (
                len(self._records) >= self._max_receipts
                or snapshot_bytes > self._max_snapshot_bytes - self._snapshot_bytes
            ):
                raise _invalid()
            for _attempt in range(8):
                raw = self._random_bytes(_DIGEST_BYTES)
                if not isinstance(raw, bytes) or len(raw) != _DIGEST_BYTES:
                    raise _invalid()
                receipt = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
                if receipt not in self._records:
                    break
            else:
                raise _invalid()
            self._records[receipt] = _ReceiptRecord(
                binding_digest=digest,
                tools=normalized_tools,
                risk_codes=risk_codes,
                expires_at=now + ttl_seconds,
                snapshot_bytes=snapshot_bytes,
            )
            self._snapshot_bytes += snapshot_bytes
        return receipt

    def consume(
        self,
        receipt: str,
        canonical_config: bytes,
        secret_values: Mapping[str, Mapping[str, str]],
        decisions: Sequence[ToolDecision],
    ) -> VerifiedTest:
        try:
            return self._consume(
                receipt,
                canonical_config,
                secret_values,
                decisions,
            )
        except MCPDomainError:
            raise
        except Exception:
            raise _invalid() from None

    def _consume(
        self,
        receipt: str,
        canonical_config: bytes,
        secret_values: Mapping[str, Mapping[str, str]],
        decisions: Sequence[ToolDecision],
    ) -> VerifiedTest:
        if not isinstance(receipt, str) or not receipt:
            raise _invalid()
        normalized_decisions = self._decisions(decisions)
        snapshots = tuple(
            _ToolSnapshot(
                decision.tool_name,
                decision.expected_description_hash,
                decision.expected_schema_hash,
            )
            for decision in normalized_decisions
        )
        now = self._now()
        with self._lock:
            record = self._records.get(receipt)
            if record is None or record.occupied_generation is not None:
                raise _invalid()
            if now >= record.expires_at:
                self._delete_record_locked(receipt)
                raise _invalid(expired=True)
            candidate = self._binding_digest(
                canonical_config,
                secret_values,
                snapshots,
                record.risk_codes,
            )
            if not hmac.compare_digest(record.binding_digest, candidate):
                raise _invalid()
            generation = record.next_generation
            record.next_generation += 1
            record.occupied_generation = generation
            return VerifiedTest(
                tools=tuple(tool.materialize() for tool in record.tools),
                risk_codes=record.risk_codes,
                decisions=normalized_decisions,
                _store=self,
                _receipt=receipt,
                _generation=generation,
                _binding_digest=record.binding_digest,
            )

    def is_available(self, receipt: str) -> bool:
        if not isinstance(receipt, str) or not receipt:
            return False
        now = self._now()
        with self._lock:
            record = self._records.get(receipt)
            if record is None:
                return False
            if now >= record.expires_at:
                self._delete_record_locked(receipt)
                return False
            return record.occupied_generation is None

    def _commit(self, receipt: str, generation: int, digest: bytes) -> None:
        with self._lock:
            record = self._matching_occupied_locked(receipt, generation, digest)
            if record is not None:
                self._delete_record_locked(receipt)

    def _rollback(self, receipt: str, generation: int, digest: bytes) -> None:
        now = self._now()
        with self._lock:
            record = self._matching_occupied_locked(receipt, generation, digest)
            if record is None:
                return
            if now >= record.expires_at:
                self._delete_record_locked(receipt)
                return
            record.occupied_generation = None

    def _matching_occupied_locked(
        self,
        receipt: str,
        generation: int,
        digest: bytes,
    ) -> _ReceiptRecord | None:
        record = self._records.get(receipt)
        if (
            record is None
            or record.occupied_generation != generation
            or not hmac.compare_digest(record.binding_digest, digest)
        ):
            return None
        return record

    def _binding_digest(
        self,
        canonical_config: bytes,
        secret_values: Mapping[str, Mapping[str, str]],
        tools: Sequence[_ToolSnapshot],
        risk_codes: Sequence[str],
    ) -> bytes:
        if not isinstance(canonical_config, bytes):
            raise _invalid()
        secrets_frame = self._secret_frame(secret_values)
        tools_frame = _frame(
            b"mcp-receipt-tools-v1",
            tuple(tool.binding_bytes() for tool in sorted(tools, key=lambda item: item.tool_name)),
        )
        risks_frame = _frame(
            b"mcp-receipt-risks-v1",
            tuple(_text_bytes(code) for code in risk_codes),
        )
        material = _frame(
            b"verilex-mcp-test-receipt-v1",
            (canonical_config, secrets_frame, tools_frame, risks_frame),
        )
        return hmac.new(self._hmac_key, material, hashlib.sha256).digest()

    @staticmethod
    def _secret_frame(
        values: Mapping[str, Mapping[str, str]],
    ) -> bytes:
        if not isinstance(values, Mapping) or set(values) != {"env", "headers"}:
            raise _invalid()
        namespace_frames: list[bytes] = []
        for namespace in ("env", "headers"):
            items = values[namespace]
            if not isinstance(items, Mapping):
                raise _invalid()
            normalized: list[tuple[str, bytes]] = []
            seen: set[str] = set()
            for name, value in items.items():
                name_bytes = _text_bytes(name)
                if not isinstance(value, str):
                    raise _invalid()
                try:
                    value_bytes = value.encode("utf-8", "strict")
                except UnicodeError:
                    raise _invalid() from None
                comparable = name.casefold() if namespace == "headers" else name
                if comparable in seen:
                    raise _invalid()
                seen.add(comparable)
                normalized.append((name, _frame(b"mcp-receipt-secret-field-v1", (name_bytes, value_bytes))))
            namespace_frames.append(
                _frame(
                    f"mcp-receipt-secret-{namespace}-v1".encode("ascii"),
                    tuple(frame for _name, frame in sorted(normalized)),
                )
            )
        return _frame(b"mcp-receipt-secrets-v1", tuple(namespace_frames))

    @staticmethod
    def _tool_snapshots(
        tools: Sequence[MCPDiscoveredTool],
    ) -> tuple[tuple[_ToolSnapshot, ...], tuple[_StoredTool, ...]]:
        if isinstance(tools, (str, bytes)) or not isinstance(tools, Sequence):
            raise _invalid()
        if len(tools) > _MAX_TOOLS:
            raise _invalid()
        snapshots: list[_ToolSnapshot] = []
        normalized: list[_StoredTool] = []
        names: set[str] = set()
        schema_bytes = 0
        for tool in tools:
            if not isinstance(tool, MCPDiscoveredTool) or tool.tool_name in names:
                raise _invalid()
            names.add(tool.tool_name)
            canonical = validate_mcp_input_schema(tool.input_schema)
            schema_bytes += len(canonical.canonical_bytes)
            if schema_bytes > _MAX_TOOL_SCHEMA_BYTES:
                raise _invalid()
            snapshot = _ToolSnapshot(
                tool.tool_name,
                _hash_description(tool.description),
                canonical.sha256,
            )
            snapshots.append(snapshot)
            normalized.append(
                _StoredTool(
                    tool_name=tool.tool_name,
                    description=tool.description,
                    canonical_schema=canonical.canonical_bytes,
                )
            )
        return tuple(snapshots), tuple(normalized)

    @staticmethod
    def _decisions(decisions: Sequence[ToolDecision]) -> tuple[ToolDecision, ...]:
        if isinstance(decisions, (str, bytes)) or not isinstance(decisions, Sequence):
            raise _invalid()
        if len(decisions) > _MAX_TOOLS:
            raise _invalid()
        normalized: list[ToolDecision] = []
        names: set[str] = set()
        for decision in decisions:
            if not isinstance(decision, ToolDecision) or decision.tool_name in names:
                raise _invalid()
            names.add(decision.tool_name)
            normalized.append(decision)
        return tuple(normalized)

    @staticmethod
    def _risk_codes(risks: Sequence[str]) -> tuple[str, ...]:
        if isinstance(risks, (str, bytes)) or not isinstance(risks, Sequence):
            raise _invalid()
        if len(risks) > _MAX_RISKS:
            raise _invalid()
        result: list[str] = []
        seen: set[str] = set()
        for code in risks:
            if not isinstance(code, str) or not code or code in seen:
                raise _invalid()
            _text_bytes(code)
            seen.add(code)
            result.append(code)
        return tuple(sorted(result))

    def _purge_expired_locked(self, now: float) -> None:
        for receipt, record in tuple(self._records.items()):
            if now >= record.expires_at:
                self._delete_record_locked(receipt)

    def _delete_record_locked(self, receipt: str) -> None:
        record = self._records.pop(receipt, None)
        if record is not None:
            self._snapshot_bytes = max(0, self._snapshot_bytes - record.snapshot_bytes)

    def _now(self) -> float:
        try:
            value = self._clock()
        except Exception:
            raise _invalid() from None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _invalid()
        result = float(value)
        if not math.isfinite(result) or result < 0:
            raise _invalid()
        return result
