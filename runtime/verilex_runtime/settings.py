"""Validated process settings for the local VeriLex runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# A single seed skill written on first run so the Skills catalog is never
# empty and the feature is discoverable. Users may add more skills in the same
# SKILL.md format under ``<state_dir>/skills``.
_DEFAULT_SKILL_MD = """---
name: legal-research
description: |
  检索中国法律条文、法规与案例，用于回答法律问题或起草法律文书。
  当用户询问法条、法规、判例或需要法律依据时使用。
---
# 法律检索

按本技能流程执行：先明确检索目标，再检索权威法源，最后给出依据与结论。
"""


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    host: str
    port: int
    state_dir: Path
    api_token: str

    def __post_init__(self) -> None:
        if self.host != "127.0.0.1":
            raise ValueError("VeriLex runtime must bind to 127.0.0.1")
        if not self.api_token:
            raise ValueError("VERILEX_API_TOKEN is required")
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be between 0 and 65535")

        resolved = Path(self.state_dir).expanduser().resolve()
        object.__setattr__(self, "state_dir", resolved)
        for directory in (
            resolved,
            resolved / "logs",
            resolved / "workspaces",
            resolved / "attachments",
            resolved / "skills",
        ):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt":
                directory.chmod(0o700)
        _seed_default_skill(resolved / "skills")
        from verilex_runtime.contract_review.skill_seed import seed_contract_review_skill

        seed_contract_review_skill(resolved / "skills")
        from verilex_runtime.skills.bundled import seed_distributed_skills

        seed_distributed_skills(resolved / "skills")


def _seed_default_skill(skills_root: Path) -> None:
    """Write the built-in seed skill exactly once per data directory.

    A ``.seeded`` marker file records that seeding already happened, so a
    user who deletes every skill never sees the seed resurrect on restart.
    The marker is a plain file, never a directory, so skill discovery skips
    it. A root that already holds skills is left alone and unmarked. A
    failure to seed is non-fatal; skills are best-effort context, not
    required for startup.
    """
    try:
        if not skills_root.is_dir():
            return
        marker = skills_root / ".seeded"
        if marker.exists():
            return
        # Symlinked directories are treated as nonexistent everywhere else;
        # they must not suppress (or fake) seeding either.
        if any(
            entry.is_dir() and not entry.is_symlink()
            for entry in skills_root.iterdir()
        ):
            return
        target = skills_root / "legal-research"
        if target.exists():
            return
        target.mkdir(mode=0o700, exist_ok=True)
        (target / "SKILL.md").write_text(_DEFAULT_SKILL_MD, encoding="utf-8")
        marker.write_text("", encoding="utf-8")
    except OSError:
        return
