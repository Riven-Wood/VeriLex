"""Agent 工具 ``contract_opinion``：意见的结构化提交入口（绑定真实工具调用来源）。"""
from __future__ import annotations

import json

import anyio

from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools.registry import (
    ToolCategory,
    ToolContext,
    ToolDefinition,
    ToolResult,
    ToolError,
    ToolScope,
    ToolRegistry,
)

from verilex_runtime.contract_review.service import ContractReviewService
from verilex_runtime.contract_review.errors import ContractReviewError

_PARAMETERS = {
    "type": "object",
    "properties": {
        "opinion_id": {
            "type": "string",
            "description": "可选；同一条意见补充更新时沿用原 ID。",
            "pattern": "^[A-Za-z0-9_-]{1,80}$",
        },
        "clause_label": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "条款定位，如「第六条 违约责任」。",
        },
        "clause_excerpt": {
            "type": "string",
            "minLength": 1,
            "maxLength": 600,
            "description": "条款原文摘录（有界）。",
        },
        "anchor_text": {
            "type": "string",
            "minLength": 1,
            "maxLength": 600,
            "description": "批注锚定文本，必须逐字摘自合同原文。",
        },
        "occurrence": {
            "type": "integer",
            "minimum": 0,
            "description": "锚定文本在合同中出现多次时的序号（从 0 开始）。",
        },
        "level": {"type": "string", "minLength": 1, "maxLength": 40},
        "category": {"type": "string", "minLength": 1, "maxLength": 40},
        "body": {"type": "string", "minLength": 1, "maxLength": 4000},
        "suggestion": {"type": "string", "maxLength": 4000},
        "replacement_text": {"type": "string", "maxLength": 4000, "description": "需要修改条文时填写：只包含用于替换 anchor_text 的最终条文，不含解释；空字符串表示删除该选区。仅提示风险或需当事人补事实时省略。本字段写入待接受的 Word 修订，理由写入批注。"},
        "citations": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "document": {"type": "string", "minLength": 1, "maxLength": 200},
                    "source_call_id": {"type": "string", "minLength": 1, "maxLength": 200,
                                       "description": "本会话成功工具结果提供的 source_call_id；准确对应所引用的返回范围和版本。"},
                },
                "required": ["document", "source_call_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["clause_label", "clause_excerpt", "anchor_text", "body"],
    "additionalProperties": False,
}


def register_contract_review_tools(
    registry: ToolRegistry, service: ContractReviewService
) -> None:
    async def submit(context: ToolContext, arguments: dict) -> ToolResult:
        try:
            outcome = await anyio.to_thread.run_sync(
                service.submit_opinion, context, arguments
            )
        except ContractReviewError as error:
            raise ToolError(error.code, error.message) from None
        return ToolResult(True, json.dumps(outcome, ensure_ascii=False))

    registry.register(
        ToolDefinition(
            name="contract_opinion",
            description=(
                "提交一条合同审查意见（每条一调用），以 Word 修订和批注交付。需要修改时提供 replacement_text；锚定文本必须逐字摘自合同原文；"
                "参考来源使用成功工具结果的 source_call_id。程序只核对调用身份，不判断条号或法律适用；保留读取范围和缺口。"
            ),
            parameters=_PARAMETERS,
            risk_level=RiskLevel.LOW,
            execute=submit,
            category=ToolCategory.DOCUMENTS,
            scopes=frozenset({ToolScope.READ, ToolScope.WRITE}),
            requires_workspace=False,
        )
    )

    async def propose(context: ToolContext, arguments: dict) -> ToolResult:
        try:
            outcome = await anyio.to_thread.run_sync(service.editor.propose, context, arguments)
        except ContractReviewError as error:
            raise ToolError(error.code, error.message) from None
        return ToolResult(True, json.dumps(outcome, ensure_ascii=False))

    registry.register(ToolDefinition(
        name="contract_edit_proposal",
        description=("针对本轮合同编辑选区提出一处文字修改；before 必须逐字等于本轮选中文字，"
                     "after 只替换该选区。只在用户明确要求改写时使用；提交不会直接改动正文，等待用户接受。"),
        parameters={
            "type": "object", "properties": {
                "before": {"type": "string", "minLength": 1, "maxLength": 12000},
                "after": {"type": "string", "maxLength": 12000},
                "reason": {"type": "string", "maxLength": 2000},
                "proposal_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,80}$"},
            }, "required": ["before", "after"], "additionalProperties": False,
        },
        risk_level=RiskLevel.LOW, execute=propose, category=ToolCategory.DOCUMENTS,
        scopes=frozenset({ToolScope.READ, ToolScope.WRITE}), requires_workspace=False,
    ))
