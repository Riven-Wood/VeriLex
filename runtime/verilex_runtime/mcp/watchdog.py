"""Parent-death watchdog for stdio MCP servers.

This is adapted from Hermes Agent's ``tools/mcp_stdio_watchdog.py`` at commit
5cc47c994beb243407bb4c8ba47d2ab421cda9cf under the MIT license (Copyright
2025 Nous Research). VeriLex uses a shorter shutdown budget, validates the
recorded parent before spawn, and rebuilds the child's environment from an
allowlist.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence


_POLL_INTERVAL_SECONDS = 0.05
_DEFAULT_GRACE_SECONDS = 0.5
_MAX_GRACE_SECONDS = 1.0
_SAFE_ENVIRONMENT_NAMES = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")


def _grandparent_pid(bootloader_pid: int) -> int | None:
    """Return the parent pid of *bootloader_pid* (macOS has no /proc)."""
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(bootloader_pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _parent_identity_matches(parent_pid: int) -> bool:
    """Whether *parent_pid* is this watchdog's recorded owner.

    Source runs are direct children of the runtime process. A PyInstaller
    ``--onefile`` build re-execs this CLI one hop below a bootloader process,
    so in frozen builds the recorded parent appears as the bootloader's own
    parent; accept exactly that hop and nothing wider.
    """
    if parent_pid <= 0:
        return False
    ppid = os.getppid()
    if ppid == parent_pid:
        return True
    if not getattr(sys, "frozen", False):
        return False
    return _grandparent_pid(ppid) == parent_pid


def _parent_heartbeat(parent_pid: int) -> bool:
    if parent_pid <= 0:
        return False
    try:
        os.kill(parent_pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def parent_is_alive(parent_pid: int) -> bool:
    """Return whether *parent_pid* is still this watchdog's live owner."""

    return _parent_identity_matches(parent_pid) and _parent_heartbeat(parent_pid)


def _child_environment(
    explicit_names: Sequence[str],
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    permitted = set(_SAFE_ENVIRONMENT_NAMES)
    permitted.update(name for name in explicit_names if name)
    return {name: source[name] for name in permitted if name in source}


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _terminate_owned_process_group(
    process_group_id: int,
    grace_seconds: float,
) -> None:
    """Terminate a process group whose independent-session ownership was proven."""

    if process_group_id <= 0:
        return
    try:
        if os.getpgid(process_group_id) != process_group_id:
            return
    except (ProcessLookupError, PermissionError, OSError):
        return
    _terminate_proven_process_group(process_group_id, grace_seconds)


def _terminate_proven_process_group(
    process_group_id: int,
    grace_seconds: float,
) -> None:
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not _process_group_exists(process_group_id):
            return
        time.sleep(min(_POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))

    try:
        os.killpg(
            process_group_id,
            getattr(signal, "SIGKILL", signal.SIGTERM),
        )
    except (ProcessLookupError, PermissionError, OSError):
        pass


def watch_parent_and_terminate_group(
    parent_pid: int,
    process_group_id: int,
    grace_seconds: float,
) -> None:
    """Watch the direct parent and terminate a proven child session on loss."""

    if os.name != "posix" or parent_pid <= 0 or process_group_id <= 0:
        return
    # ``start_new_session=True`` makes the child's PID its PGID. Prove that
    # ownership once while the leader exists; later the leader may exit while
    # a stubborn descendant still occupies the recorded group.
    try:
        if os.getpgid(process_group_id) != process_group_id:
            return
    except (ProcessLookupError, PermissionError, OSError):
        return
    # Identity was proven once at startup; poll only the cheap heartbeat.
    while _parent_heartbeat(parent_pid):
        time.sleep(_POLL_INTERVAL_SECONDS)
    _terminate_owned_process_group(process_group_id, grace_seconds)


def run_mcp_watchdog(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parent-death watchdog for a stdio MCP subprocess.",
    )
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument(
        "--grace-seconds",
        type=float,
        default=_DEFAULT_GRACE_SECONDS,
    )
    parser.add_argument("--env-key", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args(arguments)

    command = list(options.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("mcp watchdog: no command given after '--'", file=sys.stderr)
        return 2
    if not 0 <= options.grace_seconds <= _MAX_GRACE_SECONDS:
        print("mcp watchdog: grace must be between 0 and 1 second", file=sys.stderr)
        return 2
    if os.name != "posix" or not hasattr(os, "waitid"):
        # Windows stdio uses the MCP SDK's Job Object lifecycle directly; this
        # POSIX process-group wrapper must fail before spawning anything.
        print("mcp watchdog: POSIX process groups are unavailable", file=sys.stderr)
        return 2
    # Fail closed before starting an unowned server. Identity is proven once
    # here; the watcher thread then polls only the heartbeat.
    if not _parent_identity_matches(options.parent_pid) or not _parent_heartbeat(
        options.parent_pid
    ):
        return 0

    process: subprocess.Popen[bytes] | None = None
    owned_process_group: int | None = None
    def forward_shutdown(signum, frame) -> None:  # noqa: ARG001
        if owned_process_group is not None:
            _terminate_proven_process_group(
                owned_process_group,
                options.grace_seconds,
            )
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, forward_shutdown)
    signal.signal(signal.SIGINT, forward_shutdown)

    blocked_signals = {signal.SIGTERM, signal.SIGINT}
    previous_mask = None
    try:
        if hasattr(signal, "pthread_sigmask"):
            previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, blocked_signals)
        process = subprocess.Popen(
            command,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            env=_child_environment(options.env_key),
            start_new_session=True,
        )
        # POSIX guarantees a successful start_new_session makes the child's
        # PID its session/process-group ID. Capture it without a getpgid race:
        # an immediately exiting leader is kept waitable below with WNOWAIT.
        owned_process_group = process.pid
        if previous_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            previous_mask = None

        watcher = threading.Thread(
            target=watch_parent_and_terminate_group,
            args=(options.parent_pid, owned_process_group, options.grace_seconds),
            daemon=True,
        )
        watcher.start()
        # Observe the session leader's exit without reaping it. Keeping the
        # leader waitable preserves the ownership proof while remaining group
        # members are terminated, closing the PGID-reuse window.
        while True:
            observed = os.waitid(
                os.P_PID,
                process.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
            if observed is not None:
                # WNOWAIT leaves the leader's PID reserved, so the captured
                # equal PID/PGID cannot be reused between observation and
                # cleanup even on platforms where getpgid(zombie) is ESRCH.
                _terminate_proven_process_group(
                    owned_process_group,
                    options.grace_seconds,
                )
                return process.wait()
            time.sleep(_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        if owned_process_group is not None:
            _terminate_proven_process_group(
                owned_process_group,
                options.grace_seconds,
            )
        return 130
    finally:
        if previous_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        if (
            process is not None
            and process.returncode is None
            and owned_process_group is not None
        ):
            _terminate_proven_process_group(
                owned_process_group,
                options.grace_seconds,
            )
            try:
                process.wait(timeout=options.grace_seconds)
            except subprocess.TimeoutExpired:
                pass


__all__ = [
    "parent_is_alive",
    "run_mcp_watchdog",
    "watch_parent_and_terminate_group",
]


if __name__ == "__main__":
    # A stdlib-only PyInstaller entry: do not import the Runtime package here.
    raise SystemExit(run_mcp_watchdog(sys.argv[1:]))
