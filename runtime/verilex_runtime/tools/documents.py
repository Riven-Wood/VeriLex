"""The single Word creation tool of the DOCX V1 loop.

``document_create`` never receives a source document: it turns a bounded fixed-layout
Markdown draft into a new workspace artifact requested by the user. Everything risky (workspace authority, fixed layout,
reopen validation, no-replace publish) stays inside ``V1DocumentService``;
this module only binds the exact schema, normalizes service errors into the
stable user-facing codes, and keeps tool output free of content or paths.
"""

from __future__ import annotations

import json

from verilex_runtime.documents.errors import DocumentDomainError
from verilex_runtime.documents.v1_service import V1CreateRequest, V1DocumentService
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

# The one additive-artifact schema. Field lengths mirror the service-level
# limits.
DOCUMENT_CREATE_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "output_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
            "description": "新文档的文件名，例如 意见.docx",
        },
        "title": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "唯一文档主标题；正文不要重复此标题",
        },
        "content_markdown": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120000,
            "description": "受限 Markdown 正文：仅支持章节标题、段落、无序/有序列表；不重复title中的文档主标题",
        },
    },
    "required": ["output_name", "title", "content_markdown"],
    "additionalProperties": False,
}

_DOCUMENT_ERROR_MESSAGES = {
    "document_workspace_required": "请先为这个任务选择工作区，再生成 Word 文件",
    "document_create_invalid": "生成内容或文件名不符合首发支持范围",
    "document_validation_failed": "新 Word 自检没有通过，因此没有交付文件",
    "document_publish_conflict": "新文件发布时发生同名竞争，原文件没有被覆盖",
}
_DOCUMENT_CREATE_INVALID_GUIDANCE = (
    "。支持的排版：# ~ ### 标题、普通段落、- 列表、1. 编号列表；"
    "不支持缩进/嵌套子列表、#### 以上标题、表格、图片、链接、代码块、HTML、"
    "引用、星号列表、分割线；列表结束后必须先空一行。"
    "请使用上述支持的排版，其他排版需求可选择其他工具。"
)


async def _execute_document_create(
    document_service: V1DocumentService,
    context: ToolContext,
    arguments: dict,
) -> ToolResult:
    request = V1CreateRequest(
        output_name=arguments["output_name"],
        title=arguments["title"],
        content_markdown=arguments["content_markdown"],
    )
    try:
        record = await document_service.create(context, request)
    except DocumentDomainError as error:
        message = _DOCUMENT_ERROR_MESSAGES.get(error.code)
        if message is None:
            raise
        if error.code == "document_create_invalid":
            if error.detail:
                message = f"{message}：{error.detail}"
            message = f"{message}{_DOCUMENT_CREATE_INVALID_GUIDANCE}"
        raise ToolError(error.code, message) from None
    return ToolResult(
        ok=True,
        output=json.dumps(
            {
                "artifact_id": record.id,
                "display_name": record.display_name,
                "published_relative_path": record.published_relative_path,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def register_document_tools(
    registry: ToolRegistry,
    document_service: V1DocumentService,
) -> None:
    async def execute(context: ToolContext, arguments: dict) -> ToolResult:
        return await _execute_document_create(
            document_service, context, arguments
        )

    registry.register(
        ToolDefinition(
            name="document_create",
            description=(
                "在当前工作区新增一份固定版式 Word 文档。"
                "仅支持标题、段落与列表，不修改或读取任何现有文件。"
            ),
            parameters=DOCUMENT_CREATE_PARAMETERS,
            risk_level=RiskLevel.HIGH,
            execute=execute,
            source=ToolSource.BUILTIN,
            category=ToolCategory.DOCUMENTS,
            scopes=frozenset({ToolScope.WRITE}),
            requires_workspace=True,
            approval_behavior=ApprovalBehavior.ADDITIVE_ARTIFACT,
        )
    )
