"""End-to-end document_create Turn: workspace gate, real publish, restart."""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from agent_fakes import FakeChatStream, text_delta, tool_call_delta
from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.agent.limits import TurnLimits
from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.publisher import ArtifactPublisher
from verilex_runtime.documents.v1_service import V1DocumentService
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.storage.artifact_repository import ArtifactRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    AuthMode,
    MessageRole,
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
from verilex_runtime.storage.workspace_repository import WorkspaceRepository
from verilex_runtime.tools import ToolRegistry, register_file_tools
from verilex_runtime.tools.documents import register_document_tools

NOW = "2026-09-01T00:00:00Z"

_DOCUMENT_ARGUMENTS = {
    "output_name": "意见.docx",
    "title": "意见",
    "content_markdown": "# 一、结论\n\n正文段落",
}


class _RecordingFactory:
    def __init__(self, rounds):
        self.rounds = rounds
        self.streams = []

    def __call__(self, provider_id: str, model: str) -> FakeChatStream:
        index = min(len(self.streams), len(self.rounds) - 1)
        stream = FakeChatStream(self.rounds[index])
        self.streams.append(stream)
        return stream


def _build_env(tmp_path, *, with_workspace: bool):
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
            created_at=NOW,
            updated_at=NOW,
        )
    )

    ids = iter(f"id-{n}" for n in range(1, 1000))
    threads = ThreadService(
        threads_repo,
        providers_repo,
        id_factory=lambda: next(ids),
        now=lambda: NOW,
    )
    turns = TurnService(
        threads_repo,
        turns_repo,
        messages_repo,
        events_repo,
        id_factory=lambda: next(ids),
        now=lambda: NOW,
    )
    workspaces = WorkspaceService(
        workspaces_repo,
        id_factory=lambda: next(ids),
        now=lambda: NOW,
    )

    registry = ToolRegistry()
    register_file_tools(registry)
    artifact_repository = ArtifactRepository(
        database, cursor_secret=b"integration-test-secret"
    )
    artifact_store = ArtifactBlobStore(tmp_path / "artifacts")
    publisher = ArtifactPublisher(
        artifact_repository,
        artifact_store,
        now=lambda: NOW,
    )
    document_service = V1DocumentService(
        artifact_repository,
        publisher,
        temporary_root=tmp_path / "document-v1-temp",
        now=lambda: NOW,
    )
    register_document_tools(registry, document_service)

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    workspace = workspaces.create(str(ws_dir), "Workspace", allow_write=True)
    thread = threads.create(
        "Case",
        "model-1",
        provider_id="provider-1",
        workspace_id=workspace.id if with_workspace else None,
    )

    return {
        "database": database,
        "threads": threads,
        "turns": turns,
        "workspaces": workspaces,
        "registry": registry,
        "tool_calls": tool_calls_repo,
        "approvals": approvals_repo,
        "artifact_repository": artifact_repository,
        "artifact_store": artifact_store,
        "document_service": document_service,
        "thread": thread,
        "workspace": workspace,
        "ws_dir": ws_dir,
        "ids": ids,
    }


def _make_engine(env, factory):
    def guard(provider_id: str, model: str):
        raise AssertionError("provider must not be called")

    return AgentEngine(
        env["threads"],
        env["turns"],
        factory,
        limits=TurnLimits(),
        tool_registry=env["registry"],
        workspaces=env["workspaces"],
        tool_calls=env["tool_calls"],
        approvals=env["approvals"],
        id_factory=lambda: next(env["ids"]),
        now=lambda: NOW,
    )


async def _emit_into(records):
    async def emit(record):
        records.append(record)

    return emit


@pytest.mark.asyncio
async def test_word_request_without_workspace_hides_tool_and_instructs_reply(
    tmp_path,
):
    """无工作区：document_create 对模型隐藏，指令说明工具需要工作区。

    是否「要求生成 Word」由模型理解语义；harness 只保证工具不可见、
    指令说明真实能力缺口、没有任何文件或审批产生。
    """
    env = _build_env(tmp_path, with_workspace=False)
    factory = _RecordingFactory(
        [[text_delta("请先为这个任务选择工作区，再生成 Word 文件")]]
    )
    engine = _make_engine(env, factory)
    records = []
    turn = engine.start(env["thread"].id, "出一份word审查合同")
    result = await asyncio.wait_for(
        engine.run(turn.id, await _emit_into(records)), timeout=20
    )

    assert result.status is TurnStatus.COMPLETED
    # The model saw no document_create tool and no delivery lock armed.
    offered_names = [
        tool["function"]["name"]
        for stream in factory.streams
        for tool in getattr(stream, "received_tools", [])
    ]
    assert "document_create" not in offered_names
    system_text = "\n".join(
        str(message.content)
        for stream in factory.streams
        for message in stream.received_messages
        if getattr(message, "role", None) == "system"
    )
    assert "若需要工作区但尚未选择" in system_text
    assert env["tool_calls"].list(turn.id) == []
    assert env["approvals"].list_by_turn(turn.id) == []
    assert not any((env["ws_dir"] / name).exists() for name in env["ws_dir"].iterdir())


