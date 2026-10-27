"""SkillService 行为测试：校验、原子写、废纸篓、启停与审计。"""

import os
import re
from pathlib import Path

import pytest

from verilex_runtime.skills import SkillService, SkillServiceError
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.skill_state_repository import SkillStateRepository

SECRET_BODY_MARKER = "SECRET-BODY--do-not-audit"


def _service_env(tmp_path, events=None):
    root = tmp_path / "skills"
    trash_root = tmp_path / "skills-trash"
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    state = SkillStateRepository(database)
    on_event = (
        (lambda action, name: events.append((action, name)))
        if events is not None
        else None
    )
    service = SkillService(root, trash_root, state, on_event=on_event)
    return service, root, trash_root


def _expect_error(operation, code):
    with pytest.raises(SkillServiceError) as excinfo:
        operation()
    assert excinfo.value.code == code
    assert excinfo.value.message  # 固定中文文案必须非空


def _entries_by_name(service):
    return {entry.name: entry for entry in service.list_skills()}


def test_create_round_trips_through_list_skills(tmp_path):
    service, root, _ = _service_env(tmp_path)
    description = "检索中国法律条文\n用于回答法律问题"
    instructions = "# 使用说明\n按流程执行。"

    service.create("legal-research", description, instructions)

    entries = _entries_by_name(service)
    entry = entries["legal-research"]
    # 换行在写入前被替换为空格，保证 frontmatter 单行可被读回。
    assert entry.description == "检索中国法律条文 用于回答法律问题"
    assert "name: legal-research" in entry.instructions
    assert "# 使用说明" in entry.instructions
    assert entry.enabled is True
    assert entry.valid is True
    assert entry.error is None
    assert entry.path == str(root / "legal-research" / "SKILL.md")
    assert [record.name for record in service.effective_skills()] == [
        "legal-research"
    ]


def test_create_accepts_legal_name_characters(tmp_path):
    service, _, _ = _service_env(tmp_path)
    service.create("legal-name.2_X", "描述", "正文")
    assert "legal-name.2_X" in _entries_by_name(service)


@pytest.mark.parametrize(
    "name",
    [
        "../x",
        "a b",
        "合同 审查",
        "a" * 65,
        "a..b",
        "a/b",
        "a\\b",
        "-leading",
        ".start",
        "",
    ],
)
def test_create_rejects_invalid_names(tmp_path, name):
    service, root, _ = _service_env(tmp_path)
    _expect_error(
        lambda: service.create(name, "描述", "正文"), "skill_name_invalid"
    )
    # 校验失败不得触碰磁盘。
    assert not root.exists()


def test_create_accepts_chinese_names(tmp_path):
    service, root, _ = _service_env(tmp_path)
    service.create("合同审查", "审查合同时使用", "# 审查流程\n逐条列出风险点")

    entries = _entries_by_name(service)
    entry = entries["合同审查"]
    assert entry.valid is True
    assert entry.description == "审查合同时使用"
    assert "逐条列出风险点" in entry.instructions
    # 文件夹名即技能名，中文名在磁盘上可正常往返。
    assert (root / "合同审查" / "SKILL.md").is_file()
    assert [record.name for record in service.effective_skills()] == ["合同审查"]


def test_create_rejects_case_insensitive_duplicate(tmp_path):
    service, root, _ = _service_env(tmp_path)
    service.create("Contract", "描述", "正文")

    _expect_error(
        lambda: service.create("contract", "描述", "正文"),
        "skill_name_conflict",
    )
    # 也与现有技能的 frontmatter 名冲突（目录名不同）。
    hand_made = root / "folder-a"
    hand_made.mkdir(parents=True)
    (hand_made / "SKILL.md").write_text(
        "---\nname: front-name\ndescription: d\n---\n\n正文",
        encoding="utf-8",
    )
    _expect_error(
        lambda: service.create("FRONT-NAME", "描述", "正文"),
        "skill_name_conflict",
    )


def test_create_rejects_oversized_description_and_instructions(tmp_path):
    service, root, _ = _service_env(tmp_path)

    _expect_error(
        lambda: service.create("demo", "d" * 1001, "正文"), "skill_too_large"
    )
    assert not (root / "demo").exists()

    _expect_error(
        lambda: service.create("demo", "描述", "x" * 65537), "skill_too_large"
    )
    assert not (root / "demo").exists()

    # 边界值本身合法。
    service.create("demo", "d" * 1000, "x" * 65536)
    assert _entries_by_name(service)["demo"].valid is True


