"""Offline literal retrieval baseline; no model, network, or application DB.

Run from Source: runtime/.venv/bin/python scripts/knowledge/baseline.py
Search ranking/short-query scans here are a benchmark, not a production engine.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime"))
from verilex_runtime.knowledge.source import SourceSnapshot


@dataclass(frozen=True)
class SearchPage:
    document_ids: tuple[str, ...]
    truncated: bool


class LiteralIndex:
    """Bounded synthetic-corpus index; callers supply scope, never a model."""

    def __init__(self, sources: list[SourceSnapshot]):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE VIRTUAL TABLE docs USING fts5("
                        "id UNINDEXED, library UNINDEXED, body, tokenize='trigram case_sensitive 1')")
        for source in sources:
            self.db.execute("INSERT INTO docs VALUES (?,?,?)",
                            (source.document_id, source.library_id, source.text))

    def close(self) -> None:
        self.db.close()

    def search(self, query: str, libraries: tuple[str, ...], limit: int = 10) -> SearchPage:
        if not query.strip() or "\x00" in query or len(query) > 256:
            raise ValueError("invalid_query")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_limit")
        if len(libraries) > 100:
            raise ValueError("scope_too_large")
        if not libraries:
            return SearchPage((), False)
        placeholders = ",".join("?" for _ in libraries)
        where = f"library IN ({placeholders}) AND instr(body, ?) > 0"
        args: list[object] = [*libraries, query]
        if len(query) >= 3:
            # Quote as one FTS phrase; embedded operators/quotes remain data.
            where += " AND docs MATCH ?"
            args.append('"' + query.replace('"', '""') + '"')
        rows = self.db.execute(
            f"SELECT id FROM docs WHERE {where} ORDER BY id LIMIT ?", [*args, limit + 1]
        ).fetchall()
        return SearchPage(tuple(row[0] for row in rows[:limit]), len(rows) > limit)


TOPICS = [
    ("违约", "约定没有履行怎么办"), ("定金", "预先交的保证款能否退回"),
    ("租金", "房屋使用费一直未付"), ("借款", "借出去的钱到期没收回"),
    ("交付", "买的货一直没送到"), ("质量", "收到的产品存在缺陷"),
    ("解除", "希望提前结束双方约定"), ("通知", "如何让对方知道我的意思"),
    ("期限", "约好完成事情的时间已过"), ("证据", "怎样证明事情经过"),
    ("代理", "别人代替本人签了字"), ("担保", "第三人承诺替债务人负责"),
    ("抵销", "双方互相欠钱怎样冲减"), ("利息", "占用资金应付的额外费用"),
    ("保管", "代为存放的物品丢了"), ("运输", "托运途中货物破损"),
    ("验收", "收到工程后如何检查确认"), ("结算", "合作结束怎样核清账目"),
    ("保密", "合作信息被擅自公开"), ("授权", "允许别人做事的范围是什么"),
]


def corpus() -> tuple[list[SourceSnapshot], list[dict]]:
    """100 explicitly synthetic docs / 60 prelabelled queries; no legal claims.

    First half of each query category is development, second half held out.
    Paraphrase/multi-doc labels are provisional semantic evaluation fixtures;
    this script never expands queries using the answer labels.
    """
    sources, questions = [], []
    for i, (term, paraphrase) in enumerate(TOPICS):
        for kind in ("case", "law", "book", "note"):
            doc_id = f"{kind}-{i:02}"
            body = (f"# 合成测试资料（非真实法律依据）\r\n"
                    f"主题：{term}；资料类型：{kind}。\r\n"
                    f"测试编号：〔虚构2026〕测字第{i + 1}号。\r\n"
                    f"核对片段：甲方说：“{term}事项，尚待核实。”\r\n")
            sources.append(SourceSnapshot("selected", doc_id, doc_id + ".md", body.encode()))
        sources.append(SourceSnapshot("unselected", f"noise-{i:02}", f"干扰{i}.md",
                                      ("合成资料：" + term + " " + paraphrase).encode()))
        query = term if i < 10 else f"〔虚构2026〕测字第{i + 1}号"
        targets = [f"{kind}-{i:02}" for kind in ("case", "law", "book", "note")]
        questions.append(dict(id=f"exact-{i}", category="exact", query=query, relevant=targets,
                              split="dev" if i < 10 else "heldout"))
        questions.append(dict(id=f"natural-{i}", category="natural", query=paraphrase,
                              relevant=targets, split="dev" if i < 10 else "heldout"))
    for i in range(10):
        questions.append(dict(id=f"multi-{i}", category="multi",
                              query=f"对比{TOPICS[i][0]}与{TOPICS[i+10][0]}的研究资料",
                              relevant=[f"note-{i:02}", f"note-{i+10:02}"],
                              split="dev" if i < 5 else "heldout"))
        questions.append(dict(id=f"absent-{i}", category="absent",
                              query=f"不存在的测试主题Ω{i}", relevant=[],
                              split="dev" if i < 5 else "heldout"))
    return sources, questions


def evaluation_corpus(suite='v1'):
    """Versioned prompts; v1 results and labels must never be silently rewritten."""
    if suite not in ('v1', 'v2'):
        raise ValueError('unknown_evaluation_suite')
    sources, questions = corpus()
    if suite == 'v2':
        for q in questions:
            if q['category'] == 'multi':
                q['query'] += '。仅比较这两个主题的笔记（note）资料，并分别引用；不要用书籍、法规或案例替代笔记'
            elif q['category'] in ('exact', 'natural'):
                q['query'] += '。请分别查阅并引用该主题的案例（case）、法规（law）、书籍（book）、笔记（note）四类合成资料'
            q['id'] = 'v2-' + q['id']
    return sources, questions


def run() -> dict:
    sources, questions = corpus()
    start = perf_counter()
    index = LiteralIndex(sources)
    indexed_ms = (perf_counter() - start) * 1000
    rows = []
    try:
        for question in questions:
            start = perf_counter()
            page = index.search(question["query"], ("selected",))
            expected = set(question["relevant"])
            found = expected.intersection(page.document_ids)
            rows.append({**question, **asdict(page), "hit": bool(found),
                         "recall": len(found) / len(expected) if expected else None,
                         "ms": round((perf_counter() - start) * 1000, 3)})
    finally:
        index.close()
    quotes = 0
    for source in sources:
        receipt = source.read(version=source.version, start=0, end=len(source.text),
                              thread_id="benchmark", turn_id="offline", max_chars=1000)
        quote = receipt.quote(start=0, end=len(source.text), thread_id="benchmark", turn_id="offline")
        assert quote.text.encode("utf-8") == source.raw
        quotes += 1
    summary = {}
    for category in ("exact", "natural", "multi", "absent"):
        subset = [r for r in rows if r["category"] == category]
        summary[category] = dict(questions=len(subset), hits=sum(r["hit"] for r in subset),
                                 empty=sum(not r["document_ids"] for r in subset))
    return dict(mode="literal_only_no_model", synthetic=True, sqlite=sqlite3.sqlite_version,
                documents=len(sources), quotes_exact=quotes, index_ms=round(indexed_ms, 3),
                summary=summary, queries=rows,
                limitation="Literal baseline only; no natural-language or production acceptance.")


if __name__ == "__main__":
    report = run()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    exact_ok = report["summary"]["exact"]["hits"] == report["summary"]["exact"]["questions"]
    absent_ok = report["summary"]["absent"]["empty"] == report["summary"]["absent"]["questions"]
    sys.exit(0 if exact_ok and absent_ok else 1)
