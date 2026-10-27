import asyncio
import sys

import pytest

from verilex_runtime.agent.cancellation import TurnCancellation
from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools import ToolContext, ToolError, ToolRegistry
from verilex_runtime.tools import registry as tool_registry
from verilex_runtime.tools.terminal import (
    register_terminal_tools,
)

MAX_OUTPUT_STREAM_BYTES = 32 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize('cancellable', [False, True])
async def test_command_reads_quoted_filename_with_spaces(ctx, root, cancellable):
    from verilex_runtime.tools.terminal import _run_command, _run_command_cancellable
    (root / 'my document.txt').write_text('synthetic quoted path')
    run = _run_command_cancellable if cancellable else _run_command
    result = await run(ctx, {'command': '/bin/cat "my document.txt"'})
    assert result.ok and 'synthetic quoted path' in result.output


def test_malformed_quote_is_a_stable_tool_error():
    from verilex_runtime.tools.terminal import _split_command
    with pytest.raises(ToolError) as error:
        _split_command('cat "unclosed')
    assert error.value.code == 'invalid_command'


@pytest.fixture
def root(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


@pytest.fixture
def ctx(root):
    return ToolContext(root_path=root, allow_read=True, allow_write=True)


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_terminal_tools(reg)
    return reg


def _definition(registry, name="run_command"):
    return registry.get(name)


def test_run_command_registered_with_high_risk(registry):
    definition = _definition(registry)
    assert definition is not None
    assert definition.risk_level is RiskLevel.HIGH
    assert definition.source is tool_registry.ToolSource.BUILTIN
    assert definition.category is tool_registry.ToolCategory.TERMINAL
    assert definition.scopes == frozenset({tool_registry.ToolScope.EXECUTE})
    assert definition.requires_workspace is True
    assert definition.source_name is None


def test_reject_empty_command():
    with pytest.raises(ToolError) as captured:
        __import__("verilex_runtime.tools.terminal", fromlist=["_split_command"])._split_command("   ")
    assert captured.value.code == "empty_command"


@pytest.mark.asyncio
async def test_run_command_executes_echo(ctx, registry):
    definition = _definition(registry)
    result = await definition.execute(ctx, {"command": "echo hello"})
    assert result.ok is True
    assert "hello" in result.output
    assert "exit_code: 0" in result.output


@pytest.mark.asyncio
async def test_run_command_captures_stderr(ctx, registry, root):
    definition = _definition(registry)
    # Write a script that outputs to stderr, avoiding shell metacharacters
    script = root / "stderr_script.py"
    script.write_text(
        "import sys\nsys.stderr.write('error msg')\n", encoding="utf-8"
    )
    result = await definition.execute(
        ctx,
        {"command": f"{sys.executable} stderr_script.py"},
    )
    assert "error msg" in result.output


@pytest.mark.asyncio
async def test_run_command_returns_nonzero_exit_code(ctx, registry, root):
    definition = _definition(registry)
    script = root / "fail_script.py"
    script.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    result = await definition.execute(
        ctx,
        {"command": f"{sys.executable} fail_script.py"},
    )
    assert result.ok is False
    assert "exit_code: 1" in result.output


@pytest.mark.asyncio
async def test_run_command_not_found(ctx, registry):
    definition = _definition(registry)
    result = await definition.execute(ctx, {"command": "nonexistent-tool-xyz"})
    assert result.ok is False
    assert "command_not_found" in result.output


@pytest.mark.asyncio
async def test_run_command_timeout(ctx, registry, root):
    definition = _definition(registry)
    script = root / "sleep_script.py"
    script.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    result = await definition.execute(
        ctx,
        {"command": f"{sys.executable} sleep_script.py", "timeout": 1},
    )
    assert result.ok is False
    assert "command_timeout" in result.output


@pytest.mark.asyncio
async def test_run_command_output_truncation(ctx, registry, root):
    definition = _definition(registry)
    script = root / "big_output.py"
    script.write_text(
        f"print('x' * {MAX_OUTPUT_STREAM_BYTES + 1000})\n", encoding="utf-8"
    )
    result = await definition.execute(
        ctx,
        {"command": f"{sys.executable} big_output.py"},
    )
    assert "truncated" in result.output
    assert len(result.output) < MAX_OUTPUT_STREAM_BYTES + 1000


@pytest.mark.asyncio
async def test_run_command_does_not_interpret_shell_syntax(ctx, registry):
    result = await _definition(registry).execute(ctx, {"command": "echo hello; echo fixture"})
    assert result.ok and "hello; echo fixture" in result.output


@pytest.mark.asyncio
async def test_run_command_no_workspace(registry):
    definition = _definition(registry)
    empty_ctx = ToolContext(root_path=None, allow_read=False, allow_write=False)
    with pytest.raises(ToolError) as captured:
        await definition.execute(empty_ctx, {"command": "echo hello"})
    assert captured.value.code == "workspace_not_configured"


@pytest.mark.asyncio
async def test_run_command_uses_workspace_cwd(ctx, registry, root):
    (root / "marker.txt").write_text("found", encoding="utf-8")
    definition = _definition(registry)
    # List current directory - should see marker.txt
    if sys.platform == "win32":
        result = await definition.execute(ctx, {"command": "dir /b"})
    else:
        result = await definition.execute(ctx, {"command": "ls"})
    assert "marker.txt" in result.output


@pytest.mark.asyncio
async def test_run_command_cancellable_terminates_subprocess(ctx, registry, root):
    from verilex_runtime.tools.terminal import _run_command_cancellable

    cancellation = TurnCancellation()
    script = root / "long_sleep.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    task = asyncio.create_task(
        _run_command_cancellable(
            ctx,
            {"command": f"{sys.executable} long_sleep.py", "timeout": 30},
            cancellation,
        )
    )
    await asyncio.sleep(0.5)
    cancellation.cancel()
    result = await task
    assert result.ok is False
    assert "cancelled" in result.output