def test_atomic_write_replaces_file_without_temp_residue(tmp_path):
    service, root, _ = _service_env(tmp_path)
    service.create("demo", "第一行\n第二行", "# 原正文\n内容")

    skill_file = root / "demo" / "SKILL.md"
    assert skill_file.read_text(encoding="utf-8") == (
        "---\nname: demo\ndescription: 第一行 第二行\n---\n\n# 原正文\n内容\n"
    )
    assert not [
        item for item in (root / "demo").iterdir() if item.name.endswith(".tmp")
    ]

    service.update("demo", "新描述", "新正文")
    assert skill_file.read_text(encoding="utf-8") == (
        "---\nname: demo\ndescription: 新描述\n---\n\n新正文\n"
    )
    assert not [
        item for item in (root / "demo").iterdir() if item.name.endswith(".tmp")
    ]


def test_symlink_directories_are_treated_as_missing(tmp_path):
    service, root, _ = _service_env(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        "---\nname: linked\ndescription: d\n---\n\n正文", encoding="utf-8"
    )
    root.mkdir()
    os.symlink(outside, root / "outside-link")

    # 列表不含符号链接目录。
    assert service.list_skills() == []
    assert service.effective_skills() == []

    # 不可定位：启停与删除都视为不存在。
    _expect_error(
        lambda: service.set_enabled("OUTSIDE-LINK", False), "skill_not_found"
    )
    _expect_error(lambda: service.delete("outside-link"), "skill_not_found")

    # 大小写不敏感的同名创建不视为冲突（符号链接按“不存在”处理）。
    service.create("OUTSIDE-LINK", "描述", "正文")
    entries = _entries_by_name(service)
    assert entries["OUTSIDE-LINK"].valid is True
    assert (root / "OUTSIDE-LINK" / "SKILL.md").is_file()


def test_delete_moves_directory_to_trash_with_timestamp(tmp_path):
    service, root, trash_root = _service_env(tmp_path)
    service.create("demo", "描述", "# 原正文\n内容")

    trashed = service.delete("demo")

    assert re.match(r"^demo-\d{8}T\d{6}Z$", trashed)
    assert not (root / "demo").exists()
    trash_dir = trash_root / trashed
    assert trash_dir.is_dir()
    # 原内容完整保留，可从访达找回。
    assert "原正文" in (trash_dir / "SKILL.md").read_text(encoding="utf-8")


def test_delete_clears_disabled_state(tmp_path):
    service, root, _ = _service_env(tmp_path)
    service.create("demo", "描述", "正文")
    service.set_enabled("demo", False)

    service.delete("demo")

    # 停用记录被清理：同名重建无冲突且默认启用。
    service.create("demo", "新描述", "新正文")
    entry = _entries_by_name(service)["demo"]
    assert entry.enabled is True
    assert "demo" in [record.name for record in service.effective_skills()]


def test_delete_appends_suffix_when_target_exists(tmp_path, monkeypatch):
    service, _, trash_root = _service_env(tmp_path)
    monkeypatch.setattr(
        "verilex_runtime.skills.service._trash_timestamp",
        lambda: "20260830T121314Z",
    )
    service.create("demo", "描述", "正文")
    first = service.delete("demo")
    assert first == "demo-20260830T121314Z"

    service.create("demo", "描述", "正文")
    second = service.delete("demo")
    assert second == "demo-20260830T121314Z-1"
    assert (trash_root / first).is_dir()
    assert (trash_root / second).is_dir()


def test_set_enabled_toggles_listing_and_effective_skills(tmp_path):
    service, _, _ = _service_env(tmp_path)
    service.create("alpha", "描述 a", "正文 a")
    service.create("beta", "描述 b", "正文 b")

    service.set_enabled("ALPHA", False)

    assert [record.name for record in service.effective_skills()] == ["beta"]
    entries = _entries_by_name(service)
    assert entries["alpha"].enabled is False
    assert entries["beta"].enabled is True

    service.set_enabled("alpha", True)
    assert [record.name for record in service.effective_skills()] == [
        "alpha",
        "beta",
    ]
    assert _entries_by_name(service)["alpha"].enabled is True

    _expect_error(
        lambda: service.set_enabled("missing", True), "skill_not_found"
    )


