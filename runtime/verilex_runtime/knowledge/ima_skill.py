"""Bundled instruction skill, loaded only for a user-selected ima source."""
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def ima_skill_instruction():
    return Path(__file__).with_name('ima_mcp').joinpath('SKILL.md').read_text(encoding='utf-8')
