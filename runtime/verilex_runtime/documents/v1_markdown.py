from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from verilex_runtime.documents.errors import DocumentDomainError


MAX_SOURCE_CHARACTERS = 120_000
HEADING_RE = re.compile(r"(?P<marks>#{1,3}) (?P<text>.+)")
NUMBERED_RE = re.compile(r"\d+\. (?P<text>.+)")
HTML_RE = re.compile(r"<(?:[!?/]|[A-Za-z])[^>]*(?:>|$)")
IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\([^\n)]*\)")
REMOTE_LINK_RE = re.compile(
    r"(?<!!)\[[^\]\n]*\]\(\s*(?:[A-Za-z][A-Za-z0-9+.-]*:|//)[^\n)]*\)",
    re.IGNORECASE,
)
TABLE_SEPARATOR_RE = re.compile(
    r"\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*"
)
THEMATIC_BREAK_RE = re.compile(r"(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,}")

SUPPORTED_MARKDOWN_SUMMARY = (
    "支持的排版：# ~ ### 标题、普通段落、- 开头的列表、1. 编号列表、"
    "<!-- pagebreak --> 分页；网址可作为普通文字。列表之后必须空一行。"
    "不支持：缩进或嵌套（含子列表）、#### 及更深层标题、表格、图片、"
    "超链接、代码块、HTML、引用（>）、星号加号列表、分割线。"
)


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    kind: Literal["paragraph", "heading", "bullet", "numbered", "pagebreak"]
    text: str
    level: int = 0


def _invalid(detail: str | None = None) -> DocumentDomainError:
    return DocumentDomainError("document_create_invalid", detail)


def _has_disallowed_control_character(source: str) -> bool:
    return any(
        (ord(character) < 0x20 and character not in {"\r", "\n"})
        or 0x7F <= ord(character) <= 0x9F
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in source
    )


def _unsupported_block_reason(line_number: int, line: str) -> str | None:
    stripped = line.lstrip(" ")
    indent = len(line) - len(stripped)
    if indent:
        return (
            f"第 {line_number} 行：行首缩进（嵌套列表或缩进文本）不受支持，"
            "所有内容必须顶格，子项请写成独立的「- 列表」或普通段落"
        )
    if THEMATIC_BREAK_RE.fullmatch(stripped) or re.fullmatch(r"=+\s*", stripped):
        return f"第 {line_number} 行：分割线不受支持，请删除该行"
    if re.fullmatch(r"[-+*]+|\d+[.)]", stripped):
        return f"第 {line_number} 行：孤立的列表记号不受支持，请写成「- 内容」或「1. 内容」"
    if stripped.startswith(("* ", "+ ")):
        return f"第 {line_number} 行：星号/加号列表不受支持，请改用「- 」开头"
    if stripped.startswith(">"):
        return (
            f"第 {line_number} 行：HTML、引用、图片或链接标记不受支持，"
            "请删除相关标记、直接写文字"
        )
    if stripped.startswith("|"):
        return f"第 {line_number} 行：表格不受支持，请改写成普通段落或列表"
    if stripped.startswith("#") and HEADING_RE.fullmatch(line) is None:
        return (
            f"第 {line_number} 行：标题只支持 #、##、### 三级（# 后必须有空格），"
            "请改用三级以内"
        )
    if re.match(r"\d+[.)] ", stripped) and NUMBERED_RE.fullmatch(line) is None:
        return f"第 {line_number} 行：编号列表请写成「1. 内容」格式（数字+点+空格）"
    return None


def parse_v1_markdown(source: str) -> tuple[MarkdownBlock, ...]:
    if not isinstance(source, str) or not source.strip():
        raise _invalid(f"内容为空，请提供正文")
    if len(source) > MAX_SOURCE_CHARACTERS:
        raise _invalid(
            f"内容过长（{len(source)} 字，上限 {MAX_SOURCE_CHARACTERS} 字），"
            "请精简后重试"
        )
    if _has_disallowed_control_character(source):
        raise _invalid("内容含不可见控制字符，请重新整理文本")

    blocks: list[MarkdownBlock] = []
    paragraph_lines: list[str] = []
    list_requires_break = False

    def flush_paragraph() -> None:
        if paragraph_lines:
            blocks.append(MarkdownBlock("paragraph", "\n".join(paragraph_lines)))
            paragraph_lines.clear()

    for line_number, line in enumerate(
        source.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1
    ):
        heading_match = HEADING_RE.fullmatch(line)
        if not line:
            flush_paragraph()
            list_requires_break = False
        elif line.strip() and line[:1] == " ":
            # Indentation is reported ahead of the blank-line rule so nested
            # lists get the actionable "no indent" reason, not "add a blank".
            raise _invalid(
                f"第 {line_number} 行：行首缩进（嵌套列表或缩进文本）不受支持，"
                "所有内容必须顶格，子项请写成独立的「- 列表」或普通段落"
            )
        elif list_requires_break and not (
            line.startswith("- ")
            or NUMBERED_RE.fullmatch(line)
            or heading_match
            or line == "<!-- pagebreak -->"
        ):
            raise _invalid(
                f"第 {line_number} 行：列表结束后必须先空一行，再接普通段落"
            )
        elif line == "<!-- pagebreak -->":
            flush_paragraph()
            blocks.append(MarkdownBlock("pagebreak", ""))
            list_requires_break = False
        else:
            reason = None
            if (
                HTML_RE.search(line)
                or IMAGE_RE.search(line)
                or REMOTE_LINK_RE.search(line)
            ):
                reason = (
                    f"第 {line_number} 行：HTML、图片或链接不受支持，"
                    "请删除标记、直接写文字（网址请只写文字不要链接）"
                )
            elif "```" in line or "~~~" in line:
                reason = f"第 {line_number} 行：代码块不受支持，请改写成普通段落"
            elif TABLE_SEPARATOR_RE.fullmatch(line):
                reason = f"第 {line_number} 行：表格不受支持，请改写成列表或段落"
            if reason is None:
                reason = _unsupported_block_reason(line_number, line)
            if reason is not None:
                raise _invalid(reason)
            if heading_match:
                flush_paragraph()
                blocks.append(
                    MarkdownBlock(
                        "heading",
                        heading_match.group("text"),
                        len(heading_match.group("marks")),
                    )
                )
                list_requires_break = False
            elif line.startswith("- "):
                flush_paragraph()
                if not line[2:]:
                    raise _invalid(f"第 {line_number} 行：「-」后必须写内容")
                blocks.append(MarkdownBlock("bullet", line[2:]))
                list_requires_break = True
            elif match := NUMBERED_RE.fullmatch(line):
                flush_paragraph()
                blocks.append(MarkdownBlock("numbered", match.group("text")))
                list_requires_break = True
            elif line.startswith((">", "|", "```", "#")):
                raise _invalid(
                    f"第 {line_number} 行：行首的 {line.lstrip()[:2].strip()!r} "
                    "标记不受支持"
                )
            else:
                paragraph_lines.append(line)
    flush_paragraph()

    if not blocks:
        raise _invalid("内容为空，请提供正文")
    return tuple(blocks)
