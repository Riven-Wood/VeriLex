from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import stat
import threading
import time

import pytest

from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.errors import ArtifactDomainError
from verilex_runtime.artifacts.publisher import ArtifactPublisher
from verilex_runtime.attachments.errors import attachment_error
from verilex_runtime.documents.errors import DocumentDomainError
from verilex_runtime.documents.v1_creator import create_v1_docx
from verilex_runtime.documents.v1_service import V1CreateRequest, V1DocumentService
from verilex_runtime.tools.registry import ToolContext

from tests.artifacts._support import NOW, make_database, repository


def _add_tool_calls(database, *tool_ids: str) -> None:
    with database.connect() as connection:
        for tool_id in tool_ids:
            connection.execute(
                """INSERT INTO tool_calls
                   (id,turn_id,provider_call_id,name,arguments_json,risk_level,status)
                   VALUES (?,'turn-1',?,'document_create','{}','high','running')""",
                (tool_id, f"provider-{tool_id}"),
            )


@pytest.fixture
def service_env(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = make_database(tmp_path, workspace_root=workspace)
    _add_tool_calls(database, "tool-2", "tool-3", "tool-4")
    artifact_repository = repository(database)
    publisher = ArtifactPublisher(
        artifact_repository,
        ArtifactBlobStore(tmp_path / "artifacts"),
        now=lambda: NOW,
        id_factory=iter(f"journal-{number}" for number in range(100)).__next__,
    )
    temporary_root = tmp_path / "state" / "document-v1-temp"
    service = V1DocumentService(
        artifact_repository,
        publisher,
        temporary_root=temporary_root,
        now=lambda: NOW,
        id_factory=iter(f"artifact-{number}" for number in range(100)).__next__,
    )
    context = ToolContext(
        root_path=workspace,
        allow_read=True,
        allow_write=True,
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id="tool-1",
        workspace_id="workspace-1",
    )
    return database, workspace, temporary_root, service, context, publisher


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context_update",
    [
        {"root_path": None},
        {"allow_write": False},
        {"thread_id": None},
        {"turn_id": None},
        {"tool_call_id": None},
        {"workspace_id": None},
    ],
)
async def test_create_requires_complete_writable_workspace_before_staging(
    service_env, context_update
):
    database, workspace, temporary_root, service, context, _publisher = service_env

    with pytest.raises(DocumentDomainError, match="document_workspace_required"):
        await service.create(
            replace(context, **context_update),
            V1CreateRequest("报告.docx", "报告", "正文"),
        )

    assert not temporary_root.exists()
    assert list(workspace.iterdir()) == []
    assert repository(database).list_page(
        "thread-1", limit=10, cursor=None
    ).items == ()


@pytest.mark.asyncio
async def test_create_rejects_symlink_temporary_root_without_touching_target(
    service_env, tmp_path
):
    database, workspace, temporary_root, service, context, _publisher = service_env
    outside = tmp_path / "outside-temp"
    outside.mkdir(mode=0o755)
    temporary_root.parent.mkdir(parents=True)
    temporary_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="document_temp_root_invalid"):
        await service.create(
            context,
            V1CreateRequest("报告.docx", "报告", "正文"),
        )

    assert stat.S_IMODE(outside.stat().st_mode) == 0o755
    assert list(outside.iterdir()) == []
    assert list(workspace.iterdir()) == []
    assert repository(database).list_page(
        "thread-1", limit=10, cursor=None
    ).items == ()


@pytest.mark.asyncio
async def test_create_rejects_symlink_in_temporary_root_parent_chain(
    service_env, tmp_path
):
    database, workspace, temporary_root, service, context, _publisher = service_env
    outside_state = tmp_path / "outside-state"
    outside_state.mkdir(mode=0o755)
    outside_root = outside_state / temporary_root.name
    outside_root.mkdir(mode=0o755)
    stale = outside_root / ".document-v1-stale"
    stale.mkdir()
    old = time.time() - 25 * 60 * 60
    os.utime(stale, (old, old))
    temporary_root.parent.symlink_to(outside_state, target_is_directory=True)

    with pytest.raises(OSError, match="document_temp_root_invalid"):
        await service.create(
            context,
            V1CreateRequest("报告.docx", "报告", "正文"),
        )

    assert stat.S_IMODE(outside_root.stat().st_mode) == 0o755
    assert stale.is_dir()
    assert list(workspace.iterdir()) == []
    assert repository(database).list_page(
        "thread-1", limit=10, cursor=None
    ).items == ()


