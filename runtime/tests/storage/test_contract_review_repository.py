"""合同审查三表仓储：CRUD、状态机、级联删除与参数绑定。"""
from __future__ import annotations

import pytest

from verilex_runtime.storage.contract_review_repository import ContractReviewRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import MIGRATIONS, run_migrations


def _storage(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    return database, ContractReviewRepository(database)


def _thread(database: Database, thread_id: str = "th-1") -> str:
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO threads (id, title, model, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (thread_id, "合同审查", "model-1", "idle", "2026-09-15T00:00:00Z", "2026-09-15T00:00:00Z"),
        )
    return thread_id


def _review(repository: ContractReviewRepository, **overrides) -> dict:
    fields = {
        "id": "rev-1",
        "thread_id": "th-1",
        "filename": "技术服务合同.docx",
        "file_digest": "a" * 64,
        "original": b"contract-bytes",
        "skill": "通用合同审查",
        "party": "party_b",
        "strictness": "strict",
        "signature": "VeriLex",
        "retrieval_enabled": False,
        "created_at": "2026-09-15T08:00:00Z",
        "updated_at": "2026-09-15T08:00:00Z",
    }
    fields.update(overrides)
    return repository.create_review(**fields)


def _opinion(repository: ContractReviewRepository, **overrides) -> dict:
    fields = {
        "id": "op-1",
        "review_id": "rev-1",
        "clause_label": "第六条 违约责任",
        "clause_excerpt": "每逾期一日按合同总价款的千分之三向甲方支付违约金",
        "anchor_text": "千分之三向甲方支付违约金",
        "occurrence": None,
        "level": "高",
        "category": "必须修改",
        "body": "违约金上限缺失，逾期较长时金额可能过高。",
        "suggestion": "建议增加上限：违约金总额不超过合同总价款的百分之十。",
        "created_at": "2026-09-15T08:01:00Z",
        "updated_at": "2026-09-15T08:01:00Z",
    }
    fields.update(overrides)
    return repository.upsert_opinion(**fields)


def test_migration_creates_contract_review_tables(tmp_path):
    database, _ = _storage(tmp_path)
    with database.connect() as connection:
        versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
        reviews = {row[1] for row in connection.execute("PRAGMA table_info(contract_reviews)")}
        opinions = {row[1] for row in connection.execute("PRAGMA table_info(review_opinions)")}
        citations = {row[1] for row in connection.execute("PRAGMA table_info(opinion_citations)")}
    assert versions[-1] == MIGRATIONS[-1][0]
    assert 28 in versions
    assert reviews == {
        "id", "thread_id", "filename", "file_digest", "original", "normalized_docx", "skill", "party",
        "strictness", "signature", "retrieval_enabled", "status",
        "created_at", "updated_at", "document_id", "requirements", "custom_party", "discussion_only",
    }
    assert opinions == {
        "id", "review_id", "clause_label", "clause_excerpt", "anchor_text",
        "occurrence", "level", "category", "body", "suggestion", "adopted",
        "edited_text", "replacement_text", "created_at", "updated_at",
    }
    assert citations == {"id", "opinion_id", "document", "source_kind", "source_label", "verified_at", "created_at", "source_call_id"}


def test_collaboration_migration_preserves_existing_review(tmp_path,monkeypatch):
    database=Database(tmp_path/'legacy.sqlite3')
    with monkeypatch.context() as scope:
        scope.setattr('verilex_runtime.storage.migrations.MIGRATIONS',MIGRATIONS[:-1])
        run_migrations(database)
    _thread(database)
    with database.connect() as connection:
        connection.execute("INSERT INTO contract_reviews (id,thread_id,filename,file_digest,original,skill,party,strictness,signature,retrieval_enabled,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",('legacy','th-1','合同.docx','digest',b'legacy bytes','通用合同审查','neutral','normal','V',0,'completed','t','t'))
    run_migrations(database)
    review=ContractReviewRepository(database).get_review('legacy')
    assert review['discussion_only']==0 and review['filename']=='合同.docx'
    assert ContractReviewRepository(database).original_bytes('legacy')==b'legacy bytes'


def test_create_and_get_review_roundtrip(tmp_path):
    database, repository = _storage(tmp_path)
    _thread(database)
    created = _review(repository)
    assert created["status"] == "pending"
    fetched = repository.get_review("rev-1")
    assert fetched["filename"] == "技术服务合同.docx"
    assert fetched["file_digest"] == "a" * 64
    assert fetched["party"] == "party_b"
    assert fetched["strictness"] == "strict"
    assert fetched["retrieval_enabled"] == 0
    assert repository.get_review("missing") is None


def test_status_state_machine(tmp_path):
    database, repository = _storage(tmp_path)
    _thread(database)
    _review(repository)
    repository.update_status("rev-1", "running", updated_at="2026-09-15T08:00:01Z")
    repository.update_status("rev-1", "completed", updated_at="2026-09-15T08:05:00Z")
    repository.update_status("rev-1", "running", updated_at="2026-09-15T09:00:00Z")  # 追问
    repository.update_status("rev-1", "completed", updated_at="2026-09-15T09:03:00Z")
    assert repository.get_review("rev-1")["status"] == "completed"

    for target in ("failed", "stopped", "pending"):
        with pytest.raises(ValueError):
            repository.update_status("rev-1", target, updated_at="2026-09-15T09:04:00Z")

    with pytest.raises(ValueError):
        repository.update_status("missing", "running", updated_at="now")


def test_pending_cannot_jump_to_completed(tmp_path):
    database, repository = _storage(tmp_path)
    _thread(database)
    _review(repository)
    with pytest.raises(ValueError):
        repository.update_status("rev-1", "completed", updated_at="now")
    repository.update_status("rev-1", "failed", updated_at="now")  # pending → failed 允许
    assert repository.update_status("rev-1", "running", updated_at="now")["status"] == "running"


def test_running_review_lookup(tmp_path):
    database, repository = _storage(tmp_path)
    _thread(database, "th-1")
    _thread(database, "th-2")
    _review(repository)
    assert repository.running_review()["status"] == "pending"
    _review(repository, id="rev-2", thread_id="th-2", status="running")
    running = repository.running_review()
    assert running is not None and running["id"] == "rev-2"


def test_create_review_rejects_unknown_party(tmp_path):
    database, repository = _storage(tmp_path)
    _thread(database)
    with pytest.raises(Exception):
        _review(repository, party="third_party")


def test_list_reviews_returns_summaries_with_opinion_counts(tmp_path):
    database, repository = _storage(tmp_path)
    _thread(database, "th-1")
    _thread(database, "th-2")
    _review(repository)
    _review(repository, id="rev-2", thread_id="th-2", status="completed",
            created_at="2026-09-15T10:00:00Z", updated_at="2026-09-15T10:00:00Z")
    _opinion(repository, review_id="rev-2")
    _opinion(repository, id="op-2", review_id="rev-2", created_at="2026-09-15T10:01:00Z",
             updated_at="2026-09-15T10:01:00Z")
    summaries = repository.list_reviews(limit=10)
    assert [s["id"] for s in summaries] == ["rev-2", "rev-1"]  # 新的在前
    assert summaries[0]["opinion_count"] == 2
    assert summaries[1]["opinion_count"] == 0
    assert summaries[0]["filename"] == "技术服务合同.docx"
    assert summaries[0]["status"] == "completed"
    assert len(repository.list_reviews(limit=1)) == 1


def test_upsert_opinion_preserves_user_choices(tmp_path):
    database, repository = _storage(tmp_path)
    _thread(database)
    _review(repository)
    stored = _opinion(repository)
    assert stored["adopted"] == 1
    repository.update_opinion("op-1", adopted=False, edited_text="改定后的意见",
                              updated_at="2026-09-15T08:02:00Z")
    # 模型侧重发同一条意见（追问后更新正文），不得覆盖用户选择与编辑。
    _opinion(repository, body="更新后的风险说明。", updated_at="2026-09-15T08:03:00Z")
    opinion = repository.get_opinion("op-1")
    assert opinion["body"] == "更新后的风险说明。"
    assert opinion["adopted"] == 0
    assert opinion["edited_text"] == "改定后的意见"
    opinions = repository.list_opinions("rev-1")
    assert [o["id"] for o in opinions] == ["op-1"]
    assert repository.get_opinion("missing") is None


def test_update_opinion_validates_edited_text(tmp_path):
    database, repository = _storage(tmp_path)
    _thread(database)
    _review(repository)
    _opinion(repository)
    with pytest.raises(ValueError):
        repository.update_opinion("op-1", edited_text="  ", updated_at="now")
    with pytest.raises(ValueError):
        repository.update_opinion("missing", adopted=True, updated_at="now")
    repository.update_opinion("op-1", adopted=False, updated_at="now")
    assert repository.get_opinion("op-1")["adopted"] == 0


def test_citations_roundtrip(tmp_path):
    database, repository = _storage(tmp_path)
    _thread(database)
    _review(repository)
    _opinion(repository)
    repository.add_citation(
        id="cit-1", opinion_id="op-1", document="《民法典》第五百八十五条",
        source_kind="mcp",
        source_label="元典智库", verified_at="2026-09-15", created_at="2026-09-15T08:01:30Z",
    )
    repository.add_citation(
        id="cit-2", opinion_id="op-1", document="《公司法》第十六条", source_kind="web",
        source_label="国家法律法规数据库", verified_at="2026-09-15", created_at="2026-09-15T08:01:31Z",
    )
    citations = repository.opinion_citations("op-1")
    assert [(c["document"], c["source_kind"]) for c in citations] == [
        ("《民法典》第五百八十五条", "mcp"),
        ("《公司法》第十六条", "web"),
    ]
    assert repository.opinion_citations("missing") == []


def test_delete_thread_cascades(tmp_path):
    database, repository = _storage(tmp_path)
    _thread(database)
    _review(repository)
    _opinion(repository)
    repository.add_citation(
        id="cit-1", opinion_id="op-1", document="《民法典》第五百八十五条",
        source_kind="mcp",
        source_label="元典智库", verified_at="2026-09-15", created_at="2026-09-15T08:01:30Z",
    )
    with database.connect() as connection:
        connection.execute("DELETE FROM threads WHERE id = ?", ("th-1",))
    assert repository.get_review("rev-1") is None
    assert repository.list_opinions("rev-1") == []


def test_special_characters_roundtrip(tmp_path):
    """参数绑定抽查：文件名与文本含引号不破坏 SQL。"""
    database, repository = _storage(tmp_path)
    _thread(database)
    _review(repository, filename='合作"协议" v2.docx')
    _opinion(repository, body="含'单引号'与\"双引号\"的正文。")
    assert repository.get_review("rev-1")["filename"] == '合作"协议" v2.docx'
    assert "单引号" in repository.get_opinion("op-1")["body"]
