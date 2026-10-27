"""先筛后导与导出验证门禁：只写采用的、改定后的意见；失败不产出。"""
from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from docx import Document

from verilex_runtime.contract_review.errors import ContractReviewError
from verilex_runtime.contract_review.service import ContractReviewService
from verilex_runtime.storage.models import TurnRecord, TurnStatus

from tests.contract_review.sample_contract import build_contract

NOW = "2026-09-15T08:00:00Z"


@pytest.fixture()
def env(tmp_path: Path):
    from tests.contract_review.test_service import _env

    return _env(tmp_path)


async def _review_with_contract(env, tmp_path: Path, filename="技术服务合同.docx") -> dict:
    service = env[0]
    data = build_contract(tmp_path / "contract.docx").read_bytes()
    handle = await service.start_review(
        data, filename,
        provider_id=None, model="model-1", skill="通用合同审查",
        party="party_b", strictness="strict", signature="王律师",
        retrieval_enabled=False,
    )
    env[5].create(TurnRecord(
        id="turn-1", thread_id=handle["thread_id"], status=TurnStatus.COMPLETED,
        error_code=None, started_at=NOW, finished_at=NOW, created_at=NOW,
    ))
    env[1].update_status(handle["review_id"], "running", updated_at=NOW)
    env[0].reconcile(handle["review_id"])
    return handle


def _opinion(repository, review_id: str, opinion_id: str, **overrides) -> None:
    fields = {
        "id": opinion_id,
        "review_id": review_id,
        "clause_label": "第六条 违约责任",
        "clause_excerpt": "按合同总价款的千分之三支付违约金",
        "anchor_text": "千分之三向甲方支付违约金",
        "occurrence": None,
        "level": "高",
        "category": "必须修改",
        "body": "违约金上限缺失。",
        "suggestion": "建议增加上限。",
        "created_at": NOW,
        "updated_at": NOW,
    }
    fields.update(overrides)
    repository.upsert_opinion(**fields)


@pytest.mark.asyncio
async def test_export_writes_only_adopted_and_edited(env, tmp_path):
    service, repository, *_ = env
    handle = await _review_with_contract(env, tmp_path)
    _opinion(repository, handle["review_id"], "op-keep")
    _opinion(repository, handle["review_id"], "op-drop",
             anchor_text="友好协商解决", body="将被关掉采用的意见。")
    repository.update_opinion("op-drop", adopted=False, updated_at=NOW)
    _opinion(repository, handle["review_id"], "op-edit",
             anchor_text="保密期限为合同终止后三年", body="原始正文。")
    repository.update_opinion("op-edit", edited_text="改定后的意见正文。", updated_at=NOW)

    result = service.export(handle["review_id"])
    assert result["filename"] == "技术服务合同（批注版）.docx"
    document = Document(io.BytesIO(base64.b64decode(result["content"])))
    comments = [(c.author, c.text) for c in document.comments]
    assert len(comments) == 2
    assert all(author == "王律师" for author, _ in comments)
    texts = [text for _, text in comments]
    assert any("改定后的意见正文。" in text for text in texts)
    assert not any("将被关掉采用的意见。" in text for text in texts)


@pytest.mark.asyncio
async def test_export_blocked_while_running(env, tmp_path):
    service, repository, *_ = env
    handle = await _review_with_contract(env, tmp_path)
    env[5].create(TurnRecord(
        id="turn-live", thread_id=handle["thread_id"], status=TurnStatus.RUNNING,
        error_code=None, started_at=NOW, finished_at=None, created_at=NOW,
    ))
    repository.update_status(handle["review_id"], "running", updated_at=NOW)
    with pytest.raises(ContractReviewError) as caught:
        service.export(handle["review_id"])
    assert caught.value.code == "contract_thread_busy"


@pytest.mark.asyncio
async def test_export_preserves_user_edit_without_certifying_it(env, tmp_path):
    service, repository, *_ = env
    handle = await _review_with_contract(env, tmp_path)
    _opinion(repository, handle["review_id"], "edited-law")
    edited = "依据《宇宙根本法》第999999条，应当支付。"
    repository.update_opinion("edited-law", edited_text=edited, updated_at=NOW)
    assert service.export(handle["review_id"])["content"]
    assert repository.get_opinion("edited-law")["edited_text"] == edited


