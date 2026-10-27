"""审查编排服务：文件校验、一次一份、场景线程、意见工具落库与运行时核验。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from docx import Document

from verilex_runtime.attachments.blob_store import AttachmentBlobStore
from verilex_runtime.attachments.service import AttachmentService
from verilex_runtime.contract_review.citations import (
    RetrievedSource,
    source_index_from_tool_calls,
)
from verilex_runtime.contract_review.errors import ContractReviewError
from verilex_runtime.contract_review.service import ContractReviewService
from verilex_runtime.storage.attachment_repository import AttachmentRepository
from verilex_runtime.storage.contract_review_repository import ContractReviewRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import RiskLevel, ToolCallStatus
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
from verilex_runtime.storage.tool_repository import ToolCallRepository
from verilex_runtime.sessions.thread_service import ThreadService
from tests.contract_review.sample_contract import build_contract

NOW = "2026-09-15T08:00:00Z"


async def _start_test_review(service, tmp_path, filename="test.docx"):
    return await service.start_review(
        _contract_bytes(tmp_path), filename, provider_id=None, model="model-1",
        skill="通用合同审查", party="neutral", strictness="normal",
        signature="VeriLex", retrieval_enabled=False,
    )


@pytest.mark.asyncio
async def test_concurrent_creates_reserve_one_review_until_first_turn(tmp_path, monkeypatch):
    import asyncio
    service, repository, *_ = _env(tmp_path)
    upload_entered, release_upload = asyncio.Event(), asyncio.Event()
    upload = service._attachments.upload
    async def paused_upload(*args, **kwargs):
        upload_entered.set()
        await release_upload.wait()
        return await upload(*args, **kwargs)
    monkeypatch.setattr(service._attachments, "upload", paused_upload)
    first = asyncio.create_task(_start_test_review(service, tmp_path, "first.docx"))
    await upload_entered.wait()
    second = asyncio.create_task(_start_test_review(service, tmp_path, "second.docx"))
    await asyncio.sleep(0)
    release_upload.set()
    result = await asyncio.gather(first, second, return_exceptions=True)
    assert isinstance(result[0], dict)
    assert isinstance(result[1], ContractReviewError)
    assert result[1].code == "contract_thread_busy"
    assert len(repository.list_reviews()) == 1


@pytest.mark.asyncio
async def test_new_review_reconciles_completed_previous_turn(tmp_path):
    from verilex_runtime.storage.models import TurnRecord, TurnStatus
    service, repository, _, _, _, turns = _env(tmp_path)
    first = await _start_test_review(service, tmp_path)
    repository.update_status(first["review_id"], "running", updated_at=NOW)
    turns.create(TurnRecord(id="done", thread_id=first["thread_id"], status=TurnStatus.COMPLETED,
        error_code=None, started_at=NOW, finished_at=NOW, created_at=NOW))
    second = await _start_test_review(service, tmp_path, "second.docx")
    assert second["review_id"] != first["review_id"]
    assert repository.get_review(first["review_id"])["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["stopped", "failed"])
async def test_terminal_review_resumes_only_for_new_active_turn(tmp_path, outcome):
    from verilex_runtime.storage.models import TurnRecord, TurnStatus
    service, repository, _, _, _, turns = _env(tmp_path)
    review = await _start_test_review(service, tmp_path)
    repository.update_status(review["review_id"], "running", updated_at=NOW)
    turns.create(TurnRecord(id="old", thread_id=review["thread_id"],
        status=TurnStatus.RUNNING if outcome == "stopped" else TurnStatus.FAILED,
        error_code=None, started_at=NOW, finished_at=None, created_at=NOW))
    if outcome == "stopped":
        service.stop(review["review_id"])
    else:
        service.reconcile(review["review_id"])
    assert service.reconcile(review["review_id"])["status"] == outcome
    payload = {"clause_label": "付款", "clause_excerpt": "付款", "anchor_text": "付款", "body": "明确付款期限"}
    with pytest.raises(ContractReviewError):
        service.submit_opinion(_context(review["thread_id"], "old"), payload)
    if outcome == "stopped":
        from dataclasses import replace
        turns.update(replace(turns.get("old"), status=TurnStatus.CANCELLED, finished_at=NOW))
    turns.create(TurnRecord(id="new", thread_id=review["thread_id"], status=TurnStatus.RUNNING,
        error_code=None, started_at=NOW, finished_at=None, created_at="2026-09-16T00:00:00Z"))
    assert service.reconcile(review["review_id"])["status"] == "running"
    assert service.submit_opinion(_context(review["thread_id"], "new"), payload)["opinion_id"]
    with pytest.raises(ContractReviewError):
        service.submit_opinion(_context(review["thread_id"], "old"), payload)


def test_default_clock_and_optional_opinion_fields():
    from verilex_runtime.contract_review.service import _utc_now
    from datetime import datetime
    assert datetime.fromisoformat(_utc_now()).tzinfo is not None
    service = object.__new__(ContractReviewService)
    service._id_factory = lambda: "new-opinion"
    opinion = service._opinion_from_payload({
        "clause_label": "付款", "clause_excerpt": "付款", "anchor_text": "付款", "body": "补充期限。"
    })
    assert opinion.suggestion == ""
    assert opinion.level == ""


@pytest.mark.asyncio
async def test_opinion_id_cannot_update_another_contract(tmp_path):
    service, repository, *_ = _env(tmp_path)
    async def start(name):
        return await service.start_review(
            _contract_bytes(tmp_path), name, provider_id=None, model="model-1",
            skill="通用合同审查", party="neutral", strictness="normal",
            signature="VeriLex", retrieval_enabled=False,
        )
    first = await start("first.docx")
    payload = {"opinion_id": "op-1", "clause_label": "付款", "clause_excerpt": "付款",
               "anchor_text": "付款", "body": "第一份意见"}
    service.submit_opinion(_context(first["thread_id"]), payload)
    service.stop(first["review_id"])
    second = await start("second.docx")
    with pytest.raises(ContractReviewError):
        service.submit_opinion(_context(second["thread_id"]), {**payload, "body": "覆盖"})
    assert repository.get_opinion("op-1")["body"] == "第一份意见"


@pytest.mark.asyncio
async def test_zero_opinion_turn_reconciles_and_stop_cancels(tmp_path):
    from verilex_runtime.storage.models import TurnRecord, TurnStatus
    service, repository, _, _, _, turns = _env(tmp_path)
    handle = await service.start_review(
        _contract_bytes(tmp_path), "test.docx", provider_id=None, model="model-1",
        skill="通用合同审查", party="neutral", strictness="normal",
        signature="VeriLex", retrieval_enabled=False,
    )
    turns.create(TurnRecord(id="finished", thread_id=handle["thread_id"], status=TurnStatus.COMPLETED,
        error_code=None, started_at=NOW, finished_at=NOW, created_at=NOW))
    assert service.reconcile(handle["review_id"])["status"] == "completed"
    turns.create(TurnRecord(id="followup", thread_id=handle["thread_id"], status=TurnStatus.RUNNING,
        error_code=None, started_at=NOW, finished_at=None, created_at=NOW))
    assert service.reconcile(handle["review_id"])["status"] == "running"
    cancelled = []
    service.bind_cancel(lambda turn_id: cancelled.append(turn_id) or True)
    assert service.stop(handle["review_id"])["status"] == "stopped"
    assert cancelled == ["followup"]


def _env(tmp_path: Path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    providers = ProviderRepository(database)
    from verilex_runtime.storage.models import ProviderKind, AuthMode, ProviderRecord

    providers.create(
        ProviderRecord(
            id="provider-1", name="Compat", kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1", default_model="model-1",
            auth_mode=AuthMode.NONE, secret_ref=None, enabled=True,
            created_at=NOW, updated_at=NOW,
        )
    )
    counter = iter(range(1, 1000))
    threads = ThreadService(
        ThreadRepository(database), providers,
        id_factory=lambda: f"th-{next(counter)}", now=lambda: NOW,
    )
    attachments = AttachmentService(
        threads, AttachmentRepository(database),
        AttachmentBlobStore(tmp_path / "attachments"),
        id_factory=lambda: f"upl-{next(counter)}", now=lambda: NOW,
    )
    tool_calls = ToolCallRepository(database)
    turns = TurnRepository(database)
    repository = ContractReviewRepository(database)
    service = ContractReviewService(
        threads=threads,
        attachments=attachments,
        repository=repository,
        tool_calls=tool_calls,
        turns=turns,
        id_factory=lambda: f"id-{next(counter)}",
        now=lambda: NOW,
    )
    return service, repository, database, threads, tool_calls, turns


def _contract_bytes(tmp_path: Path) -> bytes:
    path = build_contract(tmp_path / "contract.docx")
    return path.read_bytes()


@pytest.mark.asyncio
async def test_start_review_creates_scene_thread_and_attachment(tmp_path):
    service, repository, _, threads, _, _ = _env(tmp_path)
    handle = await service.start_review(
        _contract_bytes(tmp_path), "技术服务合同.docx",
        provider_id="provider-1", model="model-1",
        skill="通用合同审查", party="party_b", strictness="strict",
        signature="VeriLex", retrieval_enabled=False,
    )
    assert handle["filename"] == "技术服务合同.docx"
    thread = threads.get(handle["thread_id"])
    assert thread.scene == "contract-review"
    assert thread.legal_skills == ("通用合同审查",)
    review = repository.get_review(handle["review_id"])
    assert review["status"] == "pending"
    assert review["party"] == "party_b"
    assert review["strictness"] == "strict"
    assert review["retrieval_enabled"] == 0
    assert review["file_digest"] and len(review["file_digest"]) == 64
    assert handle["upload_id"]


@pytest.mark.asyncio
async def test_start_review_rejects_non_docx(tmp_path):
    service, *_ = _env(tmp_path)
    for filename in ("合同.pdf", "合同.rtf", "合同.docx.exe"):
        with pytest.raises(ContractReviewError) as caught:
            await service.start_review(
                _contract_bytes(tmp_path), filename,
                provider_id=None, model="model-1", skill="通用合同审查",
                party="neutral", strictness="normal", signature="VeriLex",
                retrieval_enabled=False,
            )
        assert caught.value.code == "contract_file_unsupported"


@pytest.mark.asyncio
async def test_start_review_rejects_corrupt_docx(tmp_path):
    service, *_ = _env(tmp_path)
    with pytest.raises(ContractReviewError) as caught:
        await service.start_review(
            b"not a zip", "合同.docx",
            provider_id=None, model="model-1", skill="通用合同审查",
            party="neutral", strictness="normal", signature="VeriLex",
            retrieval_enabled=False,
        )
    assert caught.value.code == "contract_file_unsupported"


@pytest.mark.asyncio
async def test_second_review_while_running_rejected(tmp_path):
    service, repository, *_ = _env(tmp_path)
    first = await service.start_review(
        _contract_bytes(tmp_path), "A.docx",
        provider_id=None, model="model-1", skill="通用合同审查",
        party="neutral", strictness="normal", signature="VeriLex",
        retrieval_enabled=False,
    )
    repository.update_status(first["review_id"], "running", updated_at=NOW)
    with pytest.raises(ContractReviewError) as caught:
        await service.start_review(
            _contract_bytes(tmp_path), "B.docx",
            provider_id=None, model="model-1", skill="通用合同审查",
            party="neutral", strictness="normal", signature="VeriLex",
            retrieval_enabled=False,
        )
    assert caught.value.code == "contract_thread_busy"


def _seed_law_read(tool_calls, thread_id: str, turn_id: str,
                   title: str, effectivity: str, *, name: str = "law_read",
                   status=ToolCallStatus.SUCCEEDED, finished_at: str = NOW) -> None:
    from verilex_runtime.storage.models import ToolCallRecord

    payload = {"title": title, "effectivity": effectivity, "bbbs": "x1",
               "body_text": "第五百八十五条 当事人可以约定违约金。"}
    tool_calls.create(
        ToolCallRecord(
            id=f"tc-{title}-{turn_id}", turn_id=turn_id,
            provider_call_id=None, name=name,
            arguments_json=json.dumps({"bbbs": "x1"}),
            risk_level=RiskLevel.HIGH, status=status,
            result_json=json.dumps(
                {"ok": True, "output": json.dumps(payload, ensure_ascii=False)}
            ),
            started_at=finished_at, finished_at=finished_at,
        )
    )


def _context(thread_id: str, turn_id: str = "turn-1"):
    from verilex_runtime.tools.registry import ToolContext
    from pathlib import Path as _P

    return ToolContext(
        root_path=_P("/"), allow_read=True, allow_write=False,
        thread_id=thread_id, turn_id=turn_id, tool_call_id="call-1",
    )


@pytest.mark.asyncio
async def test_submit_opinion_gates_citations_and_persists(tmp_path):
    service, repository, database, threads, tool_calls, turns = _env(tmp_path)
    handle = await service.start_review(
        _contract_bytes(tmp_path), "合同.docx",
        provider_id=None, model="model-1", skill="通用合同审查",
        party="neutral", strictness="normal", signature="VeriLex",
        retrieval_enabled=False,
    )
    from verilex_runtime.storage.models import TurnRecord, TurnStatus

    turns.create(TurnRecord(
        id="turn-1", thread_id=handle["thread_id"], status=TurnStatus.RUNNING,
        error_code=None, started_at=NOW, finished_at=None, created_at=NOW,
    ))
    _seed_law_read(tool_calls, handle["thread_id"], "turn-1",
                   "中华人民共和国民法典", "现行有效")
    outcome = service.submit_opinion(
        _context(handle["thread_id"]),
        {
            "clause_label": "第六条 违约责任",
            "clause_excerpt": "按合同总价款的千分之三支付违约金",
            "anchor_text": "千分之三向甲方支付违约金",
            "level": "高",
            "category": "必须修改",
            "body": "违约金上限缺失。",
            "suggestion": "建议增加上限。",
            "citations": [
                {"document": "《民法典》第 585 条", "source_kind": "web",
                 "source_call_id": "tc-中华人民共和国民法典-turn-1"},
                {"document": "《宇宙根本法》第 1 条", "source_kind": "web",
                 "source_call_id": "invented-call"},
            ],
        },
    )
    assert outcome["citations_kept"] == 1
    assert outcome["citations_stripped"] == 1
    opinion = repository.list_opinions(handle["review_id"])
    assert len(opinion) == 1
    citations = repository.opinion_citations(opinion[0]["id"])
    assert len(citations) == 1
    assert citations[0]["source_label"] == "law_read"  # 检索记录口径
    assert repository.get_review(handle["review_id"])["status"] == "running"  # 意见到达即 running


@pytest.mark.asyncio
async def test_submit_opinion_requires_review(tmp_path):
    service, *_ = _env(tmp_path)
    thread = service._threads.create("无关线程", "model-1")
    with pytest.raises(ContractReviewError) as caught:
        service.submit_opinion(_context(thread.id), {"body": "x"})
    assert caught.value.code == "contract_not_found"


@pytest.mark.asyncio
async def test_reconcile_flips_running_to_completed_or_failed(tmp_path):
    service, repository, database, threads, tool_calls, turns = _env(tmp_path)
    handle = await service.start_review(
        _contract_bytes(tmp_path), "合同.docx",
        provider_id=None, model="model-1", skill="通用合同审查",
        party="neutral", strictness="normal", signature="VeriLex",
        retrieval_enabled=False,
    )
    from verilex_runtime.storage.models import TurnRecord, TurnStatus

    review_id = handle["review_id"]
    # 无 turn：保持 pending，不误报 running。
    assert service.reconcile(review_id)["status"] == "pending"
    turns.create(TurnRecord(
        id="t-a", thread_id=handle["thread_id"], status=TurnStatus.COMPLETED,
        error_code=None, started_at=NOW, finished_at=NOW, created_at=NOW,
    ))
    repository.update_status(review_id, "running", updated_at=NOW)
    assert service.reconcile(review_id)["status"] == "completed"
    # 追问：completed 可回 running；turn 失败时 reconcile 落 failed。
    repository.update_status(review_id, "running", updated_at=NOW)
    turns.create(TurnRecord(
        id="t-b", thread_id=handle["thread_id"], status=TurnStatus.FAILED,
        error_code="provider_failed", started_at=NOW, finished_at=NOW,
        created_at=NOW,
    ))
    assert service.reconcile(review_id)["status"] == "failed"


@pytest.mark.asyncio
async def test_stop_marks_stopped(tmp_path):
    service, repository, *_ = _env(tmp_path)
    handle = await service.start_review(
        _contract_bytes(tmp_path), "合同.docx",
        provider_id=None, model="model-1", skill="通用合同审查",
        party="neutral", strictness="normal", signature="VeriLex",
        retrieval_enabled=False,
    )
    repository.update_status(handle["review_id"], "running", updated_at=NOW)
    assert service.stop(handle["review_id"])["status"] == "stopped"
    with pytest.raises(ContractReviewError):
        service.stop("missing")


def test_source_index_from_tool_calls_filters_and_parses():
    from verilex_runtime.storage.models import ToolCallRecord, TurnRecord, TurnStatus
    from verilex_runtime.storage.tool_repository import ToolCallRepository
    from verilex_runtime.storage.database import Database
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        database = Database(Path(raw) / "t.sqlite3")
        run_migrations(database)
        tool_calls = ToolCallRepository(database)
        thread = ThreadService(
            ThreadRepository(database), None,
            id_factory=lambda: "th-x", now=lambda: NOW,
        ).create("来源索引", "model-1")
        TurnRepository(database).create(TurnRecord(
            id="turn-1", thread_id=thread.id, status=TurnStatus.COMPLETED,
            error_code=None, started_at=NOW, finished_at=NOW, created_at=NOW,
        ))

        def add(call_id, name, payload, ok=True, status=ToolCallStatus.SUCCEEDED):
            tool_calls.create(ToolCallRecord(
                id=call_id, turn_id="turn-1", provider_call_id=None, name=name,
                arguments_json="{}", risk_level=RiskLevel.HIGH, status=status,
                result_json=json.dumps(
                    {"ok": ok, "output": json.dumps(payload, ensure_ascii=False)}
                ),
                started_at=NOW, finished_at=NOW,
            ))

        add("c1", "law_search", {"rows": [
            {"title": "中华人民共和国民法典", "effectivity": "现行有效"},
            {"title": "中华人民共和国合同法", "effectivity": "已废止"},
        ]})
        add("c2", "law_read", {"title": "中华人民共和国公司法", "effectivity": "已修改", "clause_text": ["第十六条 合成正文"]})
        add("c3", "law_read", {"title": "失败的调用不该入索引"}, ok=False,
            status=ToolCallStatus.FAILED)
        add("c4", "mcp__lawlib__statute", {"title": "示例市房屋租赁管理条例", "effectivity": "现行有效", "content": "第三条 合成正文"})
        index = source_index_from_tool_calls(tool_calls.list_by_thread("th-x"))
    assert len(index) == 3
    assert index.match('c1').source_label=='law_search'
    assert index.match('c2').source_kind=='web'
    assert index.match('c4').source_kind=='mcp'
    assert index.match('c3') is None
    assert index.match('《公司法》第16条') is None



@pytest.mark.asyncio
async def test_ordinary_holiday_wording_saves_without_statute_lookup(tmp_path):
    from verilex_runtime.storage.models import TurnRecord, TurnStatus
    service, repository, _, _, _, turns = _env(tmp_path)
    handle = await _start_test_review(service, tmp_path)
    turns.create(TurnRecord(id='holiday-turn', thread_id=handle['thread_id'],
        status=TurnStatus.RUNNING, error_code=None, started_at=NOW, finished_at=None, created_at=NOW))
    replacement = '每月五日前支付，遇法定节假日顺延至下一个工作日。'
    result = service.submit_opinion(_context(handle['thread_id'], 'holiday-turn'), {
        'clause_label': '付款期限', 'clause_excerpt': '支付租金', 'anchor_text': '支付租金',
        'body': '明确付款时间；未开启法条检索，不援引法规。',
        'suggestion': replacement, 'replacement_text': replacement,
    })
    opinions = repository.list_opinions(handle['review_id'])
    assert len(opinions) == 1
    assert opinions[0]['id'] == result['opinion_id']
    assert opinions[0]['replacement_text'] == replacement
    assert result['citations_kept'] == 0
