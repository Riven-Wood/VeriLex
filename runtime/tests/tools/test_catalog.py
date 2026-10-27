from dataclasses import FrozenInstanceError

import pytest

import verilex_runtime.tools as tools_package
from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools.catalog import (
    ToolCapabilitySnapshot,
    list_tool_capabilities,
)
from verilex_runtime.tools.registry import (
    ApprovalBehavior,
    ToolCategory,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolScope,
    ToolSource,
    validate_arguments,
)


async def _execute(context: ToolContext, args: dict) -> ToolResult:
    del context, args
    return ToolResult(ok=True, output="ok")


def _definition(
    name: str,
    *,
    source: ToolSource,
    category: ToolCategory,
    scopes: frozenset[ToolScope],
    risk_level: RiskLevel = RiskLevel.LOW,
    source_name: str | None = None,
    approval_behavior: ApprovalBehavior = ApprovalBehavior.RISK_DEFAULT,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Description for {name}",
        parameters={"type": "object", "properties": {}},
        risk_level=risk_level,
        execute=_execute,
        source=source,
        category=category,
        scopes=scopes,
        requires_workspace=source is ToolSource.BUILTIN,
        source_name=source_name,
        approval_behavior=approval_behavior,
    )


def test_string_length_schema_is_enforced():
    schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 5,
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    assert validate_arguments(schema, {"query": ""}) == (
        "argument 'query' must contain at least 1 character(s)"
    )
    assert validate_arguments(schema, {"query": "123456"}) == (
        "argument 'query' must contain at most 5 character(s)"
    )
    assert validate_arguments(schema, {"query": "12345"}) is None


def test_string_length_counts_unicode_code_points_and_accepts_exact_boundaries():
    one_character_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 1}
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    bounded_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 2, "maxLength": 3}
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    assert validate_arguments(one_character_schema, {"query": "😀"}) is None
    assert validate_arguments(one_character_schema, {"query": "e\u0301"}) == (
        "argument 'query' must contain at most 1 character(s)"
    )
    assert validate_arguments(bounded_schema, {"query": "12"}) is None
    assert validate_arguments(bounded_schema, {"query": "123"}) is None


def test_integer_schema_rejects_boolean_values():
    schema = {
        "type": "object",
        "properties": {"count": {"type": "integer"}},
        "required": ["count"],
        "additionalProperties": False,
    }

    assert validate_arguments(schema, {"count": True}) == (
        "argument 'count' must be of type integer"
    )


def test_tool_definition_uses_risk_default_approval_behavior():
    definition = _definition(
        "run_command",
        source=ToolSource.BUILTIN,
        category=ToolCategory.TERMINAL,
        scopes=frozenset({ToolScope.EXECUTE}),
        risk_level=RiskLevel.HIGH,
    )

    assert getattr(definition, "approval_behavior", None) == "risk_default"


def test_catalog_projects_thread_first_use_without_changing_existing_default():
    registry = ToolRegistry()
    registry.register(
        _definition(
            "web_search",
            source=ToolSource.BUILTIN,
            category=ToolCategory.EXTERNAL,
            scopes=frozenset(
                {ToolScope.NETWORK, ToolScope.EXTERNAL_SERVICE}
            ),
            risk_level=RiskLevel.HIGH,
            approval_behavior=ApprovalBehavior.THREAD_FIRST_USE,
        )
    )
    registry.register(
        _definition(
            "run_command",
            source=ToolSource.BUILTIN,
            category=ToolCategory.TERMINAL,
            scopes=frozenset({ToolScope.EXECUTE}),
            risk_level=RiskLevel.HIGH,
        )
    )

    snapshots = {
        snapshot.name: snapshot
        for snapshot in list_tool_capabilities(registry)
    }

    assert getattr(snapshots["web_search"], "approval_behavior", None) is (
        ApprovalBehavior.THREAD_FIRST_USE
    )
    assert getattr(snapshots["run_command"], "approval_behavior", None) is (
        ApprovalBehavior.RISK_DEFAULT
    )


def test_thread_mcp_selection_owns_authorization_even_for_high_risk_tools():
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="mcp__law__write",
            description="Write through the selected legal server",
            parameters={"type": "object", "properties": {}},
            risk_level=RiskLevel.HIGH,
            execute=_execute,
            source=ToolSource.MCP,
            category=ToolCategory.EXTERNAL,
            scopes=frozenset({ToolScope.EXTERNAL_SERVICE}),
            source_name="Law Server",
            source_id="server-law",
            authorization_owner="thread_mcp_selection",
        )
    )
    registry.register(
        _definition(
            "run_command",
            source=ToolSource.BUILTIN,
            category=ToolCategory.TERMINAL,
            scopes=frozenset({ToolScope.EXECUTE}),
            risk_level=RiskLevel.HIGH,
        )
    )

    snapshots = {
        snapshot.name: snapshot for snapshot in list_tool_capabilities(registry)
    }

    assert snapshots["mcp__law__write"].default_approval == "automatic"
    assert snapshots["mcp__law__write"].authorization_owner == (
        "thread_mcp_selection"
    )
    assert snapshots["run_command"].default_approval == "required"
    assert snapshots["run_command"].authorization_owner == "risk_policy"


