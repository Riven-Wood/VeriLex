"""内置「通用合同审查」Skill 种子：落地、不复活、分类标记、中文名合规。"""
from __future__ import annotations

import re
from pathlib import Path

from verilex_runtime.contract_review.skill_seed import (
    CONTRACT_REVIEW_SKILL_MD,
    SEED_MARKER,
    SKILL_NAME,
    seed_contract_review_skill,
)
from verilex_runtime.skills.loader import discover_skills, read_skill_entry
from verilex_runtime.skills.service import _NAME_PATTERN, MAX_NAME_CHARS


def test_seed_lands_and_loader_discovers(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    assert seed_contract_review_skill(root) is True
    skill_md = root / SKILL_NAME / "SKILL.md"
    assert skill_md.is_file()
    assert skill_md.read_text(encoding="utf-8") == CONTRACT_REVIEW_SKILL_MD
    assert (root / SEED_MARKER).is_file()

    records = discover_skills(root)
    names = [record.name for record in records]
    assert SKILL_NAME in names
    record = next(r for r in records if r.name == SKILL_NAME)
    assert record.category == "contract-review"
    assert "四层审核" in record.description


def test_seed_runs_once_and_deleted_stays_deleted(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    seed_contract_review_skill(root)
    # 第二次不重复（标记已存在）。
    assert seed_contract_review_skill(root) is False
    # 用户删除后不复活。
    import shutil

    shutil.rmtree(root / SKILL_NAME)
    assert seed_contract_review_skill(root) is False
    assert not (root / SKILL_NAME).exists()


def test_existing_skill_directory_not_overwritten(tmp_path: Path):
    root = tmp_path / "skills"
    user_skill = root / SKILL_NAME
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text(
        "---\nname: 通用合同审查\ndescription: 用户自装版本\n---\n# 用户自己的\n",
        encoding="utf-8",
    )
    assert seed_contract_review_skill(root) is False
    assert "用户自装版本" in (user_skill / "SKILL.md").read_text(encoding="utf-8")
    assert (root / SEED_MARKER).is_file()


def test_chinese_skill_name_is_valid():
    assert _NAME_PATTERN.fullmatch(SKILL_NAME)
    assert len(SKILL_NAME) <= MAX_NAME_CHARS


def test_skill_text_treats_tool_context_as_untrusted():
    """Skill 文本是模型上下文：不得包含扩权、绕过核验的指令。"""
    lowered = CONTRACT_REVIEW_SKILL_MD
    for forbidden in ("忽略", "绕过", "不必核验", "直接修改合同", "改写合同正文"):
        assert forbidden not in lowered
    assert "逐字摘自合同原文" in lowered
    assert "未开启检索" in lowered


def test_frontmatter_category_parsed(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    seed_contract_review_skill(root)
    record, error = read_skill_entry(root / SKILL_NAME / "SKILL.md")
    assert error is None and record is not None
    assert record.category == "contract-review"
