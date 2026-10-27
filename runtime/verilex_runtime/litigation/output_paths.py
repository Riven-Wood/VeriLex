"""Keep existing workspace links usable after the output-folder rename."""
from pathlib import Path

from verilex_runtime.tools.registry import ToolError

FOLDER = '文书起草'
LEGACY_FOLDER = '工作成果'


def output_path(root: Path, relative: str) -> str:
    path = Path(relative)
    if path.is_absolute() or not path.parts or path.parts[0] not in {FOLDER, LEGACY_FOLDER}:
        return relative
    current = root / path.parts[0]
    other = root / (FOLDER if path.parts[0] == LEGACY_FOLDER else LEGACY_FOLDER)
    # Never follow a link or redirect a path whose original folder still exists.
    if not current.exists() and not current.is_symlink() and other.is_dir() and not other.is_symlink():
        return str(Path(other.name, *path.parts[1:]))
    return relative


def rename_output_folder(root: Path) -> None:
    old, new = root / LEGACY_FOLDER, root / FOLDER
    if not old.exists() and not old.is_symlink():
        return
    if old.is_symlink() or new.is_symlink():
        raise ToolError('litigation_path_invalid', '文书文件夹不能使用符号链接。')
    if not old.is_dir() or new.exists():
        raise ToolError('litigation_file_conflict', '无法将“工作成果”改名为“文书起草”：存在同名文件或文件夹，原文件均已保留。')
    try:
        old.rename(new)
    except OSError:
        raise ToolError('litigation_save_failed', '文件夹改名失败，原文件已保留，请检查文件夹权限。') from None
