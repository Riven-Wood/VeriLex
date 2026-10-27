"""意见数据结构与批注文本模板（规格第 5 节）。

等级/分类标签由所选 Skill 定义：通用 Skill 等级为高/中/低、分类为必须修改/建议协商；
`skill_levels` 把 Skill 内部等级键映射为展示标签，未命中时原样使用。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Citation:
    """一条规范性文件援引：文件与条款 + 来源渠道 + 查证日期。"""

    document: str
    source_kind: str  # mcp / knowledge / web
    source_label: str
    verified_at: str  # YYYY-MM-DD
    source_call_id: str = ""


@dataclass(frozen=True, slots=True)
class Opinion:
    """意见卡全字段（规格 5.1）。"""

    opinion_id: str
    clause_label: str
    clause_excerpt: str
    anchor_text: str
    occurrence: int | None = None
    level: str = ""
    category: str = ""
    body: str = ""
    suggestion: str = ""
    adopted: bool = True
    edited_text: str | None = None
    citations: tuple[Citation, ...] = field(default=())
    replacement_text: str | None = None


def render_comment_text(
    opinion: Opinion, *, skill_levels: Mapping[str, str]
) -> str:
    """渲染批注文本：`【等级】分类：正文 修改建议 参考来源：《…》（来源：…，读取于…）`。

    编辑后的文本优先于模型正文；建议与引用段仅在非空时出现。
    """
    level = skill_levels.get(opinion.level, opinion.level)
    body = opinion.edited_text if opinion.edited_text else opinion.body
    parts = [f"【{level}】{opinion.category}：{body}" if opinion.category
             else f"【{level}】{body}"]
    if opinion.suggestion:
        parts.append(f"修改建议：{opinion.suggestion}")
    for citation in opinion.citations:
        parts.append(
            f"参考来源：{citation.document}"
            + (f"（来源：{citation.source_label}，读取于 {citation.verified_at}）" if citation.source_call_id else "（未关联读取记录）")
        )
    return " ".join(parts)