def test_catalog_projects_only_safe_metadata_in_deterministic_order():
    registry = ToolRegistry()
    registry.register(
        _definition(
            "run_command",
            source=ToolSource.BUILTIN,
            category=ToolCategory.TERMINAL,
            scopes=frozenset({ToolScope.EXECUTE}),
            risk_level=RiskLevel.HIGH,
        )
    )
    registry.register(
        _definition(
            "read_file",
            source=ToolSource.BUILTIN,
            category=ToolCategory.FILES,
            scopes=frozenset({ToolScope.READ}),
        )
    )
    registry.register(
        _definition(
            "skill_read",
            source=ToolSource.SKILL,
            category=ToolCategory.SKILLS,
            scopes=frozenset({ToolScope.READ}),
        )
    )
    mcp_definition = _definition(
        "mcp__remote__lookup",
        source=ToolSource.MCP,
        category=ToolCategory.EXTERNAL,
        scopes=frozenset({ToolScope.EXTERNAL_SERVICE}),
        source_name="remote",
    )

    snapshots = list_tool_capabilities(
        registry, mcp_definitions=(mcp_definition,)
    )

    assert isinstance(snapshots, tuple)
    assert [item.name for item in snapshots] == [
        "read_file",
        "run_command",
        "skill_read",
        "mcp__remote__lookup",
    ]
    command = next(item for item in snapshots if item.name == "run_command")
    assert command.default_approval == "required"
    assert command.availability == "available"
    assert command.availability_reason is None
    assert not hasattr(command, "execute")
    assert not hasattr(command, "parameters")


def test_catalog_retains_registry_definition_on_external_name_collision():
    registry = ToolRegistry()
    registry.register(
        _definition(
            "shared_name",
            source=ToolSource.BUILTIN,
            category=ToolCategory.FILES,
            scopes=frozenset({ToolScope.WRITE, ToolScope.READ}),
        )
    )
    external_collision = _definition(
        "shared_name",
        source=ToolSource.MCP,
        category=ToolCategory.EXTERNAL,
        scopes=frozenset({ToolScope.EXTERNAL_SERVICE}),
        risk_level=RiskLevel.HIGH,
        source_name="remote",
    )

    snapshots = list_tool_capabilities(
        registry, mcp_definitions=(external_collision,)
    )

    assert len(snapshots) == 1
    assert snapshots[0].source is ToolSource.BUILTIN
    assert snapshots[0].scopes == (ToolScope.READ, ToolScope.WRITE)
    assert snapshots[0].default_approval == "automatic"


def test_catalog_snapshot_and_package_contract_are_immutable_and_exported():
    registry = ToolRegistry()
    registry.register(
        _definition(
            "read_file",
            source=ToolSource.BUILTIN,
            category=ToolCategory.FILES,
            scopes=frozenset({ToolScope.READ}),
        )
    )
    snapshot = list_tool_capabilities(registry)[0]

    with pytest.raises(FrozenInstanceError):
        snapshot.name = "changed"

    assert tools_package.ToolCapabilitySnapshot is ToolCapabilitySnapshot
    assert tools_package.list_tool_capabilities is list_tool_capabilities
    assert tools_package.ToolSource is ToolSource
    assert tools_package.ToolCategory is ToolCategory
    assert tools_package.ToolScope is ToolScope
    assert tools_package.ApprovalBehavior is ApprovalBehavior


def test_catalog_projects_additive_artifact_document_tool_as_automatic():
    from unittest.mock import AsyncMock

    from verilex_runtime.documents.v1_service import V1DocumentService
    from verilex_runtime.tools.documents import register_document_tools

    registry = ToolRegistry()
    register_document_tools(registry, AsyncMock(spec=V1DocumentService))

    snapshot = list_tool_capabilities(registry)[0]

    assert snapshot.name == "document_create"
    assert snapshot.category is ToolCategory.DOCUMENTS
    assert snapshot.approval_behavior is ApprovalBehavior.ADDITIVE_ARTIFACT
    assert snapshot.default_approval == "automatic"


def test_builtin_tool_names_match_provider_function_name_pattern(tmp_path):
    """OpenAI-compatible providers (e.g. DeepSeek) reject tool names outside
    ``^[a-zA-Z0-9_-]+$`` with a 400, which surfaces as
    ``provider_protocol_error`` on every tool-bearing turn. Every built-in
    tool name must stay inside that pattern."""
    import re

    from verilex_runtime.skills import SkillService
    from verilex_runtime.storage.database import Database
    from verilex_runtime.storage.migrations import run_migrations
    from verilex_runtime.storage.skill_state_repository import (
        SkillStateRepository,
    )
    from verilex_runtime.tools import (
        register_background_tools,
        register_file_tools,
        register_skill_tools,
        register_terminal_tools,
    )

    registry = ToolRegistry()
    register_file_tools(registry)
    register_terminal_tools(registry)
    database = Database(tmp_path / "skills.sqlite3")
    run_migrations(database)
    register_skill_tools(
        registry,
        SkillService(
            tmp_path / "skills",
            tmp_path / "skills-trash",
            SkillStateRepository(database),
        ),
    )
    register_background_tools(registry, None, None)

    pattern = re.compile(r"^[a-zA-Z0-9_-]+$")
    offenders = [
        definition.name
        for definition in registry.definitions()
        if not pattern.match(definition.name)
    ]
    assert offenders == []
