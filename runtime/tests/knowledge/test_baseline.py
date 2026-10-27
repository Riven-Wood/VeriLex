import importlib.util
from pathlib import Path
import sys

import pytest

from verilex_runtime.knowledge.source import SourceSnapshot

path = Path(__file__).resolve().parents[3] / "scripts/knowledge/baseline.py"
spec = importlib.util.spec_from_file_location("knowledge_offline_baseline", path)
baseline = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = baseline
spec.loader.exec_module(baseline)


@pytest.fixture
def index():
    sources = [SourceSnapshot("allowed", "z", "测试.md",
                              '合成：违约，〔虚构2026〕测字第1号。a " OR x -- 100%_ 😀'.encode())]
    sources += [SourceSnapshot("other", f"a{i}", "干扰.md", "违约".encode()) for i in range(20)]
    value = baseline.LiteralIndex(sources)
    yield value
    value.close()


@pytest.mark.parametrize("query", ["违", "违约", "〔虚构2026〕测字第1号", '" OR x --', "100%_", "😀"])
def test_literal_short_terms_punctuation_and_operators(index, query):
    assert index.search(query, ("allowed",), 1).document_ids == ("z",)


def test_scope_is_applied_before_limiting_and_empty_scope_reads_nothing(index):
    assert index.search("违约", (), 1).document_ids == ()
    page = index.search("违约", ("allowed",), 1)
    assert page.document_ids == ("z",) and not page.truncated
    page = index.search("违约", ("other",), 1)
    assert len(page.document_ids) == 1 and page.truncated
    assert index.search("没有这个词", ("allowed",)).document_ids == ()


@pytest.mark.parametrize("query", ["", "  ", "\x00", "字" * 257])
def test_invalid_queries_fail_explicitly(index, query):
    with pytest.raises(ValueError, match="invalid_query"):
        index.search(query, ("allowed",))


def test_prelabelled_corpus_and_baseline_report_dont_claim_semantic_success():
    report = baseline.run()
    assert report["documents"] == 100
    assert len(report["queries"]) == 60
    assert report["quotes_exact"] == 100
    assert report["summary"]["exact"]["hits"] == 20
    assert report["summary"]["natural"]["hits"] == 0
    assert report["summary"]["multi"]["hits"] == 0
    assert report["summary"]["absent"]["empty"] == 10
    assert {q["split"] for q in report["queries"]} == {"dev", "heldout"}
