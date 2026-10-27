"""One-level workspace browsing, with bounded enumeration and no link traversal.

Uses the same authorization/hidden-path policy as the litigation workbench,
without its case-specific output filters. No contents are read by a listing.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat

from verilex_runtime.tools.registry import ToolError

SCAN_LIMIT = 10_000
PAGE_SIZE = 200


@contextmanager
def directory_handle(root: Path, relative: str):
    path = Path(relative)
    if (path.is_absolute() or '..' in path.parts or '\x00' in relative
            or any(p.startswith('.') for p in path.parts)):
        raise ToolError('workspace_path_invalid', '只能浏览工作区内的普通文件夹。')
    if not hasattr(os, 'O_NOFOLLOW') or os.open not in os.supports_dir_fd:
        raise ToolError('workspace_browse_unsupported', '此系统暂不支持安全浏览工作区。')
    # Open each component relative to the preceding descriptor. Even a folder
    # replaced with a symlink between validation and opening cannot escape.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = None
    try:
        fd = os.open('/', flags)
        for part in (*root.absolute().parts[1:], *path.parts):
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    except OSError:
        raise ToolError('workspace_folder_unreadable', '无法读取文件夹：目录可能已移动、无权访问或包含符号链接。') from None
    finally:
        if fd is not None:
            os.close(fd)


def _stamp(fd: int) -> str:
    info = os.fstat(fd)
    return hashlib.sha256(f'{info.st_dev}:{info.st_ino}:{info.st_mtime_ns}:{info.st_ctime_ns}'.encode()).hexdigest()[:24]


def list_workspace_files(root: Path, relative: str = '', offset: int = 0, snapshot: str = '') -> dict:
    if offset < 0 or offset > SCAN_LIMIT or (offset and not snapshot):
        raise ToolError('workspace_page_invalid', '文件列表已变化，请刷新后重新浏览。')
    entries = []
    truncated = False
    with directory_handle(root, relative) as fd:
        before = _stamp(fd)
        if snapshot and snapshot != before:
            raise ToolError('workspace_files_changed', '文件夹已有变化，请刷新列表。')
        with os.scandir(fd) as children:
            for index, child in enumerate(children):
                if index >= SCAN_LIMIT:
                    truncated = True
                    break
                if child.name.startswith('.') or child.name == 'node_modules':
                    continue
                try:
                    info = child.stat(follow_symlinks=False)
                except OSError:
                    continue
                directory = stat.S_ISDIR(info.st_mode)
                if not directory and not stat.S_ISREG(info.st_mode):
                    continue
                # POSIX names can contain undecodable bytes. JSON cannot safely
                # transport those paths, so omit them rather than misaddress them.
                try:
                    child.name.encode('utf-8')
                except UnicodeError:
                    continue
                entries.append({'name': child.name, 'path': (Path(relative) / child.name).as_posix(), 'directory': directory})
        if before != _stamp(fd):
            raise ToolError('workspace_files_changed', '文件夹已有变化，请刷新列表。')
    entries.sort(key=lambda item: (not item['directory'], item['name'].casefold(), item['name']))
    end = offset + PAGE_SIZE
    return {'entries': entries[offset:end], 'snapshot': before,
            'next_offset': end if end < len(entries) else None,
            'truncated': truncated, 'path': Path(relative).as_posix() if relative else ''}
