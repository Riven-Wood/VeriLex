"""Terminal tool: run validation commands inside an authorized workspace.

``run_command`` executes a single command (no shell) with stdout+stderr capture,
bounded output, timeout termination, and cooperative cancellation. It carries
``risk_level=high`` and is gated by the Agent Engine's approval gate.

Security surface:

- No shell: uses ``asyncio.create_subprocess_exec`` with argv, so shell
  metacharacters are never interpreted.
- Subprocess writes are confined by the available OS sandbox and bound-object protection.
- Workspace boundary: the subprocess ``cwd`` is the workspace root.
- Output truncation: stdout and stderr are each capped at 32 KB.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import signal
from pathlib import Path

from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools.registry import (
    ToolContext,
    ToolCategory,
    ToolDefinition,
    ToolError,
    ToolRegistry,
    ToolResult,
    ToolScope,
    ToolSource,
)
from verilex_runtime.tools.sandbox import build_sandbox_command

MAX_OUTPUT_STREAM_BYTES = 32 * 1024
DEFAULT_COMMAND_TIMEOUT = 30
TERMINATE_GRACE_SECONDS = 1
READ_CHUNK_BYTES = 4096

def _split_command(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, posix=os.name != 'nt')
    except ValueError:
        raise ToolError('invalid_command', 'Command contains an unclosed quote.') from None
    if os.name == 'nt':
        # Preserve Windows path backslashes; non-POSIX shlex retains quotes.
        tokens = [token[1:-1] if len(token) >= 2 and token[0] == token[-1]
                  and token[0] in ('"', "'") else token for token in tokens]
    if not tokens or not tokens[0]:
        raise ToolError('empty_command', 'Command must not be empty.')
    return tokens


def _workspace_root(context: ToolContext) -> Path:
    if context.root_path is None:
        raise ToolError("workspace_not_configured", "No workspace is authorized.")
    return context.root_path


def _command_available(executable: str) -> bool:
    """Whether ``executable`` resolves to an actual runnable command.

    A bare name is resolved against PATH; a path with a separator must exist and
    be executable. This is checked before wrapping with ``sandbox-exec`` so the
    ``command_not_found`` result stays accurate (the wrapper, not the command,
    would otherwise own the exec failure).
    """
    if os.sep in executable or (os.altsep and os.altsep in executable):
        return os.path.isfile(executable) and os.access(executable, os.X_OK)
    return shutil.which(executable) is not None


def _sanitized_env() -> dict[str, str]:
    """Use the shared minimal child-process environment, never runtime secrets."""
    from verilex_runtime.mcp.client import build_minimal_stdio_environment
    return build_minimal_stdio_environment({})

def _decode_stream(data: bytes, label: str, truncated: bool) -> str:
    text = data.decode("utf-8", errors="replace")
    if truncated:
        return text + f"\n…[{label} truncated at {MAX_OUTPUT_STREAM_BYTES} bytes]"
    return text


def _format_output(
    stdout: bytes,
    stderr: bytes,
    returncode: int,
    *,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
) -> str:
    parts = [f"[exit_code: {returncode}]"]
    stdout_text = _decode_stream(stdout, "stdout", stdout_truncated)
    if stdout_text.strip():
        parts.append("--- stdout ---")
        parts.append(stdout_text)
    stderr_text = _decode_stream(stderr, "stderr", stderr_truncated)
    if stderr_text.strip():
        parts.append("--- stderr ---")
        parts.append(stderr_text)
    return "\n".join(parts)


async def _read_stream_bounded(stream, limit: int) -> tuple[bytes, bool]:
    """Read up to ``limit`` bytes, then drain the rest without buffering it.

    Draining keeps the child from blocking on a full pipe while bounding the
    parent's memory to the capture limit plus one read chunk.
    """
    captured = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        if truncated:
            continue
        remaining = limit - len(captured)
        if len(chunk) <= remaining:
            captured.extend(chunk)
        else:
            captured.extend(chunk[:remaining])
            truncated = True
    return bytes(captured), truncated


async def _collect_outputs(
    proc: asyncio.subprocess.Process,
) -> tuple[bytes, bytes, bool, bool]:
    stdout_task = asyncio.ensure_future(
        _read_stream_bounded(proc.stdout, MAX_OUTPUT_STREAM_BYTES)
    )
    stderr_task = asyncio.ensure_future(
        _read_stream_bounded(proc.stderr, MAX_OUTPUT_STREAM_BYTES)
    )
    stdout, stdout_truncated = await stdout_task
    stderr, stderr_truncated = await stderr_task
    return stdout, stderr, stdout_truncated, stderr_truncated


def _signal_process_group(pid: int, sig: int) -> None:
    """Signal the process group led by ``pid``; fall back to the single process."""
    if not hasattr(os, "killpg"):
        os.kill(pid, sig)
        return
    try:
        os.killpg(pid, sig)
    except (PermissionError, OSError):
        os.kill(pid, sig)


async def _run_command(context: ToolContext, args: dict) -> ToolResult:
    root = _workspace_root(context)
    command = args["command"]
    argv = _split_command(command)
    timeout = args.get("timeout", DEFAULT_COMMAND_TIMEOUT)

    if not argv:
        return ToolResult(ok=False, output="empty_command: command must not be empty")
    if not _command_available(argv[0]):
        return ToolResult(
            ok=False, output=f"command_not_found: '{argv[0]}' is not in PATH"
        )
    exec_argv = build_sandbox_command(argv, root, protected_paths=context.protected_paths, allow_write=context.allow_write)

    try:
        proc = await asyncio.create_subprocess_exec(
            *exec_argv,
            cwd=str(root),
            env=_sanitized_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        return ToolResult(
            ok=False, output=f"command_not_found: '{argv[0]}' is not in PATH"
        )
    except PermissionError:
        return ToolResult(
            ok=False, output=f"permission_denied: cannot execute '{argv[0]}'"
        )

    collect_task = asyncio.ensure_future(_collect_outputs(proc))
    try:
        try:
            done, _pending = await asyncio.wait(
                {collect_task},
                timeout=timeout,
            )
            if collect_task not in done:
                await _terminate(proc)
                return ToolResult(
                    ok=False,
                    output=f"command_timeout: exceeded {timeout}s",
                )
            stdout, stderr, stdout_truncated, stderr_truncated = collect_task.result()
        except asyncio.CancelledError:
            await _terminate(proc)
            raise
    except ToolError:
        raise
    except Exception as error:
        return ToolResult(
            ok=False,
            output=f"command_error: {type(error).__name__}: {error}",
        )

    output = _format_output(
        stdout,
        stderr,
        proc.returncode or 0,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )
    return ToolResult(ok=(proc.returncode or 0) == 0, output=output)


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Terminate a subprocess group gracefully, then forcefully.

    The command runs in its own session/process group, so signalling the group
    also reaps any grandchildren it spawned instead of leaving them orphaned.
    """
    if hasattr(os, "killpg"):
        # A group leader can exit before its children. Its return code and
        # wait() only describe that leader, never the lifetime of the group.
        async def wait_for_group_exit() -> bool:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + TERMINATE_GRACE_SECONDS
            while True:
                try:
                    os.killpg(proc.pid, 0)
                except ProcessLookupError:
                    return True
                except PermissionError:
                    pass
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return False
                await asyncio.sleep(min(0.02, remaining))

        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        else:
            if not await wait_for_group_exit():
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await wait_for_group_exit()
        # Pipe descriptors may have been inherited by a process outside this
        # group. Never turn bounded group termination into an unbounded wait.
        try:
            await asyncio.wait_for(proc.wait(), timeout=TERMINATE_GRACE_SECONDS)
        except asyncio.TimeoutError:
            pass
        return

    if proc.returncode is not None:
        return
    try:
        _signal_process_group(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=TERMINATE_GRACE_SECONDS)
    except asyncio.TimeoutError:
        try:
            _signal_process_group(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()


async def _run_command_cancellable(
    context: ToolContext, args: dict, cancellation=None
) -> ToolResult:
    """Run a command with optional cooperative cancellation.

    When a ``cancellation`` token is provided, the tool races the subprocess
    against the cancellation future. If the Turn is cancelled mid-command, the
    subprocess is terminated and a ``cancelled`` result is returned.
    """
    root = _workspace_root(context)
    command = args["command"]
    argv = _split_command(command)
    timeout = args.get("timeout", DEFAULT_COMMAND_TIMEOUT)

    if not argv:
        return ToolResult(ok=False, output="empty_command: command must not be empty")
    if not _command_available(argv[0]):
        return ToolResult(
            ok=False, output=f"command_not_found: '{argv[0]}' is not in PATH"
        )
    exec_argv = build_sandbox_command(argv, root, protected_paths=context.protected_paths, allow_write=context.allow_write)

    try:
        proc = await asyncio.create_subprocess_exec(
            *exec_argv,
            cwd=str(root),
            env=_sanitized_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        return ToolResult(
            ok=False, output=f"command_not_found: '{argv[0]}' is not in PATH"
        )
    except PermissionError:
        return ToolResult(
            ok=False, output=f"permission_denied: cannot execute '{argv[0]}'"
        )

    collect_task = asyncio.ensure_future(_collect_outputs(proc))
    wait_set = {collect_task}
    if cancellation is not None:
        wait_set.add(cancellation.future())

    done, _pending = await asyncio.wait(
        wait_set,
        timeout=timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )

    if cancellation is not None and cancellation.cancelled:
        await _terminate(proc)
        collect_task.cancel()
        return ToolResult(ok=False, output="cancelled")

    if collect_task not in done:
        await _terminate(proc)
        collect_task.cancel()
        return ToolResult(ok=False, output=f"command_timeout: exceeded {timeout}s")

    try:
        stdout, stderr, stdout_truncated, stderr_truncated = collect_task.result()
    except Exception as error:
        return ToolResult(
            ok=False,
            output=f"command_error: {type(error).__name__}: {error}",
        )

    output = _format_output(
        stdout,
        stderr,
        proc.returncode or 0,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )
    return ToolResult(ok=(proc.returncode or 0) == 0, output=output)


def register_terminal_tools(registry: ToolRegistry) -> None:
    """Register the ``run_command`` terminal tool on a ToolRegistry."""
    registry.register(
        ToolDefinition(
            name="run_command",
            description=(
                "Run a validation command inside the authorized workspace "
                "(requires approval). Arguments are passed directly to the executable; shell expansion, pipes and redirects are not interpreted."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "Command to execute (e.g. 'pytest', "
                            "'ruff check .', 'tsc --noEmit')."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 120,
                        "description": "Timeout in seconds. Default 30.",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=_run_command,
            source=ToolSource.BUILTIN,
            category=ToolCategory.TERMINAL,
            scopes=frozenset({ToolScope.EXECUTE}),
            requires_workspace=True,
        )
    )
