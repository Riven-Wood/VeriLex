import json
from unittest.mock import AsyncMock

import pytest

from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.agent.limits import TurnLimits
from verilex_runtime.documents.errors import DocumentDomainError
from verilex_runtime.documents.v1_service import V1CreateRequest, V1DocumentService
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    ArtifactRecord,
    ArtifactSourceKind,
    AuthMode,
    ProviderKind,
    ProviderRecord,
    RiskLevel,
    ValidationStatus,
    VisionStatus,
)
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)
from verilex_runtime.storage.tool_repository import (
    ApprovalRepository,
    ToolCallRepository,
)
from verilex_runtime.storage.workspace_repository import WorkspaceRepository
from verilex_runtime.tools.documents import (
    DOCUMENT_CREATE_PARAMETERS,
    register_document_tools,
)
from verilex_runtime.tools.registry import (
    ApprovalBehavior,
    ToolCategory,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolScope,
    ToolSource,
)


async def _execute(context: ToolContext, args: dict) -> ToolResult:
    del context, args
    return ToolResult(ok=True, output="ok")


def _additive_definition(**overrides) -> ToolDefinition:
    values = dict(
        name="document_create",
        description="Create a fixed-layout Word document in the workspace.",
        parameters=DOCUMENT_CREATE_PARAMETERS,
        risk_level=RiskLevel.HIGH,
        execute=_execute,
        source=ToolSource.BUILTIN,
        category=ToolCategory.DOCUMENTS,
        scopes=frozenset({ToolScope.WRITE}),
        requires_workspace=True,
        approval_behavior=ApprovalBehavior.ADDITIVE_ARTIFACT,
    )
    values.update(overrides)
    return ToolDefinition(**values)


def test_document_create_definition_is_exact():
    document_service = AsyncMock(spec=V1DocumentService)
    registry = ToolRegistry()
    register_document_tools(registry, document_service)

    definition = registry.get("document_create")
    assert [item.name for item in registry.definitions()] == ["document_create"]
    assert definition is not None
    assert definition.category is ToolCategory.DOCUMENTS
    assert definition.scopes == frozenset({ToolScope.WRITE})
    assert definition.risk_level is RiskLevel.HIGH
    assert definition.approval_behavior is ApprovalBehavior.ADDITIVE_ARTIFACT
    assert definition.requires_workspace is True
    assert definition.parameters == DOCUMENT_CREATE_PARAMETERS
    assert definition.parameters["additionalProperties"] is False
    assert set(definition.parameters["required"]) == {
        "output_name",
        "title",
        "content_markdown",
    }
    assert registry.validate(
        "document_create",
        {
            "output_name": "意见.docx",
            "title": "意见",
            "content_markdown": "正文",
            "extra": "value",
        },
    ) is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "document_create_x"},
        {"source": ToolSource.SKILL},
        {"category": ToolCategory.FILES},
        {"scopes": frozenset({ToolScope.READ})},
        {"scopes": frozenset({ToolScope.EXECUTE})},
        {"scopes": frozenset({ToolScope.NETWORK})},
        {"scopes": frozenset({ToolScope.EXTERNAL_SERVICE})},
        {"risk_level": RiskLevel.LOW},
        {"requires_workspace": False},
        {"authorization_owner": "thread_mcp_selection"},
        {
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        },
    ],
)
def test_registry_rejects_forged_additive_artifact_tools(overrides):
    registry = ToolRegistry()

    with pytest.raises(ValueError, match="additive_artifact"):
        registry.register(_additive_definition(**overrides))


