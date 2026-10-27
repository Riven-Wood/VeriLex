"""Agent tools ``law_search`` and ``law_read`` for the official law database.

Both tools mirror the controlled web tools: fixed Chinese error copy, no
internal details in any output, an atomic per-Turn network budget, and a
cache-first read path that never re-sends a request for an unchanged
snapshot.
"""

from __future__ import annotations

import json
from threading import Lock

from verilex_runtime.legal.cache import LawCacheService
from verilex_runtime.legal.client import LawClientError, OfficialLawClient
from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools.registry import (
    ApprovalBehavior,
    ToolCategory,
    ToolContext,
    ToolDefinition,
    ToolError,
    ToolRegistry,
    ToolResult,
    ToolScope,
    ToolSource,
)

_CITATION_NOTICE = "搜索结果仅为候选元数据，不可直接引用；引用前先用 law_read 读取原文。"
_MAX_KEYWORD_CHARACTERS = 100
_MAX_BBBS_CHARACTERS = 64
_MAX_CLAUSE_CHARACTERS = 32
_MAX_PAGE = 100
_SCOPES = frozenset({ToolScope.NETWORK, ToolScope.EXTERNAL_SERVICE})

_COPY: dict[str, str] = {
    "law_network_error": "无法连接国家法律法规数据库，请稍后重试。",
    "law_rate_limited": "国家法律法规数据库暂时限流，请稍后重试。",
    "law_site_changed": "国家法律法规数据库页面结构可能已改版，读取失败。",
    "law_timeout": "国家法律法规数据库响应超时，请稍后重试。",
    "law_not_found": "未能定位到指定的条文。",
    "law_cache_error": "本地法律缓存读写失败。",
    "law_budget_exceeded": (
        "本回合法律数据库读取次数已达上限，请基于已获取的内容继续。"
    ),
}


class LawTurnBudget:
    """Atomic per-Turn cap on official-law-database network calls."""

    def __init__(self, *, max_calls: int = 8) -> None:
        if (
            not isinstance(max_calls, int)
            or isinstance(max_calls, bool)
            or max_calls < 1
        ):
            raise ValueError("law budget limit must be a positive integer")
        self._max_calls = max_calls
        self._calls: dict[str, int] = {}
        self._lock = Lock()

    @property
    def max_calls(self) -> int:
        return self._max_calls

    def reserve(self, turn_id: str, calls: int = 1) -> None:
        turn_key = _require_turn_id(turn_id)
        if (
            not isinstance(calls, int)
            or isinstance(calls, bool)
            or calls < 1
        ):
            raise _context_error()
        with self._lock:
            current = self._calls.get(turn_key, 0)
            if current + calls > self._max_calls:
                raise _law_error("law_budget_exceeded")
            self._calls[turn_key] = current + calls

    def clear(self, turn_id: str) -> None:
        turn_key = _require_turn_id(turn_id)
        with self._lock:
            self._calls.pop(turn_key, None)