@pytest.mark.asyncio
async def test_create_keeps_using_pinned_temp_root_if_path_is_replaced(
    service_env, tmp_path, monkeypatch
):
    _database, workspace, temporary_root, service, context, _publisher = service_env
    outside = tmp_path / "outside-race"
    outside.mkdir(mode=0o755)
    original_root = tmp_path / "original-temp-root"
    leaked_during_create: list[bool] = []
    original_prepare = service._prepare_temporary_root

    def replace_root_after_prepare():
        result = original_prepare()
        temporary_root.rename(original_root)
        temporary_root.symlink_to(outside, target_is_directory=True)
        return result

    def recording_creator(target, *args, **kwargs):
        result = create_v1_docx(target, *args, **kwargs)
        leaked_during_create.append(bool(list(outside.iterdir())))
        return result

    monkeypatch.setattr(service, "_prepare_temporary_root", replace_root_after_prepare)
    monkeypatch.setattr(service, "_creator", recording_creator)

    record = await service.create(
        context,
        V1CreateRequest("报告.docx", "报告", "正文"),
    )

    assert record.display_name == "报告.docx"
    assert [path.name for path in workspace.iterdir()] == ["报告.docx"]
    assert list(outside.iterdir()) == []
    assert leaked_during_create == [False]


@pytest.mark.asyncio
async def test_creator_cannot_follow_replaced_temporary_child(
    service_env, tmp_path, monkeypatch
):
    _database, workspace, _temporary_root, service, context, _publisher = service_env
    outside = tmp_path / "outside-child-race"
    outside.mkdir(mode=0o755)
    moved_child = tmp_path / "moved-private-child"
    leaked_during_create: list[bool] = []
    original_create_temporary = service._create_temporary_directory

    def replace_child_after_create(root_descriptor):
        name, descriptor = original_create_temporary(root_descriptor)
        path = service._descriptor_path(descriptor)
        path.rename(moved_child)
        path.symlink_to(outside, target_is_directory=True)
        return name, descriptor

    def recording_creator(target, *args, **kwargs):
        result = create_v1_docx(target, *args, **kwargs)
        leaked_during_create.append((outside / "output.docx").exists())
        return result

    monkeypatch.setattr(
        service,
        "_create_temporary_directory",
        replace_child_after_create,
    )
    monkeypatch.setattr(service, "_creator", recording_creator)

    record = await service.create(
        context,
        V1CreateRequest("报告.docx", "报告", "正文"),
    )

    assert record.display_name == "报告.docx"
    assert leaked_during_create == [False]
    assert list(outside.iterdir()) == []
    assert [path.name for path in workspace.iterdir()] == ["报告.docx"]


@pytest.mark.asyncio
async def test_publish_uses_pinned_source_and_never_unlinks_replaced_path(
    service_env, tmp_path, monkeypatch
):
    _database, workspace, _temporary_root, service, context, publisher = service_env
    outside = tmp_path / "outside-publish-race"
    outside.mkdir(mode=0o755)
    moved_child = tmp_path / "moved-publish-child"
    external_output = outside / "output.docx"
    original_publish = publisher.publish_batch

    def replace_child_before_publish(requests):
        staged_path = requests[0].staged_path
        external_output.write_bytes(staged_path.read_bytes())
        staged_path.parent.rename(moved_child)
        staged_path.parent.symlink_to(outside, target_is_directory=True)
        return original_publish(requests)

    monkeypatch.setattr(publisher, "publish_batch", replace_child_before_publish)

    record = await service.create(
        context,
        V1CreateRequest("报告.docx", "报告", "正文"),
    )

    assert record.display_name == "报告.docx"
    assert external_output.is_file()
    assert external_output.stat().st_size > 0
    assert [path.name for path in workspace.iterdir()] == ["报告.docx"]


@pytest.mark.asyncio
async def test_descriptor_publish_closes_temporary_descriptors(
    service_env, monkeypatch
):
    _database, _workspace, _temporary_root, service, context, publisher = service_env

    def open_descriptor_count() -> int:
        return len(os.listdir("/dev/fd"))

    before = open_descriptor_count()
    await service.create(context, V1CreateRequest("报告.docx", "报告", "正文"))
    await service.create(
        replace(context, tool_call_id="tool-2"),
        V1CreateRequest("总结.docx", "总结", "正文"),
    )
    assert open_descriptor_count() == before

    def fail_promote(prepared, **kwargs):
        raise ArtifactDomainError("document_publish_conflict")

    monkeypatch.setattr(publisher._blob_store, "promote", fail_promote)
    with pytest.raises(DocumentDomainError, match="document_publish_conflict"):
        await service.create(
            replace(context, tool_call_id="tool-3"),
            V1CreateRequest("失败.docx", "失败", "正文"),
        )
    assert open_descriptor_count() == before


