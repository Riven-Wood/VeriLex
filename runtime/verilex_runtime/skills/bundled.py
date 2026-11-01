"""Install the distributed, licensed skills once without changing user skills."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .loader import discover_skills, read_skill_entry

_BUNDLED = Path(__file__).resolve().parent.parent / 'bundled_skills'
_MARKER = '.seeded-distributed-skills-v1'
_SKILL_DIRS = ('cn-law-hub', 'contract-copilot', 'patent-disclosure', 'chinese-legal-renewal-watcher', 'chinese-legal-commercial', 'chinese-legal-litigation', 'chinese-legal-docket-watcher', 'chinese-legal-diligence-grid', 'chinese-legal-corporate', 'chinese-legal-privacy', 'chinese-legal-launch-radar', 'chinese-legal-product', 'chinese-legal-ai-governance', 'chinese-legal-regulatory', 'chinese-legal-reg-monitor', 'chinese-legal-employment', 'chinese-legal-ip', 'chinese-legal-law-student', 'chinese-legal-clinic', 'chinese-legal-builder-hub', 'contract-review', 'contract-party-check-qcc', 'license-validation-qcc', 'labor-compliance-qcc', 'ip-infringement-alert-qcc', 'software-copyright-materials')


def seed_distributed_skills(skills_root: Path) -> None:
    marker = skills_root / _MARKER
    if marker.exists():
        return
    identities = {record.name.casefold() for record in discover_skills(skills_root)}
    occupied = {path.name.casefold() for path in skills_root.iterdir()}
    for directory in _SKILL_DIRS:
        source = _BUNDLED / directory
        record, _ = read_skill_entry(source / 'SKILL.md')
        if record is None:
            raise RuntimeError(f'Bundled skill is missing or invalid: {directory}')
        if record.name.casefold() in identities or directory.casefold() in occupied:
            continue
        # Build outside the scanned skills directory so incomplete copies never appear.
        with tempfile.TemporaryDirectory(prefix='.skill-import-', dir=skills_root.parent) as temporary:
            candidate = Path(temporary) / directory
            shutil.copytree(source, candidate, symlinks=False)
            target = skills_root / directory
            if target.exists() or target.is_symlink():
                continue
            os.rename(candidate, target)
        identities.add(record.name.casefold())
        occupied.add(directory.casefold())
    marker.touch(exist_ok=True)


def read_distributed_resource(record, relative_path: str) -> str:
    """Read only an unchanged text resource present in the distributed skill."""
    if not isinstance(relative_path, str) or not relative_path or '\\' in relative_path:
        raise ValueError('invalid skill resource path')
    parts = relative_path.split('/')
    if any(part in ('', '.', '..') or part.startswith('.') for part in parts):
        raise ValueError('invalid skill resource path')
    bundled = None
    for directory in _SKILL_DIRS:
        candidate = _BUNDLED / directory
        item, _ = read_skill_entry(candidate / 'SKILL.md')
        if item and item.name.casefold() == record.name.casefold():
            bundled = candidate
            break
    if bundled is None:
        raise ValueError('not a distributed skill')
    shipped = bundled.joinpath(*parts)
    actual = Path(record.source_path).parent.joinpath(*parts)
    root = Path(record.source_path).parent
    if shipped.is_symlink() or not shipped.is_file() or shipped.stat().st_size > 65536:
        raise ValueError('resource unavailable')
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('resource unavailable')
    if not actual.is_file() or actual.stat().st_size > 65536:
        raise ValueError('resource unavailable')
    expected = shipped.read_bytes()
    if actual.read_bytes() != expected:
        raise ValueError('resource changed')
    try:
        return expected.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError('resource is not text') from error
