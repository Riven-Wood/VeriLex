"""Skill tool: read a discovered skill's SKILL.md source.

``skill_read`` lets the model fetch the full body of a skill it saw in the
``## Skills`` catalog, mirroring Codex's on-demand skill reading. It is a
read-only, side-effect-free capability scoped to the application-level skills
root, so it is available even when no workspace is selected.

The enabled state controls automatic catalog inclusion. An explicit read may
load any valid installed skill, including one the user selected manually.
Skills remain untrusted evidence and never expand tool permissions.
"""

from __future__ import annotations

from verilex_runtime.skills import SkillService, SkillServiceError, read_skill_body
from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools.registry import (
    ApprovalBehavior,
    ToolContext,
    ToolCategory,
    ToolDefinition,
    ToolError,
    ToolRegistry,
    ToolResult,
    ToolScope,
    ToolSource,
)

_SAVE_SKILL_MODES = ("create", "update")


def _make_read_skill(service: SkillService):
    async def read_skill(context: ToolContext, args: dict) -> ToolResult:
        name = args["name"]
        skill = service.find_valid_skill(name)
        if skill is None:
            raise ToolError("skill_not_found", f"Skill '{name}' was not found.")
        return ToolResult(ok=True, output=read_skill_body(skill))

    return read_skill


def _make_save_skill(service: SkillService):
    async def save_skill(context: ToolContext, args: dict) -> ToolResult:
        name = args.get("name")
        description = args.get("description")
        instructions = args.get("instructions")
        mode = args.get("mode")
        if (
            not isinstance(name, str)
            or not isinstance(description, str)
            or not description.strip()
            or not isinstance(instructions, str)
            or not instructions.strip()
        ):
            raise ToolError(
                "skill_arguments_invalid",
                "名称、描述与正文都不能为空。",
            )
        if mode not in _SAVE_SKILL_MODES:
            raise ToolError(
                "skill_mode_invalid",
                "保存方式只支持 create（新建）或 update（修改）。",
            )
        try:
            if mode == "create":
                service.create(name, description, instructions)
            else:
                service.update(name, description, instructions)
        except SkillServiceError as error:
            # Fixed Chinese copy with a stable code, so the model can explain
            # the failure and retry with a corrected draft.
            raise ToolError(error.code, error.message) from None
        action = "created" if mode == "create" else "updated"
        return ToolResult(
            ok=True,
            output=(
                f"Skill '{name}' {action}. It is now listed in "
                "设置 → 技能 and applies to new conversations."
            ),
        )

    return save_skill


def register_skill_tools(registry: ToolRegistry, service: SkillService) -> None:
    """Register the skill tools backed by ``service``."""
    registry.register(
        ToolDefinition(
            name="skill_read",
            description=(
                "Read the full SKILL.md source of a skill from the Skills "
                "catalog. Pass the exact skill name. Use this before following "
                "a skill's instructions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The exact skill name from the Skills catalog.",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.LOW,
            execute=_make_read_skill(service),
            source=ToolSource.SKILL,
            category=ToolCategory.SKILLS,
            scopes=frozenset({ToolScope.READ}),
            requires_workspace=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="save_skill",
            description=(
                "Create or update a Skill (a SKILL.md instruction sheet). "
                "Workflow: first show the user the COMPLETE draft in your "
                "reply — name, one-line description, and the full "
                "step-by-step instructions — then call this tool to save it. "
                "The user must approve before anything is written; do not "
                "call it before showing the draft. mode='create' registers a "
                "new skill; mode='update' rewrites the description and "
                "instructions of an existing skill. The name can never be "
                "changed once created."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Skill name: Chinese characters, letters, digits, "
                            "dot, hyphen or underscore, starting with a "
                            "Chinese character, letter or digit, at most 64 "
                            "characters; no spaces or slashes. Immutable "
                            "after creation."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "One-line description (max 1000 chars) shown in "
                            "the skill catalog."
                        ),
                    },
                    "instructions": {
                        "type": "string",
                        "description": (
                            "Full step-by-step instructions (max 65536 chars)."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["create", "update"],
                        "description": "create registers a new skill; update rewrites an existing one.",
                    },
                },
                "required": ["name", "description", "instructions", "mode"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=_make_save_skill(service),
            source=ToolSource.SKILL,
            category=ToolCategory.SKILLS,
            scopes=frozenset({ToolScope.WRITE}),
            requires_workspace=False,
            approval_behavior=ApprovalBehavior.ALWAYS_HUMAN,
        )
    )