@pytest.mark.asyncio
async def test_run_command_cancellable_completes_normally(ctx, registry):
    from verilex_runtime.tools.terminal import _run_command_cancellable

    cancellation = TurnCancellation()
    result = await _run_command_cancellable(
        ctx,
        {"command": "echo hello"},
        cancellation,
    )
    assert result.ok is True
    assert "hello" in result.output


def test_registry_validation_for_run_command(registry):
    assert registry.validate("run_command", {"command": "echo hello"}) is None
    assert "missing required argument" in registry.validate("run_command", {})
    assert "must be of type string" in registry.validate("run_command", {"command": 123})
    assert "must be <= 120" in registry.validate(
        "run_command", {"command": "echo", "timeout": 999}
    )


@pytest.mark.asyncio
async def test_run_command_does_not_leak_runtime_credentials_into_subprocess(
    ctx, monkeypatch
):
    """The subprocess environment must not carry runtime or credential vars."""
    from verilex_runtime.tools.terminal import _run_command

    monkeypatch.setenv("VERILEX_API_TOKEN", "LEAKED-TOKEN-12345")
    monkeypatch.setenv("MY_SECRET_KEY", "LEAKED-SECRET-67890")
    result = await _run_command(ctx, {"command": "env"})
    assert "LEAKED-TOKEN-12345" not in result.output
    assert "LEAKED-SECRET-67890" not in result.output
    # Ordinary variables the command needs must survive the scrub.
    assert "PATH" in result.output


@pytest.mark.asyncio
async def test_run_command_output_peak_memory_is_bounded(ctx):
    """Peak parent memory must not scale with the raw output size."""
    import tracemalloc

    from verilex_runtime.tools.terminal import _run_command

    tracemalloc.start()
    try:
        script = ctx.root_path / "synthetic_output.py"
        script.write_text("import sys\nfor _ in range(1024): sys.stdout.write('0' * 8192)\n")
        result = await _run_command(ctx, {"command": f"{sys.executable} synthetic_output.py"})
    finally:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    assert "truncated" in result.output
    assert peak < 4 * 1024 * 1024


