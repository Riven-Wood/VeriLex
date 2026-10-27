"""Boundary and bounded user-list adaptation of OpenWorker coworker/compaction.py
at 5bc10d928e0b64aae74313349a3b17bd19643ae2 (MIT).
Copyright (c) 2024 Andrew Ng. See THIRD_PARTY_NOTICES.md.
VeriLex keeps whole turns, SQLite message sequences, and its CJK-aware estimator.
"""
import json
from verilex_runtime.context_usage.estimator import estimate_context_usage
from verilex_runtime.context_usage.models import ContextSegment
from verilex_runtime.sessions.message_text import message_text
from verilex_runtime.storage.models import MessageRole


def should_compact(estimate: int, window: int | None) -> bool:
    return window is not None and estimate >= window * .8


def text_of(view) -> str:
    return message_text(json.dumps(view.content, ensure_ascii=False))


def pick_boundary(views, keep_budget: int, previous: int = 0) -> int | None:
    candidates = [i for i, view in enumerate(views)
                  if view.role == MessageRole.USER and view.sequence > previous and i > 0]
    if not candidates:
        return None
    # Earliest complete user turn fitting the recent budget, else keep newest.
    for i in candidates:
        segments = [ContextSegment("messages", json.dumps(v.content, ensure_ascii=False)) for v in views[i:]]
        if estimate_context_usage(segments, None).used_tokens <= keep_budget:
            return views[i].sequence
    return views[candidates[-1]].sequence


def render_span(views, previous_summary: str, char_budget: int) -> str:
    lines = ["先前摘要（可能遗漏细节）：\n" + previous_summary] if previous_summary else []
    for view in views:
        text = text_of(view)
        if view.role == MessageRole.TOOL:
            text = json.dumps(view.content, ensure_ascii=False)[:400]
        elif view.role == MessageRole.ASSISTANT and not text:
            text = json.dumps(view.content, ensure_ascii=False)[:400]
        lines.append(f"{view.role.value}: {text}")
    result = "\n".join(lines)
    if len(result) <= char_budget:
        return result
    # Bounded request: keep both prior state and the most recent changes.
    half = max(0, (char_budget - 30) // 2)
    return result[:half] + "\n[中间部分因长度省略]\n" + result[-half:]


def render_trim_block(views, previous_summary: str, char_budget: int) -> str:
    users = [text_of(view)[:600] for view in views if view.role == MessageRole.USER]
    header = f"摘要生成失败，以下仅为早期问题清单；省略了 {max(0, len(users)-40)} 条更早问题。\n"
    previous = ("先前摘要：" + previous_summary[:char_budget // 3] + "\n") if previous_summary else ""
    return (header + previous + "\n".join(users[-40:]))[:char_budget]