def test_ordinary_chat_without_workspace_runs_normally(tmp_path):
    """无工作区＋无明确 Word 请求：不触发旁路，正常调用模型。"""
    env = _build_env(tmp_path, with_workspace=False)
    factory = _RecordingFactory([[text_delta("这份合同看起来是虚构样本。")]])
    engine = _make_engine(env, factory)
    records = []
    turn = engine.start(env["thread"].id, "帮我看看这份合同有什么问题")
    result = asyncio.run(engine.run(turn.id, _SyncEmit(records)))

    assert result.status is TurnStatus.COMPLETED
    assert len(factory.streams) == 1  # provider was called normally
    assert env["tool_calls"].list(turn.id) == []
    assert env["approvals"].list_by_turn(turn.id) == []
    messages = env["turns"].list_messages(env["thread"].id)
    assert not any(
        "请先为这个任务选择工作区" in json.dumps(view.content, ensure_ascii=False)
        for view in messages
    )


class _SyncEmit:
    def __init__(self, records):
        self._records = records

    async def __call__(self, record):
        self._records.append(record)


@pytest.mark.asyncio
async def test_document_create_turn_publishes_once_and_survives_restart(tmp_path):
    env = _build_env(tmp_path, with_workspace=True)
    source = env["ws_dir"] / "合同.docx"
    source.write_bytes(b"original contract bytes")
    original_sha = hashlib.sha256(source.read_bytes()).hexdigest()

    factory = _RecordingFactory(
        [
            [
                tool_call_delta(
                    "call-1",
                    "document_create",
                    json.dumps(_DOCUMENT_ARGUMENTS, ensure_ascii=False),
                )
            ],
            [text_delta("已生成 Word 文件。")],
            [text_delta("没有更多操作。")],
        ]
    )
    engine = _make_engine(env, factory)
    records = []
    turn = engine.start(env["thread"].id, "请整理成一份 Word 交付")
    result = await asyncio.wait_for(
        engine.run(turn.id, await _emit_into(records)), timeout=30
    )

    assert result.status is TurnStatus.COMPLETED
    output = env["ws_dir"] / "意见.docx"
    assert output.is_file() and output.stat().st_size > 0
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_sha
    artifacts = env["artifact_repository"].list_page(
        env["thread"].id, limit=10, cursor=None
    ).items
    assert [item.operation for item in artifacts] == ["document_create"]
    assert artifacts[0].published_relative_path == "意见.docx"
    tool_messages = [
        message
        for message in factory.streams[1].received_messages
        if getattr(message, "role", None) == "tool"
    ]
    assert any(
        json.loads(str(message.content))["display_name"] == "意见.docx"
        for message in tool_messages
        if str(message.content).startswith("{")
    )

    # Simulated restart: reconcile over the committed journal and run one more
    # plain Turn — the artifact must not duplicate and the tool must not replay.
    restarted_publisher = ArtifactPublisher(
        env["artifact_repository"],
        ArtifactBlobStore(tmp_path / "artifacts"),
        now=lambda: NOW,
    )
    report = restarted_publisher.reconcile_all()
    assert report.completed_batches == 0
    assert report.conflicted_batches == 0
    assert report.cleaned_batches == 0

    followup = _make_engine(
        env, _RecordingFactory([[text_delta("好的。")]])
    )
    followup_turn = followup.start(env["thread"].id, "谢谢")
    followup_result = await asyncio.wait_for(
        followup.run(followup_turn.id, await _emit_into([])), timeout=20
    )
    assert followup_result.status is TurnStatus.COMPLETED
    assert [
        item.operation
        for item in env["artifact_repository"].list_page(
            env["thread"].id, limit=10, cursor=None
        ).items
    ] == ["document_create"]
    assert sorted(path.name for path in env["ws_dir"].iterdir()) == [
        "合同.docx",
        "意见.docx",
    ]