@pytest.mark.asyncio
async def test_run_command_terminate_reaps_grandchild_processes(tmp_path):
    """Terminating a command must not leave orphaned grandchildren."""
    import os
    import signal

    from verilex_runtime.tools.terminal import _terminate

    parent_code = (
        "import subprocess, time\n"
        "c = subprocess.Popen(['sleep', '60'])\n"
        "print(c.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_code,
        cwd=tmp_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    grandchild_pid = None
    try:
        first_line = await proc.stdout.readline()
        grandchild_pid = int(first_line.decode().strip())

        await _terminate(proc)

        try:
            os.kill(grandchild_pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        assert alive is False
    finally:
        if grandchild_pid is not None:
            try:
                os.kill(grandchild_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if proc.returncode is None:
            proc.kill()


@pytest.mark.asyncio
@pytest.mark.parametrize('parent_exits_first', [True, False])
async def test_stop_reaps_child_even_when_group_leader_has_exited(tmp_path, parent_exits_first):
    import os, signal
    from contextlib import suppress
    from verilex_runtime.tools.terminal import _terminate
    if not hasattr(os, 'killpg'):
        pytest.skip('POSIX process groups')
    marker = tmp_path / 'child-ready'
    child_code = (
        'import signal,time,pathlib; '
        'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
        f'pathlib.Path({str(marker)!r}).write_text("ready"); time.sleep(30)'
    )
    parent_code = (
        'import subprocess,sys,time; '
        f'p=subprocess.Popen([sys.executable,"-c",{child_code!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); '
        'print(p.pid,flush=True); ' + ('sys.exit(0)' if parent_exits_first else 'time.sleep(30)')
    )
    proc = await asyncio.create_subprocess_exec(sys.executable, '-c', parent_code, cwd=tmp_path,
        stdout=asyncio.subprocess.PIPE, start_new_session=True)
    child_pid = int(await proc.stdout.readline())
    try:
        for _ in range(200):
            if marker.exists():break
            await asyncio.sleep(.01)
        assert marker.exists()
        if parent_exits_first: await asyncio.wait_for(proc.wait(), 2)
        await asyncio.wait_for(_terminate(proc), 3)
        for _ in range(200):
            try: os.kill(child_pid, 0)
            except ProcessLookupError: break
            await asyncio.sleep(.01)
        else: pytest.fail('child is still running after stop')
    finally:
        with suppress(ProcessLookupError): os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()

@pytest.mark.asyncio
@pytest.mark.parametrize('cancellable', [False, True])
async def test_literal_punctuation_is_passed_as_argv_not_shell(ctx, root, cancellable):
    from verilex_runtime.tools.terminal import _run_command, _run_command_cancellable
    name = '合同(修订)&附件.txt'
    (root / name).write_text('已读原文')
    run = _run_command_cancellable if cancellable else _run_command
    assert '已读原文' in (await run(ctx, {'command': f'/bin/cat "{name}"'})).output
    result = await run(ctx, {'command': '/bin/echo "$(touch injected)" > redirected'})
    assert result.ok and '$(touch injected) > redirected' in result.output
    assert not (root/'injected').exists() and not (root/'redirected').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('cancellable', [False, True])
async def test_command_name_is_not_a_semantic_blocklist(ctx, root, cancellable):
    from verilex_runtime.tools.terminal import _run_command, _run_command_cancellable
    target = root / 'helper.txt'; target.write_text('safe')
    run = _run_command_cancellable if cancellable else _run_command
    result = await run(ctx, {'command':'chmod u+x helper.txt'})
    assert result.ok
    assert target.stat().st_mode & 0o100


@pytest.mark.asyncio
async def test_command_sandbox_protects_source_but_allows_new_output(ctx, root):
    import shutil
    from dataclasses import replace
    from verilex_runtime.tools.terminal import _run_command
    if shutil.which('sandbox-exec') is None: pytest.skip('native sandbox unavailable')
    source = root / 'source.txt'; source.write_text('original')
    (root / 'alias.txt').symlink_to(source)
    protected = replace(ctx, protected_paths=(source,))
    for command in ['rm source.txt', 'sh -c "echo changed > alias.txt"', 'ln source.txt hardlink.txt']:
        result = await _run_command(protected, {'command':command})
        assert not result.ok
        assert source.read_text() == 'original'
    result = await _run_command(protected, {'command':'sh -c "echo new > output.txt"'})
    assert result.ok and (root/'output.txt').read_text().strip() == 'new'


@pytest.mark.asyncio
async def test_sandbox_refuses_preexisting_hardlink_to_protected_object(ctx, root):
    import os
    from dataclasses import replace
    from verilex_runtime.tools.terminal import _run_command
    from verilex_runtime.tools.sandbox import sandbox_available
    if not sandbox_available():
        pytest.skip('native sandbox unavailable')
    source = root / 'source.txt'
    source.write_text('synthetic original')
    os.link(source, root / 'alias.txt')
    with pytest.raises(ToolError) as error:
        await _run_command(replace(ctx, protected_paths=(source,)),
                           {'command': 'sh -c "echo changed > alias.txt"'})
    assert error.value.code == 'protected_source'
    assert source.read_text() == 'synthetic original'
