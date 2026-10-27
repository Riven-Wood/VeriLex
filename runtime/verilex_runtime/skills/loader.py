"""Minimal SKILL.md discovery and prompt rendering.

A skill is a directory directly under a skills root. The root is scanned for
immediate subdirectories that contain a ``SKILL.md``; each such directory is a
skill. A ``SKILL.md`` must begin with a fenced or bare ``---`` frontmatter block
containing ``name`` and ``description``. The loader parses only those two fields
(no full YAML dependency) and ignores a skill whose frontmatter is malformed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKILL_FILE = "SKILL.md"

# Honest reason surfaced to the management UI when a skill directory exists
# but its SKILL.md cannot yield both ``name`` and ``description``.
SKILL_MISSING_FIELDS_MESSAGE = "SKILL.md 缺少名称或描述"

_FRONTMATTER_DELIMITER = "---"


@dataclass(frozen=True, slots=True)
class SkillRecord:
    name: str
    description: str
    source_path: str
    # Optional menu grouping marker from frontmatter (e.g. contract-review).
    category: str = ""


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Return a minimal mapping of frontmatter keys relevant to a skill.

    The first line must be ``---``; the block ends at the next ``---``. Only
    ``name`` and ``description`` are extracted, each written as ``key: value``
    (value may be a quoted or bare string). Block scalar descriptions are
    accumulated until the next key that starts a new top-level entry.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        return {}
    fields: dict[str, list[str]] = {}
    current_key: str | None = None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == _FRONTMATTER_DELIMITER:
            break
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in line and not stripped.startswith(("-",)):
            key, _, raw_value = line.partition(":")
            current_key = key.strip()
            value = raw_value.strip()
            # A bare block-scalar indicator (e.g. ``description: |``) carries
            # no inline value; the content arrives via continuation lines.
            if value in ("|", ">", "|-", ">-", "|+", ">+"):
                value = ""
            value = value.strip('"').strip("'")
            if value:
                fields.setdefault(current_key, []).append(value)
            else:
                fields.setdefault(current_key, [])
            continue
        # Continuation line for a block scalar (description |).
        if current_key is not None and stripped:
            fields[current_key].append(stripped)
    return {key: " ".join(values).strip() for key, values in fields.items() if values}


def read_skill_entry(path: Path) -> tuple[SkillRecord | None, str | None]:
    """Parse a SKILL.md, returning either a record or a failure reason.

    Unlike :func:`_read_skill`, a missing file or incomplete frontmatter is
    reported instead of silently collapsed to ``None`` so management surfaces
    can show the directory with an honest Chinese error.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, SKILL_MISSING_FIELDS_MESSAGE
    fields = _parse_frontmatter(text)
    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()
    if not name or not description:
        return None, SKILL_MISSING_FIELDS_MESSAGE
    category = fields.get("verilex-category", "").strip()
    if len(category) > 64:
        category = ""
    return (
        SkillRecord(
            name=name,
            description=description,
            source_path=str(path),
            category=category,
        ),
        None,
    )


def _read_skill(path: Path) -> SkillRecord | None:
    record, _ = read_skill_entry(path)
    return record


def discover_skills(root: Path) -> list[SkillRecord]:
    """Return every skill found directly under ``root``.

    Invalid or missing frontmatter is skipped silently so a partial skill never
    surfaces to the model as an empty catalog entry.
    """
    if root is None:
        return []
    if not root.is_dir():
        return []
    skills: list[SkillRecord] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        skill_file = entry / SKILL_FILE
        if not skill_file.is_file():
            continue
        record = _read_skill(skill_file)
        if record is not None:
            skills.append(record)
    return skills


def render_skills_block(records: list[SkillRecord]) -> str:
    """Render a ``## Skills`` context block describing each discovered skill."""
    if not records:
        return ""
    lines = ["## Skills", ""]
    lines.append(
        "A skill is a set of instructions provided through a SKILL.md source. "
        "Below is the list of skills that can be used. Each entry includes a "
        "name and description. Choose and follow a skill when the task matches "
        "its description; read its SKILL.md source before acting."
    )
    lines.append("")
    for record in records:
        lines.append(f"- **{record.name}**: {record.description}")
    lines.append("")
    return "\n".join(lines)


def find_skill(root: Path, name: str) -> SkillRecord | None:
    """Return the skill with the given name, or ``None`` if absent."""
    if not name:
        return None
    for record in discover_skills(root):
        if record.name == name:
            return record
    return None


def read_skill_body(record: SkillRecord, *, max_chars: int = 64 * 1024) -> str:
    """Return the full SKILL.md text (frontmatter + body) for a skill.

    The content is bounded so a maliciously huge skill file cannot flood the
    model context. Reads are confined to the skill file recorded by discovery.
    """
    path = Path(record.source_path)
    text = path.read_text(encoding="utf-8")
    if len(text) > max_chars:
        return text[:max_chars] + "\n…[skill truncated]"
    return text
