import pytest

from verilex_runtime.skills import SkillService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.storage.skill_state_repository import SkillStateRepository
from verilex_runtime.tools import ToolRegistry, register_skill_tools
from verilex_runtime.tools import registry as tool_registry
from verilex_runtime.tools.registry import (
    ApprovalBehavior,
    ToolContext,
    ToolError,
)


def _service(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    return SkillService(
        tmp_path / "skills",
        tmp_path / "skills-trash",
        SkillStateRepository(database),
    )


def _context() -> ToolContext:
    return ToolContext(root_path=None, allow_read=True, allow_write=True)


async def _invoke(definition, arguments):
    handler = definition.execute
    return await handler(_context(), arguments)


def test_skill_read_describes_application_level_read_capability(tmp_path):
    registry = ToolRegistry()
    register_skill_tools(registry, _service(tmp_path))

    definition = registry.get("skill_read")

    assert definition is not None
    assert definition.source is tool_registry.ToolSource.SKILL
    assert definition.category is tool_registry.ToolCategory.SKILLS
    assert definition.scopes == frozenset({tool_registry.ToolScope.READ})
    assert definition.requires_workspace is False
    assert definition.source_name is None


@pytest.mark.asyncio
async def test_skill_read_returns_body_for_enabled_skill(tmp_path):
    service = _service(tmp_path)
    service.create("demo", "合同审查流程", "# 使用说明\n按步骤执行。")
    registry = ToolRegistry()
    register_skill_tools(registry, service)

    result = await _invoke(registry.get("skill_read"), {"name": "demo"})

    assert result.ok is True
    assert "按步骤执行。" in result.output


@pytest.mark.asyncio
async def test_skill_read_loads_manually_selected_disabled_skill(tmp_path):
    service = _service(tmp_path)
    service.create("demo", "描述", "正文")
    service.set_enabled("demo", False)
    registry = ToolRegistry()
    register_skill_tools(registry, service)

    result = await _invoke(registry.get("skill_read"), {"name": "demo"})
    assert result.ok is True
    assert "正文" in result.output
    assert service.effective_skills() == []


@pytest.mark.asyncio
async def test_skill_read_rejects_unknown_skill(tmp_path):
    registry = ToolRegistry()
    register_skill_tools(registry, _service(tmp_path))

    with pytest.raises(ToolError) as excinfo:
        await _invoke(registry.get("skill_read"), {"name": "missing"})

    assert excinfo.value.code == "skill_not_found"


# ---------------------------------------------------------------------------
# save_skill


def test_save_skill_is_high_risk_and_always_human(tmp_path):
    registry = ToolRegistry()
    register_skill_tools(registry, _service(tmp_path))

    definition = registry.get("save_skill")

    assert definition is not None
    assert definition.risk_level is RiskLevel.HIGH
    assert definition.approval_behavior is ApprovalBehavior.ALWAYS_HUMAN
    assert definition.requires_workspace is False


@pytest.mark.asyncio
async def test_save_skill_create_persists_draft(tmp_path):
    service = _service(tmp_path)
    registry = ToolRegistry()
    register_skill_tools(registry, service)
    definition = registry.get("save_skill")

    result = await _invoke(
        definition,
        {
            "name": "contract-review",
            "description": "审查合同时使用",
            "instructions": "# 审查流程\n1. 通读合同原文",
            "mode": "create",
        },
    )

    assert result.ok is True
    entries = [entry for entry in service.list_skills()]
    assert [entry.name for entry in entries] == ["contract-review"]
    entry = entries[0]
    assert entry.description == "审查合同时使用"
    assert "# 审查流程" in entry.instructions
    # The file is system-generated frontmatter plus the body.
    persisted = (tmp_path / "skills" / "contract-review" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert persisted.startswith("---\nname: contract-review\n")
    assert "1. 通读合同原文" in persisted


@pytest.mark.asyncio
async def test_save_skill_update_rewrites_description_and_body(tmp_path):
    service = _service(tmp_path)
    service.create("contract-review", "旧描述", "旧正文")
    registry = ToolRegistry()
    register_skill_tools(registry, service)
    definition = registry.get("save_skill")

    result = await _invoke(
        definition,
        {
            "name": "contract-review",
            "description": "新描述",
            "instructions": "# 新流程\n第 2 步改为重点核对违约条款",
            "mode": "update",
        },
    )

    assert result.ok is True
    entry = service.list_skills()[0]
    assert entry.description == "新描述"
    assert "违约条款" in entry.instructions
    assert "旧正文" not in entry.instructions


@pytest.mark.asyncio
async def test_save_skill_update_requires_existing_skill(tmp_path):
    service = _service(tmp_path)
    registry = ToolRegistry()
    register_skill_tools(registry, service)
    definition = registry.get("save_skill")

    with pytest.raises(ToolError) as excinfo:
        await _invoke(
            definition,
            {
                "name": "ghost",
                "description": "描述",
                "instructions": "正文",
                "mode": "update",
            },
        )

    assert excinfo.value.code == "skill_not_found"
    assert service.list_skills() == []


@pytest.mark.asyncio
async def test_save_skill_create_conflict_reports_service_code(tmp_path):
    service = _service(tmp_path)
    service.create("demo", "描述", "正文")
    registry = ToolRegistry()
    register_skill_tools(registry, service)
    definition = registry.get("save_skill")

    with pytest.raises(ToolError) as excinfo:
        await _invoke(
            definition,
            {
                "name": "DEMO",
                "description": "描述",
                "instructions": "正文",
                "mode": "create",
            },
        )

    assert excinfo.value.code == "skill_name_conflict"
    # 中文文案必须随错误回传，供助手向用户解释并换名重试。
    assert "同名" in excinfo.value.message


@pytest.mark.asyncio
async def test_save_skill_invalid_name_reports_service_code(tmp_path):
    registry = ToolRegistry()
    register_skill_tools(registry, _service(tmp_path))
    definition = registry.get("save_skill")

    with pytest.raises(ToolError) as excinfo:
        await _invoke(
            definition,
            {
                "name": "../x",
                "description": "描述",
                "instructions": "正文",
                "mode": "create",
            },
        )

    assert excinfo.value.code == "skill_name_invalid"


@pytest.mark.asyncio
async def test_save_skill_oversized_draft_reports_service_code(tmp_path):
    registry = ToolRegistry()
    register_skill_tools(registry, _service(tmp_path))
    definition = registry.get("save_skill")

    with pytest.raises(ToolError) as excinfo:
        await _invoke(
            definition,
            {
                "name": "demo",
                "description": "长" * 1001,
                "instructions": "正文",
                "mode": "create",
            },
        )

    assert excinfo.value.code == "skill_too_large"
    assert not (tmp_path / "skills" / "demo").exists()


@pytest.mark.asyncio
async def test_save_skill_rejects_unknown_mode(tmp_path):
    registry = ToolRegistry()
    register_skill_tools(registry, _service(tmp_path))
    definition = registry.get("save_skill")

    with pytest.raises(ToolError) as excinfo:
        await _invoke(
            definition,
            {
                "name": "demo",
                "description": "描述",
                "instructions": "正文",
                "mode": "rename",
            },
        )

    assert excinfo.value.code == "skill_mode_invalid"
    assert not (tmp_path / "skills").exists()


@pytest.mark.asyncio
async def test_save_skill_rejects_missing_fields(tmp_path):
    registry = ToolRegistry()
    register_skill_tools(registry, _service(tmp_path))
    definition = registry.get("save_skill")

    with pytest.raises(ToolError) as excinfo:
        await _invoke(definition, {"name": "demo", "mode": "create"})

    assert excinfo.value.code == "skill_arguments_invalid"
    assert not (tmp_path / "skills").exists()

@pytest.mark.parametrize("scene", ["legal-retrieval", "case-retrieval", "litigation-work"])
@pytest.mark.asyncio
async def test_manual_skill_read_is_available_in_every_workbench(tmp_path, scene):
    service = _service(tmp_path)
    service.create("chosen", "检索", "本次采用的步骤")
    service.set_enabled("chosen", False)
    registry = ToolRegistry()
    register_skill_tools(registry, service)
    context = ToolContext(root_path=None, allow_read=True, allow_write=False, thread_id=scene)
    result = await registry.get("skill_read").execute(context, {"name": "chosen"})
    assert "本次采用的步骤" in result.output
    assert service.effective_skills() == []
