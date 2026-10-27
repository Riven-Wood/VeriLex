"""合同审查批注写入：委托文档层的经验证写入引擎，原件没有写路径。

写入与验证一体（write_verified_comments）：任何锚定或验证失败都不产出文件；
副本落盘走临时文件＋原子替换，失败不遗留半截文件。
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.documents.comments import CommentSpec as DocCommentSpec
from verilex_runtime.documents.comments import write_verified_comments
from verilex_runtime.documents.errors import DocumentDomainError

from verilex_runtime.contract_review.errors import ContractReviewError

LIMITATIONS = (
    "批注回复串与已解决状态不写入。",
    "页眉、页脚、脚注与文本框不能加批注。",
    "含修订痕迹或复杂内容的锚定区间不支持。",
)


@dataclass(frozen=True, slots=True)
class CommentSpec:
    """合同审查批注：锚定文本（可选出现次序）+ 已渲染意见文本 + 署名。"""

    anchor_text: str
    text: str
    author: str
    occurrence: int | None = None


def _initials(author: str) -> str:
    return author[:2] if author else "VL"


class ContractAnnotator:
    """把审查意见写入合同副本；批注日期为写入时刻（审查时间）。"""

    def write(
        self,
        original: Path,
        comments: Sequence[CommentSpec],
        dest: Path,
    ) -> None:
        if not comments:
            raise ContractReviewError(
                "contract_annotation_write_failed", "no comments to write"
            )
        for comment in comments:
            if not (comment.anchor_text.strip() and comment.text.strip()
                    and comment.author.strip()):
                raise ContractReviewError(
                    "contract_annotation_write_failed", "empty comment field"
                )
        # 原件不变式在写入器层守卫：目标路径与原件同路径或别名（硬链接）一律拒绝。
        if original.resolve() == dest.resolve() or (
            dest.exists() and os.path.samefile(original, dest)
        ):
            raise ContractReviewError(
                "contract_annotation_write_failed", "destination aliases original"
            )
        try:
            outcome = write_verified_comments(
                original,
                [
                    DocCommentSpec(
                        anchor_text=c.anchor_text,
                        comment_text=c.text,
                        author=c.author,
                        initials=_initials(c.author),
                        occurrence=c.occurrence,
                    )
                    for c in comments
                ],
            )
        except AttachmentDomainError:
            raise ContractReviewError("contract_file_unsupported") from None
        except DocumentDomainError as error:
            raise ContractReviewError(
                "contract_annotation_write_failed", error.code
            ) from None
        except Exception:
            # 不向调用方泄漏库内部异常与堆栈，一律收敛为固定错误码。
            raise ContractReviewError(
                "contract_annotation_write_failed", "unexpected writer failure"
            ) from None
        self._atomic_write(dest, outcome.copy_bytes)

    @staticmethod
    def _atomic_write(dest: Path, data: bytes) -> None:
        try:
            handle, temp_name = tempfile.mkstemp(
                dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(data)
                os.replace(temp_name, dest)
            except BaseException:
                with suppress(OSError):
                    os.unlink(temp_name)
                raise
        except OSError:
            raise ContractReviewError(
                "contract_annotation_write_failed", "destination write failed"
            ) from None