@pytest.mark.asyncio
async def test_descriptor_promote_deduplicates_existing_blob(
    service_env, tmp_path
):
    _database, _workspace, _temporary_root, _service, _context, publisher = (
        service_env
    )
    blob_store = publisher._blob_store
    payload = b"same immutable document"
    digest = hashlib.sha256(payload).hexdigest()
    source = tmp_path / "same.docx"
    source.write_bytes(payload)
    descriptor = os.open(source, os.O_RDONLY)
    try:
        first = blob_store.prepare_descriptor(
            descriptor, digest, len(payload), token="dup-first"
        )
        second = blob_store.prepare_descriptor(
            descriptor, digest, len(payload), token="dup-second"
        )
    finally:
        os.close(descriptor)

    first_blob = blob_store.promote(first)
    second_blob = blob_store.promote(second)

    assert first_blob == second_blob
    stored = blob_store.resolve(first_blob.stored_path)
    assert stored.read_bytes() == payload
    assert not first.temporary_path.exists()
    assert not second.temporary_path.exists()


@pytest.mark.asyncio
async def test_descriptor_publish_never_unlinks_replaced_blob_temp_path(
    service_env, tmp_path, monkeypatch
):
    _database, workspace, _temporary_root, service, context, publisher = service_env
    blob_store = publisher._blob_store
    outside = tmp_path / "outside-blob-temp"
    outside.mkdir(mode=0o755)
    moved_tmp = tmp_path / "moved-blob-temp"
    external_prepared: list[Path] = []
    original_prepare = blob_store.prepare_descriptor

    def replace_blob_temp_after_prepare(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        external = outside / prepared.temporary_path.name
        external.write_bytes(prepared.temporary_path.read_bytes())
        blob_store._tmp.rename(moved_tmp)
        blob_store._tmp.symlink_to(outside, target_is_directory=True)
        external_prepared.append(external)
        return prepared

    monkeypatch.setattr(
        blob_store,
        "prepare_descriptor",
        replace_blob_temp_after_prepare,
    )

    record = await service.create(
        context,
        V1CreateRequest("报告.docx", "报告", "正文"),
    )

    assert record.display_name == "报告.docx"
    assert len(external_prepared) == 1
    assert external_prepared[0].is_file()
    assert external_prepared[0].stat().st_size > 0
    assert [path.name for path in workspace.iterdir()] == ["报告.docx"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "create_request",
    [
        V1CreateRequest("../报告.docx", "报告", "正文"),
        V1CreateRequest("报告.txt", "报告", "正文"),
        V1CreateRequest(".docx", "报告", "正文"),
        V1CreateRequest(" .docx", "报告", "正文"),
        V1CreateRequest("\ud800.docx", "报告", "正文"),
        V1CreateRequest("a" * 116 + ".docx", "报告", "正文"),
        V1CreateRequest("报告.docx", "", "正文"),
        V1CreateRequest("报告.docx", "\ud800", "正文"),
        V1CreateRequest("报告.docx", "a" * 201, "正文"),
    ],
)
async def test_create_rejects_invalid_request_before_staging(
    service_env, create_request
):
    _database, workspace, temporary_root, service, context, _publisher = service_env

    with pytest.raises(DocumentDomainError, match="document_create_invalid"):
        await service.create(context, create_request)

    assert not temporary_root.exists()
    assert list(workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_same_name_is_numbered_continuously(service_env):
    _database, _workspace, _temporary_root, service, context, _publisher = service_env
    records = []
    for number in range(1, 4):
        records.append(
            await service.create(
                replace(context, tool_call_id=f"tool-{number}"),
                V1CreateRequest("报告.docx", "报告", "正文"),
            )
        )

    assert [item.display_name for item in records] == [
        "报告.docx",
        "报告 (2).docx",
        "报告 (3).docx",
    ]


@pytest.mark.asyncio
async def test_four_authorized_documents_are_independent_artifacts(service_env):
    database, workspace, temporary_root, service, context, _publisher = service_env
    for number in range(1, 5):
        await service.create(replace(context, tool_call_id=f"tool-{number}"),
            V1CreateRequest(f"报告-{number}.docx", "报告", "正文"))
    assert len(list(workspace.glob('*.docx'))) == 4
    assert temporary_root.exists() and list(temporary_root.iterdir()) == []


def test_concurrent_creates_preserve_each_distinct_artifact(service_env):
    database, workspace, temporary_root, service, context, _publisher = service_env
    asyncio.run(
        service.create(
            context,
            V1CreateRequest("报告-1.docx", "报告", "正文"),
        )
    )
    asyncio.run(
        service.create(
            replace(context, tool_call_id="tool-2"),
            V1CreateRequest("报告-2.docx", "报告", "正文"),
        )
    )

    barrier = threading.Barrier(2)

    def synchronized_creator(*args, **kwargs):
        barrier.wait(timeout=5)
        return create_v1_docx(*args, **kwargs)

    artifact_repository = repository(database)

    def concurrent_service(label: str) -> V1DocumentService:
        return V1DocumentService(
            artifact_repository,
            ArtifactPublisher(
                artifact_repository,
                ArtifactBlobStore(temporary_root.parents[1] / "artifacts"),
                now=lambda: NOW,
                id_factory=iter(
                    f"journal-{label}-{number}" for number in range(20)
                ).__next__,
            ),
            temporary_root=temporary_root,
            now=lambda: NOW,
            id_factory=lambda: f"artifact-{label}",
            creator=synchronized_creator,
        )

    calls = (
        (
            concurrent_service("a"),
            replace(context, tool_call_id="tool-3"),
            V1CreateRequest("报告-3.docx", "报告", "正文"),
        ),
        (
            concurrent_service("b"),
            replace(context, tool_call_id="tool-4"),
            V1CreateRequest("报告-4.docx", "报告", "正文"),
        ),
    )

    def invoke(item):
        current_service, current_context, current_request = item
        try:
            return asyncio.run(current_service.create(current_context, current_request))
        except DocumentDomainError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(invoke, calls))

    assert sum(not isinstance(result, DocumentDomainError) for result in results) == 2
    failures = [result for result in results if isinstance(result, DocumentDomainError)]
    assert failures == []
    assert len(list(workspace.glob("报告-*.docx"))) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["creator", "inspector", "publisher"])
