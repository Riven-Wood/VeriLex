"""Skill management service: validation, safe writes, trash, and enabled state.

The skills root is the source of truth: every call rescans its first-level
directories (the directory is small, so correctness beats caching). Disabled
state lives in SQLite via :class:`SkillStateRepository`; a skill with no record
is enabled.

Provenance: the name whitelist, path-escape/symlink refusal, and atomic-write
patterns are adapted from OpenWorker
(https://github.com/andrewyng/openworker) ``coworker/skills/store.py``
(MIT License). Trash handling, the disabled-state repository, honest parse
errors, and audit callbacks are VeriLex-specific.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from verilex_runtime.skills.loader import (
    SKILL_FILE,
    SkillRecord,
    read_skill_entry,
)
from verilex_runtime.storage.skill_state_repository import SkillStateRepository

# Error codes reused by the agent tool and HTTP API layers.
SKILL_NOT_FOUND = "skill_not_found"
SKILL_NAME_INVALID = "skill_name_invalid"
SKILL_NAME_CONFLICT = "skill_name_conflict"
SKILL_NAME_UNCHANGEABLE = "skill_name_unchangeable"
SKILL_TOO_LARGE = "skill_too_large"
SKILL_WRITE_FAILED = "skill_write_failed"

_MESSAGES = {
    SKILL_NOT_FOUND: "技能不存在",
    SKILL_NAME_INVALID: (
        "技能名称只能包含中文、字母、数字、点、横线和下划线，"
        "且以中文、字母或数字开头，长度不超过 64 个字符；"
        "不能包含空格或斜杠"
    ),
    SKILL_NAME_CONFLICT: "已存在同名技能（不区分大小写）",
    SKILL_NAME_UNCHANGEABLE: "技能名称创建后不可修改",
    SKILL_TOO_LARGE: "内容超出长度限制：描述不超过 1000 字，正文不超过 65536 字",
    SKILL_WRITE_FAILED: "写入失败，请重试",
}

# Name whitelist adapted from OpenWorker coworker/skills/store.py (MIT) and
# extended by VeriLex for Chinese skill names (product decision 2026-08-31):
# Han characters join letters/digits as allowed start and body characters.
# Spaces, separators, ``..`` and other filesystem-hostile input stay rejected;
# the allowed set contains no case pairs or decomposable sequences, so the
# lowercase uniqueness comparison remains sound.
_NAME_PATTERN = re.compile(r"[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9._\-\u4e00-\u9fff]*")

MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 1000
MAX_INSTRUCTIONS_CHARS = 64 * 1024


class SkillServiceError(Exception):
    """Domain error with a stable code and a fixed Chinese message."""

    def __init__(self, code: str):
        super().__init__(_MESSAGES[code])
        self.code = code
        self.message = _MESSAGES[code]


@dataclass(frozen=True, slots=True)
class SkillEntry:
    """A skill as the management surface sees it."""

    name: str
    description: str
    instructions: str
    enabled: bool
    valid: bool
    error: str | None
    path: str
    category: str = ""


@dataclass(frozen=True, slots=True)
class _ScannedDirectory:
    directory: Path
    record: SkillRecord | None
    error: str | None

    @property
    def canonical_name(self) -> str:
        return self.record.name if self.record is not None else self.directory.name

    def identities(self) -> set[str]:
        keys = {self.directory.name.strip().lower()}
        if self.record is not None:
            keys.add(self.record.name.strip().lower())
        return keys


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trash_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_bounded_text(path: Path, max_chars: int = MAX_INSTRUCTIONS_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if len(text) > max_chars:
        return text[:max_chars] + "\n…[skill truncated]"
    return text


def _render_skill_file(name: str, description: str, instructions: str) -> str:
    single_line_description = " ".join(description.splitlines())
    body = instructions if instructions.endswith("\n") else instructions + "\n"
    return (
        f"---\nname: {name}\ndescription: {single_line_description}\n---\n\n{body}"
    )


def _atomic_write(path: Path, text: str) -> None:
    temp_path = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError as error:
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise SkillServiceError(SKILL_WRITE_FAILED) from error


class SkillService:
    def __init__(
        self,
        root: Path,
        trash_root: Path,
        state: SkillStateRepository,
        on_event: Callable[[str, str], None] | None = None,
    ):
        self._root = root
        self._trash_root = trash_root
        self._state = state
        self._on_event = on_event

    # -- discovery -----------------------------------------------------

    def _scan(self) -> list[_ScannedDirectory]:
        if not self._root.is_dir():
            return []
        scanned: list[_ScannedDirectory] = []
        for entry in sorted(self._root.iterdir(), key=lambda item: item.name):
            # A symlinked directory is treated as nonexistent: never listed,
            # never editable, never a valid target.
            if entry.is_symlink() or not entry.is_dir():
                continue
            record, error = read_skill_entry(entry / SKILL_FILE)
            scanned.append(_ScannedDirectory(entry, record, error))
        return scanned

    def list_skills(self) -> list[SkillEntry]:
        disabled = self._state.disabled_names()
        entries = [
            SkillEntry(
                name=item.canonical_name,
                description=(
                    item.record.description if item.record is not None else ""
                ),
                instructions=_read_bounded_text(item.directory / SKILL_FILE),
                enabled=item.canonical_name.strip().lower() not in disabled,
                valid=item.record is not None,
                error=item.error,
                path=str(item.directory / SKILL_FILE),
                category=item.record.category if item.record is not None else "",
            )
            for item in self._scan()
        ]
        entries.sort(key=lambda entry: entry.name)
        return entries

    def effective_skills(self) -> list[SkillRecord]:
        """Valid, enabled skills — the catalog injected into new Turns."""
        disabled = self._state.disabled_names()
        records = [
            item.record
            for item in self._scan()
            if item.record is not None
            and item.record.name.strip().lower() not in disabled
        ]
        records.sort(key=lambda record: record.name)
        return records

    def find_valid_skill(self, name: str) -> SkillRecord | None:
        """Explicit per-task selection may use a valid, globally disabled skill."""
        return next((item.record for item in self._scan()
                     if item.record is not None and item.record.name == name), None)

    def skill_directory(self, name: str) -> Path:
        """Return the located skill's directory (never a symlink)."""
        return self._locate(name).directory

    @property
    def root_path(self) -> Path:
        return self._root

    def _locate(self, name: str) -> _ScannedDirectory:
        key = name.strip().lower()
        for item in self._scan():
            if key in item.identities():
                return item
        raise SkillServiceError(SKILL_NOT_FOUND)

    # -- validation ----------------------------------------------------

    def _validate_name(self, name: str) -> None:
        if (
            not isinstance(name, str)
            or not _NAME_PATTERN.fullmatch(name)
            or len(name) > MAX_NAME_CHARS
            or ".." in name
        ):
            raise SkillServiceError(SKILL_NAME_INVALID)

    def _validate_lengths(self, description: str, instructions: str) -> None:
        if (
            len(description) > MAX_DESCRIPTION_CHARS
            or len(instructions) > MAX_INSTRUCTIONS_CHARS
        ):
            raise SkillServiceError(SKILL_TOO_LARGE)

    def _ensure_no_conflict(self, name: str) -> None:
        key = name.strip().lower()
        for item in self._scan():
            if key in item.identities():
                raise SkillServiceError(SKILL_NAME_CONFLICT)

    # -- mutations -----------------------------------------------------

    def create(self, name: str, description: str, instructions: str) -> None:
        self._validate_name(name)
        self._validate_lengths(description, instructions)
        self._ensure_no_conflict(name)
        self._root.mkdir(parents=True, exist_ok=True)
        self._create_skill_directory(self._root / name)
        self._write_skill_file(name, self._root / name, description, instructions)
        self._emit("skill_created", name)

    def update(self, name: str, description: str, instructions: str) -> None:
        located = self._locate(name)
        self._validate_lengths(description, instructions)
        frontmatter_name = located.canonical_name
        self._write_skill_file(
            frontmatter_name, located.directory, description, instructions
        )
        self._emit("skill_updated", frontmatter_name)

    def set_enabled(self, name: str, enabled: bool) -> None:
        located = self._locate(name)
        canonical = located.canonical_name
        self._state.set_enabled(canonical, enabled, updated_at=_utc_now())
        self._emit("skill_enabled" if enabled else "skill_disabled", canonical)

    def delete(self, name: str) -> str:
        """Move the skill directory into the trash; return the trash folder name."""
        located = self._locate(name)
        self._trash_root.mkdir(parents=True, exist_ok=True)
        target = self._trash_target(located.directory.name)
        try:
            # A symlink that appeared between scan and move is removed as a
            # link only; its target tree is never touched.
            if located.directory.is_symlink():
                located.directory.unlink()
            else:
                os.replace(located.directory, target)
        except OSError as error:
            raise SkillServiceError(SKILL_WRITE_FAILED) from error
        self._state.forget(located.canonical_name)
        self._state.forget(located.directory.name)
        self._emit("skill_deleted", located.canonical_name)
        return target.name

    def _trash_target(self, directory_name: str) -> Path:
        base = f"{directory_name}-{_trash_timestamp()}"
        candidate = self._trash_root / base
        suffix = 0
        while candidate.exists():
            suffix += 1
            candidate = self._trash_root / f"{base}-{suffix}"
        return candidate

    def _create_skill_directory(self, skill_dir: Path) -> None:
        try:
            skill_dir.mkdir()
        except FileExistsError:
            shadow = self._symlink_shadow(skill_dir)
            if shadow is None:
                raise SkillServiceError(SKILL_NAME_CONFLICT) from None
            # A differently-cased symlink shadows the name on case-insensitive
            # file systems; the link itself (not its target) is removed.
            shadow.unlink()
            try:
                skill_dir.mkdir()
            except OSError as error:
                raise SkillServiceError(SKILL_WRITE_FAILED) from error
        except OSError as error:
            raise SkillServiceError(SKILL_WRITE_FAILED) from error

    def _symlink_shadow(self, skill_dir: Path) -> Path | None:
        key = skill_dir.name.lower()
        try:
            entries = list(self._root.iterdir())
        except OSError:
            return None
        for entry in entries:
            if entry.name.lower() == key and entry.is_symlink():
                return entry
        return None

    def _write_skill_file(
        self,
        frontmatter_name: str,
        skill_dir: Path,
        description: str,
        instructions: str,
    ) -> None:
        skill_file = skill_dir / SKILL_FILE
        root_resolved = self._root.resolve()
        if not skill_file.resolve().is_relative_to(root_resolved):
            raise SkillServiceError(SKILL_WRITE_FAILED)
        _atomic_write(skill_file, _render_skill_file(
            frontmatter_name, description, instructions
        ))

    def _emit(self, action: str, skill_name: str) -> None:
        if self._on_event is not None:
            self._on_event(action, skill_name)