@pytest.fixture
def engine_env(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads_repo = ThreadRepository(database)
    turns_repo = TurnRepository(database)
    messages_repo = MessageRepository(database)
    events_repo = EventRepository(database)
    providers_repo = ProviderRepository(database)
    workspaces_repo = WorkspaceRepository(database)
    tool_calls_repo = ToolCallRepository(database)
    approvals_repo = ApprovalRepository(database)

    providers_repo.create(
        ProviderRecord(
            id="provider-1",
            name="Compat",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-1",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-13T00:00:00Z",
            updated_at="2026-08-13T00:00:00Z",
        )
    )

    ids = iter(f"id-{n}" for n in range(1, 1000))
    threads = ThreadService(
        threads_repo,
        providers_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    turns = TurnService(
        threads_repo,
        turns_repo,
        messages_repo,
        events_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )
    workspaces = WorkspaceService(
        workspaces_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-13T00:00:00Z",
    )

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    workspace = workspaces.create(str(ws_dir), "Workspace", allow_write=True)
    thread = threads.create(
        "Case", "model-1", provider_id="provider-1", workspace_id=workspace.id
    )

    return {
        "threads": threads,
        "turns": turns,
        "workspaces": workspaces,
        "tool_calls": tool_calls_repo,
        "approvals": approvals_repo,
        "thread": thread,
        "workspace": workspace,
        "ws_dir": ws_dir,
        "ids": ids,
    }


def _make_engine(engine_env, registry):
    def factory(provider_id: str, model: str):
        raise AssertionError("document executor must not call the provider")

    return AgentEngine(
        engine_env["threads"],
        engine_env["turns"],
        factory,
        limits=TurnLimits(),
        tool_registry=registry,
        workspaces=engine_env["workspaces"],
        tool_calls=engine_env["tool_calls"],
        approvals=engine_env["approvals"],
        id_factory=lambda: next(engine_env["ids"]),
        now=lambda: "2026-08-13T00:00:00Z",
    )


_ARGUMENTS = {
    "output_name": "意见.docx",
    "title": "合同审查意见",
    "content_markdown": "# 一、结论\n\n正文",
}


def _tool_context(engine_env, registry) -> tuple[ToolContext, ToolDefinition]:
    definition = registry.get("document_create")
    assert definition is not None
    context = ToolContext(
        root_path=engine_env["ws_dir"],
        allow_read=True,
        allow_write=True,
        thread_id=engine_env["thread"].id,
        turn_id="turn-1",
        tool_call_id="tool-1",
        workspace_id=engine_env["workspace"].id,
    )
    return context, definition


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "message"),
    [
        (
            "document_workspace_required",
            "请先为这个任务选择工作区，再生成 Word 文件",
        ),
        ("document_create_invalid", "生成内容或文件名不符合首发支持范围"),
        ("document_validation_failed", "新 Word 自检没有通过，因此没有交付文件"),
        ("document_publish_conflict", "新文件发布时发生同名竞争，原文件没有被覆盖"),
    ],
)
async def test_document_create_maps_service_errors_to_stable_codes(
    engine_env, code, message
):
    document_service = AsyncMock(spec=V1DocumentService)
    document_service.create.side_effect = DocumentDomainError(code)
    registry = ToolRegistry()
    register_document_tools(registry, document_service)
    engine = _make_engine(engine_env, registry)
    context, definition = _tool_context(engine_env, registry)

    result = await engine._invoke(definition, context, dict(_ARGUMENTS))

    assert result.ok is False
    assert result.output.startswith(f"{code}: ")
    assert message in result.output
    assert str(engine_env["ws_dir"]) not in result.output


@pytest.mark.asyncio
async def test_document_create_unknown_service_error_is_not_swallowed(engine_env):
    document_service = AsyncMock(spec=V1DocumentService)
    document_service.create.side_effect = DocumentDomainError(
        "document_totally_unexpected"
    )
    registry = ToolRegistry()
    register_document_tools(registry, document_service)
    engine = _make_engine(engine_env, registry)
    context, definition = _tool_context(engine_env, registry)

    result = await engine._invoke(definition, context, dict(_ARGUMENTS))

    assert result.ok is False
    assert result.output == "internal_error: tool failed"


@pytest.mark.asyncio
async def test_document_create_success_output_is_exact_json(engine_env):
    document_service = AsyncMock(spec=V1DocumentService)
    document_service.create.return_value = ArtifactRecord(
        id="artifact-1",
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id="tool-1",
        blob_sha256="a" * 64,
        display_name="意见.docx",
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        size_bytes=2048,
        operation="document_create",
        source_kind=ArtifactSourceKind.NEW,
        parent_artifact_id=None,
        source_attachment_id=None,
        source_workspace_id=None,
        source_relative_path=None,
        published_workspace_id="workspace-1",
        published_relative_path="意见.docx",
        structure_status=ValidationStatus.PASSED,
        render_status=ValidationStatus.NOT_REQUIRED,
        vision_status=VisionStatus.NOT_RUN,
        validation_report_json="{}",
        created_at="2026-09-01T00:00:00Z",
    )
    registry = ToolRegistry()
    register_document_tools(registry, document_service)
    engine = _make_engine(engine_env, registry)
    context, definition = _tool_context(engine_env, registry)

    result = await engine._invoke(definition, context, dict(_ARGUMENTS))

    assert result.ok is True
    payload = json.loads(result.output)
    assert payload == {
        "artifact_id": "artifact-1",
        "display_name": "意见.docx",
        "published_relative_path": "意见.docx",
    }
    request = document_service.create.await_args.args[1]
    assert isinstance(request, V1CreateRequest)
    assert request.output_name == "意见.docx"
    assert request.title == "合同审查意见"
    assert request.content_markdown == "# 一、结论\n\n正文"
