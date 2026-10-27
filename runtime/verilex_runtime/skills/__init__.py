"""Skill discovery and prompt rendering.

A Skill is a folder containing a ``SKILL.md`` with a small YAML frontmatter
carrying ``name`` and ``description``. Skills are *instructional* context, not
callable tools: the runtime discovers them, renders a ``## Skills`` block into
the model context, and the model chooses which to follow. The risk/permission
gate stays on the tools a Skill causes the agent to invoke, never on the Skill
itself (mirroring the reference design).
"""

from verilex_runtime.skills.loader import (
    SkillRecord,
    discover_skills,
    find_skill,
    read_skill_body,
    render_skills_block,
)
from verilex_runtime.skills.service import (
    SkillEntry,
    SkillService,
    SkillServiceError,
)

__all__ = [
    "SkillEntry",
    "SkillRecord",
    "SkillService",
    "SkillServiceError",
    "discover_skills",
    "find_skill",
    "read_skill_body",
    "render_skills_block",
]
