import asyncio
import base64
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from verilex_runtime.terminal import TerminalManager, TerminalSession, MAX_OUTPUT
from verilex_runtime.tools.registry import ToolError


@pytest.fixture
def manager(tmp_path):
    workspace = SimpleNamespace(root_path=str(tmp_path.resolve()), allow_read=True, allow_write=True)
    return TerminalManager(SimpleNamespace(get=lambda key: workspace if key == 'w' else None))


async def command(manager, sid, text):
    session = manager.get('w', sid)
    manager.write('w', sid, text + '\r', session.input_sequence + 1)
    # Wait for a unique marker emitted after the command (not its echoed input).
    token = uuid4().hex
    manager.write('w', sid, "printf '\\n%s%s\\n' " + token[:16] + ' ' + token[16:] + '\r', session.input_sequence + 1)
    for _ in range(100):
        await asyncio.sleep(.05)
        if token.encode() in session.output:
            return bytes(session.output).decode(errors='replace')
    raise AssertionError(bytes(session.output).decode(errors='replace'))


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS PTY sandbox')
async def test_real_terminal_cwd_unicode_resize_interrupt_exit(manager, tmp_path):
    sid = str(uuid4())
    try:
        created = manager.create('w', sid, 80, 24)
        assert manager.create('w', sid, 80, 24) == created  # idempotent create
        await asyncio.sleep(.2)
        output = await command(manager, sid, "mkdir 子目录; cd 子目录; printf '\\n中文输入\\n'; pwd")
        assert str(tmp_path.resolve() / '子目录') in output
        assert '中文输入' in output
        manager.resize('w', sid, 65, 17)
        assert '17 65' in await command(manager, sid, 'stty size')
        session = manager.get('w', sid)
        manager.write('w', sid, 'sleep 30\r', session.input_sequence + 1)
        await asyncio.sleep(.2)
        manager.write('w', sid, '\x03', session.input_sequence + 1)
        assert 'after-interrupt' in await command(manager, sid, 'echo after-interrupt')
        with pytest.raises(ToolError, match='输入顺序'):
            manager.write('w', sid, 'echo should-not-run\r', session.input_sequence)
        with pytest.raises(ToolError):
            await manager.output('other', sid, 0)
        manager.write('w', sid, 'exit 7\r', session.input_sequence + 1)
        await asyncio.wait_for(session.monitor, 3)
        assert session.status == 'exited' and session.exit_code == 7
        assert session.fd == -1
        assert manager.active_count() == 0
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS PTY sandbox')
async def test_real_terminal_write_boundary_network_environment_cleanup(manager, tmp_path, monkeypatch):
    monkeypatch.setenv('VERILEX_API_TOKEN', 'never-inherit-test-token')
    # tmp_path under /var/folders: sibling writes aren't implicitly allowed tmp writes.
    outside = tmp_path.parent / ('terminal-forbidden-' + uuid4().hex)
    sid = str(uuid4())
    try:
        manager.create('w', sid, 80, 24)
        await asyncio.sleep(.2)
        output = await command(manager, sid, f"echo forbidden > '{outside}'; echo allowed > inside.txt; print -r -- $VERILEX_API_TOKEN")
        assert not outside.exists()
        assert (tmp_path / 'inside.txt').read_text().strip() == 'allowed'
        assert 'never-inherit-test-token' not in output
        output = await command(manager, sid, '/usr/bin/curl --connect-timeout 1 http://127.0.0.1:9; echo network-result:$?')
        assert 'network-result:7' in output
        session = manager.get('w', sid)
        manager.write('w', sid, 'sleep 30\r', session.input_sequence + 1)
        await asyncio.sleep(.2)
        fg = os.tcgetpgrp(session.fd)
        assert manager.has_active('w')
        await manager.shutdown()
        assert session.status == 'closed' and session.fd == -1
        assert not manager.has_active('w')
        # A killed child can remain a zombie briefly until launchd reaps it.
        for _ in range(40):
            try:
                os.killpg(fg, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(.05)
        else:
            raise AssertionError('foreground process group survived shutdown')
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS PTY sandbox')
async def test_real_terminal_global_limit(manager):
    try:
        for _ in range(3):
            manager.create('w', str(uuid4()), 80, 24)
        with pytest.raises(ToolError, match='最多'):
            manager.create('w', str(uuid4()), 80, 24)
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_output_bound_and_cursor(manager, monkeypatch):
    session = TerminalSession('s', 'w', '/', 1, -1, 80, 24, status='exited')
    manager.sessions['s'] = session
    chunks = iter([b'x' * (MAX_OUTPUT + 200), b''])
    monkeypatch.setattr(os, 'read', lambda *_: next(chunks))
    manager._read_ready(session)
    result = await manager.output('w', 's', 0)
    assert len(session.output) == MAX_OUTPUT and session.offset == 200
    assert result['truncated'] and result['cursor'] == 200 + 65536
    assert base64.b64decode(result['data']) == b'x' * 65536


def test_permission_and_unsupported_fail_closed(manager, monkeypatch):
    with pytest.raises(ToolError):
        manager.create('missing', str(uuid4()), 80, 24)
    monkeypatch.setattr('verilex_runtime.terminal.sandbox_available', lambda: False)
    with pytest.raises(ToolError, match='仅支持'):
        manager.create('w', str(uuid4()), 80, 24)
    assert not manager.sessions

@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS PTY sandbox')
async def test_workspace_path_with_quotes_backslash_and_spaces(tmp_path):
    root = tmp_path / 'folder "quoted" \\ space'; root.mkdir()
    record = SimpleNamespace(root_path=str(root.resolve()), allow_read=True, allow_write=True)
    manager = TerminalManager(SimpleNamespace(get=lambda _: record))
    try:
        manager.create('w', 'special-path', 80, 24)
        await asyncio.sleep(.2)
        await command(manager, 'special-path', 'echo ok > result.txt')
        assert (root / 'result.txt').read_text() == 'ok\n'
    finally:
        await manager.shutdown()
