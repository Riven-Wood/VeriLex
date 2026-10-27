import hashlib
import json
from dataclasses import replace
from urllib.parse import urlsplit

import pytest

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    RiskLevel,
    ThreadRecord,
    ThreadStatus,
    ToolCallRecord,
    ToolCallStatus,
    TurnRecord,
    TurnStatus,
)
from verilex_runtime.storage.thread_repository import ThreadRepository, TurnRepository
from verilex_runtime.storage.tool_repository import ToolCallRepository
from verilex_runtime.web.citations import (
    CitationResolver,
    citation_warning_block,
    sources_block,
    web_access_offer_block,
)
from verilex_runtime.web.tools import UNTRUSTED_PREFIX, UNTRUSTED_SUFFIX


NOW = "2026-08-27T08:09:10Z"


class SourceLedger:
    def __init__(self, repository: ToolCallRepository) -> None:
        self.repository = repository

    def add(
        self,
        source_id: str,
        *,
        turn_id: str = "turn-web",
        name: str = "web_read",
        status: ToolCallStatus = ToolCallStatus.SUCCEEDED,
        title: str = "来源标题",
        url: str = "https://example.com/article",
        text: str = "已经核验的网页正文",
    ) -> ToolCallRecord:
        payload = {
            "source_id": source_id,
            "title": title,
            "url": url,
            "site": urlsplit(url).hostname,
            "fetched_at": NOW,
            "content_type": "text/html; charset=utf-8",
            "character_count": len(text),
            "truncated": False,
            "text": f"{UNTRUSTED_PREFIX}\n{text}\n{UNTRUSTED_SUFFIX}",
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        record = ToolCallRecord(
            id=source_id,
            turn_id=turn_id,
            provider_call_id=f"provider-{source_id}",
            name=name,
            arguments_json="{}",
            risk_level=RiskLevel.HIGH,
            status=status,
            result_json=json.dumps(
                {"ok": status is ToolCallStatus.SUCCEEDED, "output": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            started_at=NOW,
            finished_at=NOW,
        )
        self.repository.create(record)
        return record


@pytest.fixture
def source_env(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    threads = ThreadRepository(database)
    turns = TurnRepository(database)
    tool_calls = ToolCallRepository(database)
    for thread_id in ("t1", "t2"):
        threads.create(
            ThreadRecord(
                id=thread_id,
                title=thread_id,
                provider_id=None,
                model="model",
                workspace_id=None,
                status=ThreadStatus.IDLE,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    for turn_id, thread_id in (
        ("turn-web", "t1"),
        ("turn-old", "t1"),
        ("turn-local", "t1"),
        ("turn-foreign", "t2"),
    ):
        turns.create(
            TurnRecord(
                id=turn_id,
                thread_id=thread_id,
                status=TurnStatus.COMPLETED,
                created_at=NOW,
                started_at=NOW,
                finished_at=NOW,
            )
        )
    return CitationResolver(tool_calls), SourceLedger(tool_calls)


def test_only_successful_same_thread_web_reads_resolve(source_env):
    resolver, ledger = source_env
    good = ledger.add("good")
    foreign = ledger.add("foreign", turn_id="turn-foreign", url="https://other.com/b")
    failed = ledger.add("failed", status=ToolCallStatus.FAILED)
    search = ledger.add("search", name="web_search")

    result = resolver.resolve(
        (
            f"事实[[cite:{good.id}]] "
            f"跨任务[[cite:{foreign.id}]] "
            f"失败[[cite:{failed.id}]] "
            f"搜索[[cite:{search.id}]] "
            "伪造[[cite:made-up]] 手写[[cite:https://evil.test/x]]"
        ),
        thread_id="t1",

    )

    assert [source.source_id for source in result.sources] == ["good"]
    assert result.needs_repair is True
    assert result.warning is False


def test_plain_markdown_url_is_not_promoted_to_a_verified_source(source_env):
    resolver, _ = source_env

    result = resolver.resolve(
        "普通链接 [网页](https://example.com/a)",
        thread_id="t1",

    )

    assert result.sources == ()
    assert result.needs_repair is False


@pytest.mark.parametrize(
    "malformed",
    [
        "残缺[[cite:source-a",
        "空标记[[cite:]]",
        "换行[[cite:source-a\n后文]]",
        "嵌套[[cite:outer[[cite:good]]",
        "超长[[cite:" + ("x" * 129) + "]]",
    ],
)
def test_malformed_citation_marker_is_repaired_and_never_becomes_a_source(
    source_env, malformed
):
    resolver, ledger = source_env
    ledger.add("good")

    draft = resolver.resolve(
        malformed,
        thread_id="t1",

    )
    final = resolver.resolve(
        malformed,
        thread_id="t1",

        final_attempt=True,
    )

    assert draft.needs_repair is True
    assert draft.sources == ()
    assert "[[cite:" not in final.text
    assert "〔引用未验证〕" in final.text
    assert final.sources == ()
    assert final.warning is True


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda record: replace(record, result_json="{"),
        lambda record: replace(record, result_json=json.dumps({"ok": False, "output": "{}"})),
        lambda record: replace(
            record,
            result_json=record.result_json.replace(
                hashlib.sha256("已经核验的网页正文".encode("utf-8")).hexdigest(),
                "0" * 64,
            ),
        ),
        lambda record: replace(
            record,
            result_json=record.result_json.replace(
                "https://example.com/article", "HTTPS://example.com:443/article#fragment"
            ),
        ),
        lambda record: replace(record, result_json="x" * (128 * 1024 + 1)),
    ],
)
def test_corrupt_stored_web_read_is_never_a_source(source_env, corrupt):
    resolver, ledger = source_env
    record = ledger.add("corrupt")
    assert ledger.repository.update(corrupt(record)) is True

    result = resolver.resolve(
        "不可信[[cite:corrupt]]",
        thread_id="t1",

    )

    assert result.sources == ()
    assert result.needs_repair is True


def test_sources_follow_first_appearance_and_duplicate_url_sha_uses_one_number(source_env):
    resolver, ledger = source_env
    ledger.add("source-a", title="A", url="https://example.com/a", text="A正文")
    ledger.add("source-b", title="B", url="https://example.com/b", text="B正文")
    ledger.add("source-a-copy", title="A副本", url="https://example.com/a", text="A正文")

    result = resolver.resolve(
        "先B[[cite:source-b]] 再A[[cite:source-a]] 又A[[cite:source-a-copy]]",
        thread_id="t1",

    )

    assert [(source.number, source.source_id) for source in result.sources] == [
        (1, "source-b"),
        (2, "source-a"),
    ]
    assert result.text == (
        "先B[[cite:source-b]] 再A[[cite:source-a]] 又A[[cite:source-a]]"
    )
    assert result.needs_repair is False


def test_reading_a_source_does_not_require_citing_it_in_every_answer(source_env):
    resolver, ledger = source_env
    ledger.add("historical", turn_id="turn-old")

    assert resolver.resolve(
        "纯本地回答", thread_id="t1"
    ).needs_repair is False

    ledger.add("current", turn_id="turn-web", url="https://example.com/current")
    assert resolver.resolve(
        "收到，暂不作结论。", thread_id="t1"
    ).needs_repair is False


def test_offer_marker_only_becomes_one_block_while_web_access_is_off(source_env):
    resolver, _ = source_env

    disabled = resolver.resolve(
        "可以补充[[offer_web_access]]再次[[offer_web_access]]",
        thread_id="t1",

        web_access_enabled=False,
    )
    enabled = resolver.resolve(
        "已经打开[[offer_web_access]]",
        thread_id="t1",

        web_access_enabled=True,
    )

    assert disabled.text == "可以补充再次"
    assert disabled.web_access_offer is True
    assert enabled.text == "已经打开"
    assert enabled.web_access_offer is False


@pytest.mark.parametrize("split", range(1, 19))
def test_offer_filter_hides_marker_at_every_stream_split(split):
    from verilex_runtime.web.citations import WebOfferStreamFilter

    marker = "[[offer_web_access]]"
    stream_filter = WebOfferStreamFilter()
    first = stream_filter.feed("前文" + marker[:split])
    second = stream_filter.feed(marker[split:] + "后文 [普通](link)")
    assert first == "前文"
    assert first + second + stream_filter.finish() == "前文后文 [普通](link)"


def test_offer_filter_preserves_brackets_and_hides_incomplete_control_word():
    from verilex_runtime.web.citations import strip_web_offer_marker

    assert strip_web_offer_marker("正文[[") == "正文[["
    assert strip_web_offer_marker("正文[[offer_web_") == "正文"


def test_final_attempt_replaces_unknown_marker_and_adds_warning(source_env):
    resolver, ledger = source_env
    ledger.add("good")

    result = resolver.resolve(
        "合法[[cite:good]] 伪造[[cite:made-up]]",
        thread_id="t1",

        final_attempt=True,
    )

    assert result.text == "合法[[cite:good]] 伪造〔引用未验证〕"
    assert [source.source_id for source in result.sources] == ["good"]
    assert result.warning is True
    assert result.needs_repair is True


def test_structured_blocks_use_bounded_camel_case_protocol(source_env):
    resolver, ledger = source_env
    ledger.add("good", title="标题")
    resolution = resolver.resolve(
        "事实[[cite:good]]", thread_id="t1"
    )

    assert sources_block(resolution.sources) == {
        "type": "sources",
        "sources": [
            {
                "number": 1,
                "sourceId": "good",
                "title": "标题",
                "url": "https://example.com/article",
                "site": "example.com",
                "fetchedAt": NOW,
                "truncated": False,
            }
        ],
    }
    assert web_access_offer_block() == {"type": "web_access_offer"}
    assert citation_warning_block() == {"type": "citation_warning"}

def test_all_successful_source_ids_remain_resolvable_over_display_threshold(source_env):
    resolver, ledger = source_env
    for i in range(18): ledger.add(f'source-{i}', url=f'https://example.com/{i}', text=f'合成正文{i}')
    answer = ' '.join(f'[[cite:source-{i}]]' for i in range(18))
    result = resolver.resolve(answer, thread_id='t1')
    assert len(result.sources) == 18 and not result.needs_repair
