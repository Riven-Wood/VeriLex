"""合同审查批注写入底座：委托经验证的 docx 引擎，原件全程只读。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from docx import Document

from verilex_runtime.contract_review.annotator import CommentSpec, ContractAnnotator
from tests.contract_review.sample_contract import build_contract


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def contract(tmp_path: Path) -> Path:
    """虚构合同样本：结构真实，当事人与编号均为虚构。"""
    return build_contract(tmp_path / "contract.docx")


def _read(path: Path) -> list[tuple[str, str]]:
    return [(c.author, c.text) for c in Document(path).comments]


def test_three_comments_written_and_readable(contract: Path, tmp_path: Path) -> None:
    dest = tmp_path / "annotated.docx"
    ContractAnnotator().write(
        contract,
        [
            CommentSpec(
                anchor_text="百分之四十作为预付款；\n验收合格后十个工作日内支付剩余百分之六十。",
                text="【中】建议协商：付款节奏建议增加里程碑验收条件。",
                author="VeriLex",
            ),
            CommentSpec(
                anchor_text="千分之三向甲方支付违约金",
                text="【高】必须修改：违约金上限缺失。",
                author="VeriLex",
            ),
            CommentSpec(
                anchor_text="友好协商解决",
                text="【低】建议协商：仲裁机构名称建议核对全称。",
                author="VeriLex",
            ),
        ],
        dest,
    )
    comments = _read(dest)
    assert len(comments) == 3
    assert all(author == "VeriLex" for author, _ in comments)
    assert {text for _, text in comments} == {
        "【中】建议协商：付款节奏建议增加里程碑验收条件。",
        "【高】必须修改：违约金上限缺失。",
        "【低】建议协商：仲裁机构名称建议核对全称。",
    }


def test_comment_date_written(contract: Path, tmp_path: Path) -> None:
    dest = tmp_path / "annotated.docx"
    ContractAnnotator().write(
        contract,
        [CommentSpec(anchor_text="友好协商解决", text="意见", author="VeriLex")],
        dest,
    )
    timestamps = [c.timestamp for c in Document(dest).comments]
    assert len(timestamps) == 1
    assert timestamps[0] is not None


def test_anchor_inside_table_supported(contract: Path, tmp_path: Path) -> None:
    dest = tmp_path / "annotated.docx"
    ContractAnnotator().write(
        contract,
        [CommentSpec(anchor_text="120,000", text="表格金额与正文核对。", author="VeriLex")],
        dest,
    )
    assert len(_read(dest)) == 1


def test_header_anchor_rejected(contract: Path, tmp_path: Path) -> None:
    from verilex_runtime.contract_review.errors import ContractReviewError

    with pytest.raises(ContractReviewError) as caught:
        ContractAnnotator().write(
            contract,
            [CommentSpec(anchor_text="技术服务合同（演示样本）", text="意见", author="VeriLex")],
            tmp_path / "out.docx",
        )
    assert caught.value.code == "contract_annotation_write_failed"


def test_footer_anchor_rejected(contract: Path, tmp_path: Path) -> None:
    from verilex_runtime.contract_review.errors import ContractReviewError

    with pytest.raises(ContractReviewError) as caught:
        ContractAnnotator().write(
            contract,
            [CommentSpec(anchor_text="第 1 页", text="意见", author="VeriLex")],
            tmp_path / "out.docx",
        )
    assert caught.value.code == "contract_annotation_write_failed"


def test_dest_same_path_as_original_rejected(contract: Path) -> None:
    from verilex_runtime.contract_review.errors import ContractReviewError

    before = _sha(contract)
    with pytest.raises(ContractReviewError) as caught:
        ContractAnnotator().write(
            contract,
            [CommentSpec(anchor_text="友好协商解决", text="意见", author="VeriLex")],
            contract,
        )
    assert caught.value.code == "contract_annotation_write_failed"
    assert _sha(contract) == before


def test_dest_hardlink_alias_rejected(contract: Path, tmp_path: Path) -> None:
    import os

    from verilex_runtime.contract_review.errors import ContractReviewError

    alias = tmp_path / "alias.docx"
    os.link(contract, alias)
    before = _sha(contract)
    with pytest.raises(ContractReviewError) as caught:
        ContractAnnotator().write(
            contract,
            [CommentSpec(anchor_text="友好协商解决", text="意见", author="VeriLex")],
            alias,
        )
    assert caught.value.code == "contract_annotation_write_failed"
    assert _sha(contract) == before


def test_empty_fields_rejected(contract: Path, tmp_path: Path) -> None:
    from verilex_runtime.contract_review.errors import ContractReviewError

    for spec in (
        CommentSpec(anchor_text="友好协商解决", text="  ", author="VeriLex"),
        CommentSpec(anchor_text="友好协商解决", text="意见", author=""),
        CommentSpec(anchor_text="  ", text="意见", author="VeriLex"),
    ):
        with pytest.raises(ContractReviewError):
            ContractAnnotator().write(contract, [spec], tmp_path / "out.docx")


def test_failed_write_leaves_no_partial_dest(contract: Path, tmp_path: Path) -> None:
    dest = tmp_path / "out.docx"
    ContractAnnotator().write(
        contract,
        [CommentSpec(anchor_text="友好协商解决", text="意见", author="VeriLex")],
        dest,
    )
    assert dest.exists()
    assert not list(tmp_path.glob(".*.tmp"))  # 原子写入不留临时文件


def test_ambiguous_anchor_requires_occurrence(contract: Path, tmp_path: Path) -> None:
    from verilex_runtime.contract_review.errors import ContractReviewError

    annotator = ContractAnnotator()
    with pytest.raises(ContractReviewError):
        annotator.write(
            contract,
            [CommentSpec(anchor_text="示例科技有限公司", text="意见", author="VeriLex")],
            tmp_path / "out.docx",
        )
    dest = tmp_path / "disambiguated.docx"
    annotator.write(
        contract,
        [
            CommentSpec(
                anchor_text="示例科技有限公司",
                text="签署栏主体与首部核对。",
                author="VeriLex",
                occurrence=1,
            )
        ],
        dest,
    )
    assert len(_read(dest)) == 1


def test_original_sha256_unchanged(contract: Path, tmp_path: Path) -> None:
    before = _sha(contract)
    ContractAnnotator().write(
        contract,
        [CommentSpec(anchor_text="友好协商解决", text="意见", author="VeriLex")],
        tmp_path / "annotated.docx",
    )
    assert _sha(contract) == before


def test_corrupt_source_rejected(tmp_path: Path) -> None:
    from verilex_runtime.contract_review.errors import ContractReviewError

    bad = tmp_path / "broken.docx"
    bad.write_bytes(b"not a zip archive")
    with pytest.raises(ContractReviewError) as caught:
        ContractAnnotator().write(
            bad,
            [CommentSpec(anchor_text="任意", text="意见", author="VeriLex")],
            tmp_path / "out.docx",
        )
    assert caught.value.code == "contract_file_unsupported"


def test_empty_comment_list_rejected(contract: Path, tmp_path: Path) -> None:
    from verilex_runtime.contract_review.errors import ContractReviewError

    with pytest.raises(ContractReviewError):
        ContractAnnotator().write(contract, [], tmp_path / "out.docx")