def test_invalid_skills_report_missing_fields(tmp_path):
    service, root, _ = _service_env(tmp_path)
    (root / "no-file").mkdir(parents=True)
    (root / "broken").mkdir()
    (root / "broken" / "SKILL.md").write_text(
        "---\nname: broken\n---\n\n只有名称没有描述", encoding="utf-8"
    )

    entries = _entries_by_name(service)
    assert entries["no-file"].valid is False
    assert entries["no-file"].error == "SKILL.md 缺少名称或描述"
    assert entries["no-file"].enabled is True
    assert entries["broken"].valid is False
    assert entries["broken"].error == "SKILL.md 缺少名称或描述"
    assert service.effective_skills() == []


def test_locates_by_frontmatter_name_and_directory_name(tmp_path):
    service, root, trash_root = _service_env(tmp_path)
    (root / "folder-one").mkdir(parents=True)
    (root / "folder-one" / "SKILL.md").write_text(
        "---\nname: display-name\ndescription: d\n---\n\n正文", encoding="utf-8"
    )

    # 列表以 frontmatter 名作为条目名。
    entries = _entries_by_name(service)
    assert list(entries) == ["display-name"]
    assert entries["display-name"].valid is True

    # 经 frontmatter 名（大小写不敏感）启停。
    service.set_enabled("DISPLAY-NAME", False)
    assert _entries_by_name(service)["display-name"].enabled is False
    assert service.effective_skills() == []

    # 经目录名删除，废纸篓目录名跟随物理目录名。
    trashed = service.delete("FOLDER-ONE")
    assert trashed.startswith("folder-one-")
    assert (trash_root / trashed).is_dir()
    assert not (root / "folder-one").exists()


def test_audit_callback_receives_actions_and_names_only(tmp_path):
    events = []
    service, _, _ = _service_env(tmp_path, events)

    service.create("demo", "描述", SECRET_BODY_MARKER)
    service.set_enabled("demo", False)
    service.set_enabled("demo", True)
    service.update("demo", "描述2", SECRET_BODY_MARKER + "2")
    service.delete("demo")

    assert events == [
        ("skill_created", "demo"),
        ("skill_disabled", "demo"),
        ("skill_enabled", "demo"),
        ("skill_updated", "demo"),
        ("skill_deleted", "demo"),
    ]
    # 审计只收到 (动作, 名称)，绝不包含技能正文。
    for action, name in events:
        assert SECRET_BODY_MARKER not in action
        assert SECRET_BODY_MARKER not in name


def test_update_replaces_content_and_keeps_identity(tmp_path):
    service, _, _ = _service_env(tmp_path)
    service.create("demo", "旧描述", "旧正文")

    service.update("DEMO", "新描述", "新正文")

    entry = _entries_by_name(service)["demo"]
    assert entry.name == "demo"
    assert entry.description == "新描述"
    assert "新正文" in entry.instructions
    assert "旧正文" not in entry.instructions

    _expect_error(
        lambda: service.update("missing", "描述", "正文"), "skill_not_found"
    )
    _expect_error(
        lambda: service.update("demo", "d" * 1001, "正文"), "skill_too_large"
    )
    # 超限更新不得破坏原文件。
    assert "新正文" in _entries_by_name(service)["demo"].instructions


def test_update_repairs_invalid_skill(tmp_path):
    service, root, _ = _service_env(tmp_path)
    (root / "broken").mkdir(parents=True)
    (root / "broken" / "SKILL.md").write_text(
        "---\nname: broken\n---\n\n只有名称没有描述", encoding="utf-8"
    )

    service.update("broken", "修复后的描述", "修复后的正文")

    entry = _entries_by_name(service)["broken"]
    assert entry.valid is True
    assert entry.error is None
    assert entry.description == "修复后的描述"


def test_list_skills_on_missing_root_returns_empty(tmp_path):
    service, _, _ = _service_env(tmp_path)
    assert service.list_skills() == []
    assert service.effective_skills() == []
