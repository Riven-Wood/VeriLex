from dataclasses import asdict
import json

import pytest

from verilex_runtime.knowledge.source import SourceError, SourceSnapshot


def source(raw: bytes) -> SourceSnapshot:
    return SourceSnapshot("library", "document", "合成测试.md", raw)


def read(snapshot, start=0, end=None):
    return snapshot.read(version=snapshot.version, start=start,
                         end=len(snapshot.text) if end is None else end,
                         thread_id="thread", turn_id="turn", max_chars=2000)


def quote(receipt, start, end, claimed=None):
    return receipt.quote(start=start, end=end, thread_id="thread", turn_id="turn",
                         claimed_text=claimed)


def test_real_file_read_preserves_bom_crlf_unicode_and_trailing_whitespace(tmp_path):
    raw = b"\xef\xbb\xbf" + '# 合成资料\r\n甲说：“Ａ，e\u0301😀。”  \r\n乙：完毕。\r'.encode()
    path = tmp_path / "笔记.md"
    path.write_bytes(raw)
    snapshot = source(path.read_bytes())
    result = quote(read(snapshot), 0, len(snapshot.text))
    assert result.text.encode() == raw
    assert path.read_bytes() == raw
    assert json.loads(json.dumps(asdict(result), ensure_ascii=False))["text"].encode() == raw


@pytest.mark.parametrize("changed", ["甲说：\"Ａ，完成。\"", "甲说：“A，完成。”",
                                    "甲说：“Ａ,完成。”", "甲说：“Ａ，已完成。”",
                                    "甲说：“Ａ，完成。”…", "甲说：“Ａ，完成。”\n"])
def test_model_rewriting_is_rejected(changed):
    snapshot = source('甲说：“Ａ，完成。”'.encode())
    with pytest.raises(SourceError, match="quote_text_mismatch"):
        quote(read(snapshot), 0, len(snapshot.text), changed)


def test_cannot_quote_unread_content_or_another_turn():
    snapshot = source("甲段。\n乙段。\n丙段。".encode())
    receipt = read(snapshot, 4, 7)
    assert quote(receipt, 4, 7).text == "乙段。"
    with pytest.raises(SourceError, match="quote_not_read"):
        quote(receipt, 0, 7)
    for thread, turn in [("other", "turn"), ("thread", "other")]:
        with pytest.raises(SourceError, match="receipt_scope_mismatch"):
            receipt.quote(start=4, end=7, thread_id=thread, turn_id=turn)


@pytest.mark.parametrize("start,end", [(-1, 1), (0, 99), (2, 1), (1, 1), (True, 2), (0, 2.5)])
def test_invalid_or_ambiguous_offsets_rejected(start, end):
    with pytest.raises(SourceError, match="invalid_range"):
        read(source("原文。".encode()), start, end)


def test_version_changes_even_for_newlines_and_old_quote_survives_deletion(tmp_path):
    path = tmp_path / "source.md"
    path.write_bytes(b"first\r\nsecond")
    old = source(path.read_bytes())
    receipt = read(old)
    path.write_bytes(b"first\nsecond")
    new = source(path.read_bytes())
    with pytest.raises(SourceError, match="source_changed"):
        new.read(version=old.version, start=0, end=5, thread_id="thread", turn_id="turn", max_chars=10)
    path.unlink()
    assert quote(receipt, 0, len(old.text)).text == "first\r\nsecond"


def test_mixed_newlines_and_non_bmp_character_offsets():
    snapshot = source("😀甲\r\n乙\r丙\n丁".encode())
    text = snapshot.text
    for fragment, line in [("😀甲", 1), ("乙", 2), ("丙", 3), ("丁", 4)]:
        start = text.index(fragment)
        result = quote(read(snapshot), start, start + len(fragment))
        assert (result.line_start, result.line_end, result.text) == (line, line, fragment)


def test_read_budget_and_invalid_encoding():
    with pytest.raises(SourceError, match="read_budget_exceeded"):
        snapshot = source("甲乙丙".encode())
        snapshot.read(version=snapshot.version, start=0, end=3, thread_id="t", turn_id="r", max_chars=2)
    with pytest.raises(SourceError, match="invalid_utf8"):
        source(b"\xff")
    with pytest.raises(SourceError, match="immutable_bytes_required"):
        source(bytearray(b"abc"))