@pytest.mark.asyncio
async def test_export_requires_adopted_opinions(env, tmp_path):
    service, repository, *_ = env
    handle = await _review_with_contract(env, tmp_path)
    _opinion(repository, handle["review_id"], "op-1")
    repository.update_opinion("op-1", adopted=False, updated_at=NOW)
    with pytest.raises(ContractReviewError) as caught:
        service.export(handle["review_id"])
    assert caught.value.code == "contract_annotation_write_failed"


@pytest.mark.asyncio
async def test_export_failed_anchor_blocks_and_keeps_original(env, tmp_path):
    service, repository, *_ = env
    handle = await _review_with_contract(env, tmp_path)
    before = repository.original_bytes(handle["review_id"])
    _opinion(repository, handle["review_id"], "op-bad",
             anchor_text="合同里根本不存在的一句话")
    with pytest.raises(ContractReviewError) as caught:
        service.export(handle["review_id"])
    assert caught.value.code == "contract_annotation_write_failed"
    assert repository.original_bytes(handle["review_id"]) == before


@pytest.mark.asyncio
async def test_export_unknown_review(env):
    service = env[0]
    with pytest.raises(ContractReviewError):
        service.export("missing")


@pytest.mark.asyncio
async def test_export_review_not_found_after_reconcile(env, tmp_path):
    service = env[0]
    with pytest.raises(ContractReviewError) as caught:
        service.export("missing")
    assert caught.value.code == "contract_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["长" * 76 + ".docx", "合同*.docx", ".合同?.docx"])
async def test_export_portable_name_preserves_original_and_comments(env, tmp_path, filename):
    service, repository, *_ = env
    handle = await _review_with_contract(env, tmp_path, filename)
    original = repository.original_bytes(handle["review_id"])
    _opinion(repository, handle["review_id"], "portable-name")
    result = service.export(handle["review_id"])
    assert len(result["filename"].encode("utf-8")) <= 240
    assert result["filename"].endswith("（批注版）.docx")
    assert not result["filename"].startswith(".")
    assert not any(char in result["filename"] for char in '*?')
    assert repository.get_review(handle["review_id"])["filename"] == filename
    assert repository.original_bytes(handle["review_id"]) == original
    document = Document(io.BytesIO(base64.b64decode(result["content"])))
    assert len(document.comments) == 1


@pytest.mark.asyncio
async def test_doc_import_preserves_source_and_freezes_docx_baseline(env,tmp_path,monkeypatch):
    from verilex_runtime.attachments import legacy_doc
    from verilex_runtime.contract_review.preview import build_preview
    from verilex_runtime.documents.comment_verify import verify_comment_copy
    normalized=build_contract(tmp_path/'baseline.docx').read_bytes()
    raw=legacy_doc._MAGIC+b'synthetic original'
    monkeypatch.setattr(legacy_doc,'convert_doc',lambda data: normalized)
    service,repository,*_=env
    handle=await service.start_review(raw,'旧合同.doc',provider_id=None,model='model-1',skill='通用合同审查',party='neutral',strictness='normal',signature='VeriLex',retrieval_enabled=False)
    assert repository.original_bytes(handle['review_id'])==raw
    assert repository.review_bytes(handle['review_id'])==normalized
    assert base64.b64decode(build_preview(repository.review_bytes(handle['review_id']))['content'])==normalized
    _opinion(repository,handle['review_id'],'doc-opinion')
    # Export must never run a fresh conversion against a changed installation.
    monkeypatch.setattr(legacy_doc,'convert_doc',lambda _: pytest.fail('converted twice'))
    result=service.export(handle['review_id'])
    assert result['filename']=='旧合同（批注版）.docx'
    assert verify_comment_copy(normalized,base64.b64decode(result['content'])).passed
    assert repository.original_bytes(handle['review_id'])==raw
