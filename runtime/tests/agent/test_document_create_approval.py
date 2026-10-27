"""Engine-level tests: continued authorization and additive document delivery."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from agent_fakes import FakeChatStream, text_delta, tool_call_delta
from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.agent.limits import TurnLimits
from verilex_runtime.documents.v1_service import V1DocumentService
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    ApprovalStatus,
    AuthMode,
    ProviderKind,
    ProviderRecord,
    TurnStatus,
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
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.workspace_repository import WorkspaceRepository
from verilex_runtime.tools import (
    ToolRegistry,
    register_file_tools,
)
from verilex_runtime.tools.documents import register_document_tools
from verilex_runtime.tools.terminal import register_terminal_tools

_DOCUMENT_ARGUMENTS = {
    "output_name": "意见.docx",
    "title": "意见",
    "content_markdown": "# 一、结论\n\n正文",
}


@pytest.fixture
def env(tmp_path):
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
    audit_repo = AuditRepository(database)
    audit_recorder = AuditRecorder(audit_repo, now=lambda: "2026-08-13T00:00:00Z")

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

    registry = ToolRegistry()
    register_file_tools(registry)
    register_terminal_tools(registry)
    document_service = AsyncMock(spec=V1DocumentService)
    register_document_tools(registry, document_service)

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
        "registry": registry,
        "document_service": document_service,
        "tool_calls": tool_calls_repo,
        "approvals": approvals_repo,
        "audit": audit_repo,
        "audit_recorder": audit_recorder,
        "thread": thread,
        "workspace": workspace,
        "ws_dir": ws_dir,
        "ids": ids,
    }


class _RecordingFactory:
    """Serve one scripted round per model step and record every stream."""

    def __init__(self, rounds):
        self.rounds = rounds
        self.streams = []

    def __call__(self, provider_id: str, model: str) -> FakeChatStream:
        index = min(len(self.streams), len(self.rounds) - 1)
        stream = FakeChatStream(self.rounds[index])
        self.streams.append(stream)
        return stream


def make_engine(env, factory, *, limits=None):
    return AgentEngine(
        env["threads"],
        env["turns"],
        factory,
        limits=limits or TurnLimits(),
        tool_registry=env["registry"],
        workspaces=env["workspaces"],
        tool_calls=env["tool_calls"],
        approvals=env["approvals"],
        audit=env["audit_recorder"],
        id_factory=lambda: next(env["ids"]),
        now=lambda: "2026-08-13T00:00:00Z",
    )


async def emit_into(records):
    async def emit(record):
        records.append(record)

    return emit


def payload_of(record):
    return json.loads(record.payload_json)


@pytest.mark.asyncio
async def test_continued_document_task_needs_no_current_message_quote(env):
    # Authorization from the prior request continues with the current user request.
    prior_turn, _ = env["turns"].create_user_turn(
        env["thread"].id, "请整理成一份 Word"
    )
    env["turns"].transition(prior_turn.id, TurnStatus.FAILED)
    tool_arguments = json.dumps(_DOCUMENT_ARGUMENTS, ensure_ascii=False)
    factory = _RecordingFactory(
        [
            [tool_call_delta("call-1", "document_create", tool_arguments)],
            [text_delta("已生成。")],
        ]
    )
    engine = make_engine(env, factory)
    records = []
    turn = engine.start(env["thread"].id, "继续")
    result = await asyncio.wait_for(
        engine.run(turn.id, await emit_into(records)), timeout=20
    )

    assert result.status is TurnStatus.COMPLETED
    env["document_service"].create.assert_awaited_once()
    assert env["tool_calls"].list(turn.id)[0].name == "document_create"


@pytest.mark.asyncio
async def test_current_message_quote_authorizes_document_create(env):
    record = env["document_service"].create.return_value
    tool_arguments = json.dumps(_DOCUMENT_ARGUMENTS, ensure_ascii=False)
    factory = _RecordingFactory(
        [
            [tool_call_delta("call-1", "document_create", tool_arguments)],
            [text_delta("已生成。")],
        ]
    )
    engine = make_engine(env, factory)
    records = []
    turn = engine.start(env["thread"].id, "请整理成一份 Word 交付")
    result = await asyncio.wait_for(
        engine.run(turn.id, await emit_into(records)), timeout=20
    )

    assert result.status is TurnStatus.COMPLETED
    env["document_service"].create.assert_awaited_once()
    tool_calls = env["tool_calls"].list(turn.id)
    assert [call.name for call in tool_calls] == ["document_create"]
    del record


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ask", "help", "full"])
async def test_document_create_is_auto_allowed_in_every_mode(env, mode):
    tool_arguments = json.dumps(_DOCUMENT_ARGUMENTS, ensure_ascii=False)
    engine = make_engine(
        env,
        _RecordingFactory(
            [
                [tool_call_delta("call-1", "document_create", tool_arguments)],
                [text_delta("已生成。")],
            ]
        ),
    )
    records = []
    turn = engine.start(
        env["thread"].id, "请整理成一份 Word 交付", approval_mode=mode
    )
    result = await asyncio.wait_for(
        engine.run(turn.id, await emit_into(records)), timeout=20
    )

    assert result.status is TurnStatus.COMPLETED
    approvals = env["approvals"].list_by_turn(turn.id)
    assert len(approvals) == 1
    assert approvals[0].status is ApprovalStatus.ALLOWED
    assert approvals[0].decision_reason == (
        "explicit_document_instruction_additive_artifact"
    )
    assert not any(record.type == "approval_requested" for record in records)
    resolved = [record for record in records if record.type == "approval_resolved"]
    assert len(resolved) == 1
    assert json.loads(resolved[0].payload_json)["decision"] == "allowed"
    env["document_service"].create.assert_awaited_once()


@pytest.mark.asyncio
async def test_additive_behavior_leaves_normal_write_approval_untouched(env):
    """ask 仍然等待人工批准 write_file；ADDITIVE_ARTIFACT 只作用于自己。"""
    engine = make_engine(
        env,
        _RecordingFactory(
            [
                [
                    tool_call_delta(
                        "call-1",
                        "write_file",
                        '{"path": "note.txt", "content": "内容"}',
                    )
                ],
                [text_delta("完成")],
            ]
        ),
    )
    records = []
    turn = engine.start(env["thread"].id, "写个文件", approval_mode="ask")
    task = asyncio.create_task(engine.run(turn.id, await emit_into(records)))
    approval = await _wait_for_event(records, "approval_requested")
    engine.resolve(turn.id, approval["toolCallId"], "allowed")
    result = await asyncio.wait_for(task, timeout=20)

    assert result.status is TurnStatus.COMPLETED
    approvals = env["approvals"].list_by_turn(turn.id)
    assert len(approvals) == 1
    assert approvals[0].status is ApprovalStatus.ALLOWED
    assert approvals[0].decision_reason is None


@pytest.mark.asyncio
async def test_word_delivery_discipline_instruction_follows_document_registration(
    env,
):
    """文档工具只描述能力，不强制交付步骤。"""
    engine = make_engine(env, _RecordingFactory([[text_delta("好的。")]]))
    records = []
    turn = engine.start(env["thread"].id, "你好")
    result = await asyncio.wait_for(
        engine.run(turn.id, await emit_into(records)), timeout=20
    )

    assert result.status is TurnStatus.COMPLETED
    system_messages = [
        message
        for message in engine._build_context(env["thread"]).messages
        if getattr(message, "role", None) == "system"
    ]
    instruction = "\n".join(str(message.content) for message in system_messages)
    assert "用户请求和已有授权" in instruction
    assert "intent_quote" not in instruction
    assert "不要在工作区创建中间文件" not in instruction
    assert "不要用命令做格式转换" not in instruction

    # Without the document tool registered (non-darwin shape), the constraint
    # does not nag unrelated conversations.
    env["registry"] = ToolRegistry()
    register_file_tools(env["registry"])
    plain_engine = make_engine(env, _RecordingFactory([[text_delta("好。")]]))
    plain_turn = plain_engine.start(env["thread"].id, "再说一句")
    plain_result = await asyncio.wait_for(
        plain_engine.run(plain_turn.id, await emit_into([])), timeout=20
    )
    assert plain_result.status is TurnStatus.COMPLETED
    plain_systems = [
        message
        for message in plain_engine._build_context(env["thread"]).messages
        if getattr(message, "role", None) == "system"
    ]
    assert not any(
        "不要在工作区创建中间文件" in str(message.content)
        for message in plain_systems
    )


async def _wait_for_event(records, event_type):
    for _ in range(5000):
        for record in records:
            if record.type == event_type:
                return payload_of(record)
        await asyncio.sleep(0.001)
    raise AssertionError(f"event {event_type} was not emitted")


_MARKER_NAME = "绝密文件名.docx"
_MARKER_TITLE = "绝密标题七字"
_MARKER_CONTENT = "绝密正文内容"


@pytest.mark.asyncio
async def test_document_arguments_never_leak_into_events_audit_or_logs(
    env, caplog
):
    from verilex_runtime.storage.models import (
        ArtifactRecord,
        ArtifactSourceKind,
        ValidationStatus,
        VisionStatus,
    )

    env["document_service"].create.return_value = ArtifactRecord(
        id="artifact-secret",
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id="tool-1",
        blob_sha256="b" * 64,
        display_name=_MARKER_NAME,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=4096,
        operation="document_create",
        source_kind=ArtifactSourceKind.NEW,
        parent_artifact_id=None,
        source_attachment_id=None,
        source_workspace_id=None,
        source_relative_path=None,
        published_workspace_id="workspace-1",
        published_relative_path=_MARKER_NAME,
        structure_status=ValidationStatus.PASSED,
        render_status=ValidationStatus.NOT_REQUIRED,
        vision_status=VisionStatus.NOT_RUN,
        validation_report_json="{}",
        created_at="2026-09-01T00:00:00Z",
    )
    arguments = {
        "output_name": _MARKER_NAME,
        "title": _MARKER_TITLE,
        "content_markdown": f"# {_MARKER_TITLE}\n\n{_MARKER_CONTENT}",
    }
    engine = make_engine(
        env,
        factory := _RecordingFactory(
            [
                [
                    tool_call_delta(
                        "call-1",
                        "document_create",
                        json.dumps(arguments, ensure_ascii=False),
                    )
                ],
                [text_delta("已生成。")],
            ]
        ),
    )
    records = []
    with caplog.at_level("INFO"):
        turn = engine.start(env["thread"].id, "请整理成一份 Word 交付")
        result = await asyncio.wait_for(
            engine.run(turn.id, await emit_into(records)), timeout=20
        )

    assert result.status is TurnStatus.COMPLETED
    secrets = (
        _MARKER_NAME,
        _MARKER_TITLE,
        _MARKER_CONTENT,
        str(env["ws_dir"]),
    )

    event_blob = json.dumps(
        [payload_of(record) for record in records], ensure_ascii=False
    )
    for secret in secrets:
        assert secret not in event_blob

    audit_blob = json.dumps(
        [
            {
                "action": entry.action,
                "subject": entry.subject,
                "outcome": entry.outcome,
                "details": entry.details,
            }
            for entry in env["audit"].list_by_thread(env["thread"].id)
        ],
        ensure_ascii=False,
    )
    for secret in secrets:
        assert secret not in audit_blob

    for secret in secrets:
        assert secret not in caplog.text

    # The persisted tool call keeps full arguments for replay/history, and the
    # model still sees the artifact name in its own tool result.
    tool_call = env["tool_calls"].list(turn.id)[0]
    assert _MARKER_CONTENT in tool_call.arguments_json
    tool_messages = [
        message
        for message in factory.streams[-1].received_messages
        if getattr(message, "role", None) == "tool"
    ]
    assert any(_MARKER_NAME in str(m.content) for m in tool_messages)


# Word creation does not restrict unrelated tools; normal authorization applies.

@pytest.mark.asyncio
async def test_turn_that_attempted_document_create_allows_authorized_textutil(env):
    tool_arguments = json.dumps(_DOCUMENT_ARGUMENTS, ensure_ascii=False)
    factory = _RecordingFactory(
        [
            [tool_call_delta("call-doc", "document_create", tool_arguments)],
            [
                tool_call_delta(
                    "call-1",
                    "run_command",
                    json.dumps(
                        {
                            "command": "textutil -convert docx -output 报告.docx 报告.html"
                        }
                    ),
                )
            ],
            [text_delta("改用 document_create。")],
        ]
    )
    engine = make_engine(env, factory)
    records = []
    turn = engine.start(env["thread"].id, "请整理成一份 Word 交付", approval_mode="full")
    result = await asyncio.wait_for(
        engine.run(turn.id, await emit_into(records)), timeout=20
    )

    assert result.status is TurnStatus.COMPLETED
    tool_calls = env["tool_calls"].list(turn.id)
    assert {call.name for call in tool_calls} == {"document_create", "run_command"}
    approvals = env["approvals"].list_by_turn(turn.id)
    assert any(a.status is ApprovalStatus.ALLOWED for a in approvals)


@pytest.mark.asyncio
async def test_textutil_before_any_document_attempt_follows_normal_approval(env):
    """模型还没尝试生成 Word 时，普通转换不触发锁，维持原有审批行为。"""
    factory = _RecordingFactory(
        [
            [
                tool_call_delta(
                    "call-1",
                    "run_command",
                    json.dumps({"command": "textutil -convert txt -output a.txt a.doc"}),
                )
            ],
            [text_delta("已转换。")],
        ]
    )
    engine = make_engine(env, factory)
    records = []
    turn = engine.start(
        env["thread"].id,
        "把这个文件转换一下格式",
        approval_mode="full",
    )
    result = await asyncio.wait_for(
        engine.run(turn.id, await emit_into(records)), timeout=20
    )

    assert result.status is TurnStatus.COMPLETED
    approvals = env["approvals"].list_by_turn(turn.id)
    assert len(approvals) == 1
    assert approvals[0].status is ApprovalStatus.ALLOWED


@pytest.mark.asyncio
async def test_turn_that_attempted_document_create_allows_authorized_html_draft(env):
    tool_arguments = json.dumps(_DOCUMENT_ARGUMENTS, ensure_ascii=False)
    factory = _RecordingFactory(
        [
            [tool_call_delta("call-doc", "document_create", tool_arguments)],
            [
                tool_call_delta(
                    "call-1",
                    "write_file",
                    json.dumps(
                        {"path": "审查报告源稿.html", "content": "<table>…</table>"}
                    ),
                )
            ],
            [text_delta("改用 document_create。")],
        ]
    )
    engine = make_engine(env, factory)
    records = []
    turn = engine.start(env["thread"].id, "请整理成一份 Word 交付", approval_mode="full")
    result = await asyncio.wait_for(
        engine.run(turn.id, await emit_into(records)), timeout=20
    )

    assert result.status is TurnStatus.COMPLETED
    tool_calls = env["tool_calls"].list(turn.id)
    assert {call.name for call in tool_calls} == {"document_create", "write_file"}
    approvals = env["approvals"].list_by_turn(turn.id)
    assert any(a.status is ApprovalStatus.ALLOWED for a in approvals)


@pytest.mark.asyncio
async def test_unrelated_write_after_document_attempt_still_uses_approval(env):
    """txt 笔记等工作区写入不受交付锁影响，仍走常规审批。"""
    tool_arguments = json.dumps(_DOCUMENT_ARGUMENTS, ensure_ascii=False)
    factory = _RecordingFactory(
        [
            [tool_call_delta("call-doc", "document_create", tool_arguments)],
            [
                tool_call_delta(
                    "call-1",
                    "write_file",
                    json.dumps({"path": "笔记.txt", "content": "要点"}),
                )
            ],
            [text_delta("已记录。")],
        ]
    )
    engine = make_engine(env, factory)
    records = []
    turn = engine.start(
        env["thread"].id,
        "请整理成一份 Word 交付，顺便记个笔记",
        approval_mode="full",
    )
    result = await asyncio.wait_for(
        engine.run(turn.id, await emit_into(records)), timeout=20
    )

    assert result.status is TurnStatus.COMPLETED
    tool_calls = env["tool_calls"].list(turn.id)
    names = [call.name for call in tool_calls]
    assert "write_file" in names  # the txt note went through approval
    approvals = env["approvals"].list_by_turn(turn.id)
    write_ids = {call.id for call in tool_calls if call.name == "write_file"}
    assert any(
        entry.tool_call_id in write_ids
        and entry.status is ApprovalStatus.ALLOWED
        for entry in approvals
    )
