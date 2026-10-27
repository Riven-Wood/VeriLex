"""Lossless UTF-8 snapshots and continuous quotes from previously read ranges.

Offsets are zero-based Unicode code points, end-exclusive (not JS UTF-16).
Line numbers are one-based; CRLF counts as one newline. The BOM is preserved.
These pure primitives accept trusted server-created snapshots/receipts; they do
not authenticate IDs, read paths, grant access, or verify official originals.
"""

from dataclasses import dataclass
from hashlib import sha256
from bisect import bisect_right
import re


class SourceError(ValueError):
    pass


def _range(start: int, end: int, size: int) -> None:
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= size:
        raise SourceError("invalid_range")


@dataclass(frozen=True)
class SourceSnapshot:
    library_id: str
    document_id: str
    source_name: str
    raw: bytes

    def __post_init__(self) -> None:
        if type(self.raw) is not bytes:
            raise SourceError("immutable_bytes_required")
        if not all((self.library_id, self.document_id, self.source_name)):
            raise SourceError("source_identity_required")
        try:
            self.raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SourceError("invalid_utf8") from exc

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", errors="strict")

    @property
    def version(self) -> str:
        return sha256(self.raw).hexdigest()

    def read(self, *, version: str, start: int, end: int,
             thread_id: str, turn_id: str, max_chars: int) -> "ReadReceipt":
        if version != self.version:
            raise SourceError("source_changed")
        _range(start, end, len(self.text))
        if type(max_chars) is not int or max_chars < 1 or end - start > max_chars:
            raise SourceError("read_budget_exceeded")
        if not thread_id or not turn_id:
            raise SourceError("turn_identity_required")
        return ReadReceipt(self, start, end, thread_id, turn_id)


@dataclass(frozen=True)
class QuoteSnapshot:
    library_id: str
    document_id: str
    source_name: str
    version: str
    thread_id: str
    turn_id: str
    start: int
    end: int
    line_start: int
    line_end: int
    text: str


@dataclass(frozen=True)
class ReadReceipt:
    source: SourceSnapshot
    start: int
    end: int
    thread_id: str
    turn_id: str

    @property
    def text(self) -> str:
        return self.source.text[self.start:self.end]

    def quote(self, *, start: int, end: int, thread_id: str, turn_id: str,
              claimed_text: str | None = None) -> QuoteSnapshot:
        if (thread_id, turn_id) != (self.thread_id, self.turn_id):
            raise SourceError("receipt_scope_mismatch")
        text = self.source.text
        _range(start, end, len(text))
        if start < self.start or end > self.end:
            raise SourceError("quote_not_read")
        quoted = text[start:end]
        if claimed_text is not None and claimed_text != quoted:
            raise SourceError("quote_text_mismatch")
        line_starts = [0] + [m.end() for m in re.finditer(r"\r\n|\r|\n", text)]
        return QuoteSnapshot(
            self.source.library_id, self.source.document_id, self.source.source_name,
            self.source.version, self.thread_id, self.turn_id, start, end,
            bisect_right(line_starts, start), bisect_right(line_starts, end - 1), quoted,
        )