def register_law_tools(
    registry: ToolRegistry,
    client: OfficialLawClient,
    cache: LawCacheService,
    budget: LawTurnBudget,
) -> None:
    from verilex_runtime.legal.service import LawRetrievalService

    service = LawRetrievalService(client, cache)

    async def law_search(context: ToolContext, args: dict) -> ToolResult:
        turn_id = _context_value(context.turn_id)
        _context_value(context.tool_call_id)
        keyword, search_type, sxx, page = _search_arguments(args)
        budget.reserve(turn_id, 1)
        try:
            payload = await service.search(
                keyword, search_type=search_type, sxx=sxx, page=page
            )
        except LawClientError as error:
            raise _law_error(error.code) from None
        except ToolError:
            raise
        except Exception:
            raise _law_error("law_network_error") from None
        payload["notice"] = _CITATION_NOTICE
        return ToolResult(ok=True, output=_json(payload))

    async def law_read(context: ToolContext, args: dict) -> ToolResult:
        turn_id = _context_value(context.turn_id)
        _context_value(context.tool_call_id)
        bbbs, clause, force_refresh = _read_arguments(args)

        def reserve(calls: int) -> None:
            budget.reserve(turn_id, calls)

        try:
            payload = await service.read_statute(
                bbbs,
                clause,
                force_refresh=force_refresh,
                reserve=reserve,
            )
        except LawClientError as error:
            raise _law_error(error.code) from None
        except ToolError:
            raise
        except Exception:
            raise _law_error("law_network_error") from None
        return ToolResult(ok=True, output=_json(payload))

    registry.register(
        ToolDefinition(
            name="law_search",
            description=(
                "检索国家法律法规数据库的法规候选（法律、司法解释等），"
                "返回候选元数据与效力状态；引用前必须先用 law_read 读取原文。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": _MAX_KEYWORD_CHARACTERS,
                    },
                    "search_type": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 9,
                        "default": 1,
                    },
                    "sxx": {
                        "type": "array",
                        "items": {"type": "integer", "enum": [1, 2, 3]},
                        "maxItems": 3,
                    },
                    "page": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_PAGE,
                        "default": 1,
                    },
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=law_search,
            source=ToolSource.BUILTIN,
            category=ToolCategory.EXTERNAL,
            scopes=_SCOPES,
            requires_workspace=False,
            approval_behavior=ApprovalBehavior.THREAD_FIRST_USE,
        )
    )
    registry.register(
        ToolDefinition(
            name="law_read",
            description=(
                "读取一条法规的官方原文（docx 正文）与效力、日期等元数据，"
                "优先使用本机缓存；可用 clause 定位到具体条文。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "bbbs": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": _MAX_BBBS_CHARACTERS,
                    },
                    "clause": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": _MAX_CLAUSE_CHARACTERS,
                    },
                    "force_refresh": {
                        "type": "boolean",
                        "default": False,
                    },
                },
                "required": ["bbbs"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=law_read,
            source=ToolSource.BUILTIN,
            category=ToolCategory.EXTERNAL,
            scopes=_SCOPES,
            requires_workspace=False,
            approval_behavior=ApprovalBehavior.THREAD_FIRST_USE,
        )
    )


def _search_arguments(args: object) -> tuple[str, int, list[int] | None, int]:
    if not isinstance(args, dict) or set(args) - {
        "keyword",
        "search_type",
        "sxx",
        "page",
    }:
        raise _arguments_error()
    keyword = args.get("keyword")
    search_type = args.get("search_type", 1)
    sxx = args.get("sxx")
    page = args.get("page", 1)
    if (
        not isinstance(keyword, str)
        or not 1 <= len(keyword) <= _MAX_KEYWORD_CHARACTERS
        or not keyword.strip()
        or not isinstance(search_type, int)
        or isinstance(search_type, bool)
        or not 1 <= search_type <= 9
        or not isinstance(page, int)
        or isinstance(page, bool)
        or not 1 <= page <= _MAX_PAGE
    ):
        raise _arguments_error()
    if sxx is not None:
        if not isinstance(sxx, list) or not 1 <= len(sxx) <= 3:
            raise _arguments_error()
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value not in {1, 2, 3}
            for value in sxx
        ):
            raise _arguments_error()
    return keyword, search_type, sxx, page


def _read_arguments(args: object) -> tuple[str, str | None, bool]:
    if not isinstance(args, dict) or set(args) - {
        "bbbs",
        "clause",
        "force_refresh",
    }:
        raise _arguments_error()
    bbbs = args.get("bbbs")
    clause = args.get("clause")
    force_refresh = args.get("force_refresh", False)
    if (
        not isinstance(bbbs, str)
        or not 1 <= len(bbbs) <= _MAX_BBBS_CHARACTERS
        or not bbbs.strip()
    ):
        raise _arguments_error()
    if clause is not None:
        if (
            not isinstance(clause, str)
            or not 1 <= len(clause) <= _MAX_CLAUSE_CHARACTERS
            or not clause.strip()
        ):
            raise _arguments_error()
    if not isinstance(force_refresh, bool):
        raise _arguments_error()
    return bbbs, clause, force_refresh


def _context_value(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
    ):
        raise _context_error()
    return value


def _require_turn_id(turn_id: object) -> str:
    return _context_value(turn_id)


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _law_error(code: str) -> ToolError:
    return ToolError(code, _COPY[code])


def _context_error() -> ToolError:
    return ToolError(
        "law_tool_context_invalid",
        "The law tool execution context is invalid.",
    )


def _arguments_error() -> ToolError:
    return ToolError(
        "law_arguments_invalid",
        "The law tool arguments are invalid.",
    )


__all__ = ["LawTurnBudget", "register_law_tools"]
