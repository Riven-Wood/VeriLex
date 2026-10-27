from pathlib import Path

from verilex_runtime.skills import (
    SkillRecord,
    discover_skills,
    find_skill,
    read_skill_body,
    render_skills_block,
)

VALID_SKILL = """---
name: legal-research
description: |
  检索中国法律条文与案例，用于回答法律问题或起草文书。
  当用户询问法条、法规或判例时使用。
---
# 使用说明
按 SKILL.md 正文执行法律检索流程。
"""


def _write_skill(root: Path, name: str, content: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def test_discover_reads_name_and_description(tmp_path):
    _write_skill(tmp_path, "legal-research", VALID_SKILL)
    records = discover_skills(tmp_path)
    assert len(records) == 1
    assert records[0].name == "legal-research"
    assert "检索中国法律条文" in records[0].description
    assert records[0].source_path == str(tmp_path / "legal-research" / "SKILL.md")


def test_discover_returns_empty_for_missing_root(tmp_path):
    assert discover_skills(tmp_path / "does-not-exist") == []


def test_discover_returns_empty_for_empty_dir(tmp_path):
    assert discover_skills(tmp_path) == []


def test_discover_skips_skill_without_frontmatter(tmp_path):
    _write_skill(tmp_path, "no-frontmatter", "# Just a heading\nno frontmatter here")
    _write_skill(tmp_path, "valid", VALID_SKILL)
    records = discover_skills(tmp_path)
    # The record's name comes from SKILL.md frontmatter, not the directory name.
    assert [record.name for record in records] == ["legal-research"]


def test_discover_skips_subdirectory_without_skill_md(tmp_path):
    (tmp_path / "empty").mkdir()
    _write_skill(tmp_path, "valid", VALID_SKILL)
    records = discover_skills(tmp_path)
    assert [record.name for record in records] == ["legal-research"]


def test_render_skills_block_lists_each_skill():
    records = [
        SkillRecord(name="alpha", description="Alpha helper.", source_path="/a"),
        SkillRecord(name="beta", description="Beta helper.", source_path="/b"),
    ]
    block = render_skills_block(records)
    assert "## Skills" in block
    assert "- **alpha**: Alpha helper." in block
    assert "- **beta**: Beta helper." in block


def test_render_skills_block_empty_returns_empty_string():
    assert render_skills_block([]) == ""


def test_seed_default_skill_writes_once_and_is_idempotent(tmp_path):
    from verilex_runtime.settings import _seed_default_skill

    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _seed_default_skill(skills_root)

    seed_file = skills_root / "legal-research" / "SKILL.md"
    assert seed_file.is_file()
    first_content = seed_file.read_text(encoding="utf-8")
    assert "legal-research" in first_content

    # Seeding again must not overwrite a user-edited skill.
    seed_file.write_text("user edited", encoding="utf-8")
    _seed_default_skill(skills_root)
    assert seed_file.read_text(encoding="utf-8") == "user edited"


def test_seed_default_skill_writes_marker_file(tmp_path):
    from verilex_runtime.settings import _seed_default_skill

    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _seed_default_skill(skills_root)

    # The marker is a plain file: discovery never treats it as a skill.
    assert (skills_root / ".seeded").is_file()
    records = discover_skills(skills_root)
    assert [record.name for record in records] == ["legal-research"]


def test_seed_default_skill_does_not_resurrect_after_deletion(tmp_path):
    import shutil

    from verilex_runtime.settings import _seed_default_skill

    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _seed_default_skill(skills_root)

    # The user deletes every skill; the next startup must keep the list empty.
    shutil.rmtree(skills_root / "legal-research")
    _seed_default_skill(skills_root)

    assert discover_skills(skills_root) == []
    assert not (skills_root / "legal-research").exists()


def test_seed_default_skill_respects_bare_marker(tmp_path):
    from verilex_runtime.settings import _seed_default_skill

    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    (skills_root / ".seeded").write_text("", encoding="utf-8")

    _seed_default_skill(skills_root)

    assert not (skills_root / "legal-research").exists()


def test_seed_default_skill_skips_existing_skills_without_marker(tmp_path):
    from verilex_runtime.settings import _seed_default_skill

    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "custom", VALID_SKILL)

    _seed_default_skill(skills_root)

    # A root that already holds skills is left alone and unmarked.
    assert not (skills_root / "legal-research").exists()
    assert not (skills_root / ".seeded").exists()


def test_find_skill_matches_by_name(tmp_path):
    _write_skill(tmp_path, "legal-research", VALID_SKILL)
    skill = find_skill(tmp_path, "legal-research")
    assert skill is not None
    assert skill.name == "legal-research"
    assert find_skill(tmp_path, "nope") is None


def test_read_skill_body_returns_frontmatter_and_body(tmp_path):
    _write_skill(tmp_path, "legal-research", VALID_SKILL)
    skill = find_skill(tmp_path, "legal-research")
    assert skill is not None
    body = read_skill_body(skill)
    assert "name: legal-research" in body
    assert "执行法律检索流程" in body


def test_find_skill_handles_empty_name(tmp_path):
    assert find_skill(tmp_path, "") is None
