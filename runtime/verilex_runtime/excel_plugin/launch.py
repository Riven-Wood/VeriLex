"""Resolve the reserved catalog command without PATH or shell execution."""
from pathlib import Path
import sys

COMMAND = 'verilex-excel-plugin'


def resolve_excel_command(draft):
    if draft.command != COMMAND:
        return draft.command, list(draft.args)
    if draft.args or not draft.cwd or draft.env:
        raise ValueError('Excel 插件必须选择表格文件夹，不接受额外命令或环境变量。')
    root = Path(draft.cwd).resolve(strict=True)
    if not root.is_dir() or root == Path(root.anchor):
        raise ValueError('请选择专门存放表格的文件夹。')
    if getattr(sys, 'frozen', False):
        return str(Path(sys.executable).with_name('verilex-excel-plugin')), [str(root)]
    # Absolute entry avoids relying on cwd/PYTHONPATH in the minimal MCP env.
    return sys.executable, [str(Path(__file__).with_name('entry.py')), str(root)]