async def test_create_failure_leaves_no_visible_result(
    service_env, monkeypatch, failure_stage
):
    database, workspace, temporary_root, service, context, publisher = service_env
    original = workspace / "原文件.docx"
    original.write_bytes(b"original")
    original_sha = hashlib.sha256(original.read_bytes()).hexdigest()

    if failure_stage == "creator":
        monkeypatch.setattr(
            service,
            "_creator",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                DocumentDomainError("document_validation_failed")
            ),
        )
        expected = "document_validation_failed"
    elif failure_stage == "inspector":
        monkeypatch.setattr(
            service,
            "_inspector",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                attachment_error("attachment_docx_invalid")
            ),
        )
        expected = "document_validation_failed"
    else:
        monkeypatch.setattr(
            publisher,
            "publish_batch",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ArtifactDomainError("document_publish_conflict")
            ),
        )
        expected = "document_publish_conflict"

    with pytest.raises(DocumentDomainError, match=expected):
        await service.create(
            context,
            V1CreateRequest("结果.docx", "结果", "正文"),
        )

    assert hashlib.sha256(original.read_bytes()).hexdigest() == original_sha
    assert sorted(path.name for path in workspace.iterdir()) == ["原文件.docx"]
    assert repository(database).list_page(
        "thread-1", limit=10, cursor=None
    ).items == ()
    assert temporary_root.exists() and list(temporary_root.iterdir()) == []


@pytest.mark.asyncio
async def test_cleanup_failure_after_commit_does_not_change_success(
    service_env, monkeypatch
):
    database, workspace, temporary_root, service, context, _publisher = service_env

    def fail_cleanup(*_args, **_kwargs):
        raise OSError("injected cleanup failure")

    monkeypatch.setattr("verilex_runtime.documents.v1_service.shutil.rmtree", fail_cleanup)
    record = await service.create(
        context,
        V1CreateRequest("结果.docx", "结果", "正文"),
    )

    assert record.display_name == "结果.docx"
    assert [path.name for path in workspace.iterdir()] == ["结果.docx"]
    assert repository(database).get_owned("thread-1", record.id) == record
    assert temporary_root.exists() and len(list(temporary_root.iterdir())) == 1


@pytest.mark.asyncio
async def test_next_create_reclaims_only_bounded_stale_document_directories(
    service_env,
):
    _database, _workspace, temporary_root, service, context, _publisher = service_env
    temporary_root.mkdir(parents=True)
    for number in range(16):
        (temporary_root / f".000-{number:02d}").write_text("unrelated")
    stale = temporary_root / ".document-v1-stale"
    stale.mkdir()
    fresh = temporary_root / ".document-v1-fresh"
    fresh.mkdir()
    old = time.time() - 25 * 60 * 60
    os.utime(stale, (old, old))

    await service.create(
        context,
        V1CreateRequest("结果.docx", "结果", "正文"),
    )

    assert not stale.exists()
    assert fresh.is_dir()
    assert len(list(temporary_root.glob(".000-*"))) == 16
