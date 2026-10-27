"""合同审查完整性验证器：委托文档层独立校验器，映射为固定失败分类。

与写入引擎分离（comment_verify 不导入写入引擎），导出门禁只认本报告。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.documents.comment_verify import verify_comment_copy

UNEXPECTED_PART_CHANGE = "unexpected_part_change"
DOCUMENT_BODY_CHANGED = "document_body_changed"
CONTENT_TYPES_DRIFT = "content_types_drift"
RELS_DRIFT = "rels_drift"
UNREADABLE_PACKAGE = "unreadable_package"


@dataclass(frozen=True, slots=True)
class VerificationReport:
    ok: bool
    failures: tuple[str, ...]


def _category(difference: str) -> str:
    if difference.startswith("changed:"):
        name = difference.split(":", 1)[1]
        if name == "word/document.xml":
            return DOCUMENT_BODY_CHANGED
        if name == "[Content_Types].xml":
            return CONTENT_TYPES_DRIFT
        if name == "word/_rels/document.xml.rels":
            return RELS_DRIFT
        return UNEXPECTED_PART_CHANGE
    if difference == "invalid-comment-copy":
        return UNREADABLE_PACKAGE
    return UNEXPECTED_PART_CHANGE


class AnnotationIntegrityVerifier:
    def verify(self, original: Path, annotated: Path) -> VerificationReport:
        try:
            data = annotated.read_bytes()
        except OSError:
            return VerificationReport(False, (UNREADABLE_PACKAGE,))
        try:
            result = verify_comment_copy(original, data)
        except AttachmentDomainError:
            return VerificationReport(False, (UNREADABLE_PACKAGE,))
        failures = tuple(sorted({_category(d) for d in result.differences}))
        return VerificationReport(result.passed, failures)
