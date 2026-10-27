from __future__ import annotations

import os
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from verilex_runtime.mcp import watchdog as watchdog_module
from verilex_runtime.mcp.watchdog import (
    parent_is_alive,
    watch_parent_and_terminate_group,
)

from .fake_server import WatchdogProcessTree, pid_alive


def test_parent_identity_check_fails_closed_for_invalid_pid():
    assert parent_is_alive(0) is False
    assert parent_is_alive(-1) is False


def test_frozen_onefile_bootloader_hop_is_accepted(monkeypatch):
    """PyInstaller --onefile re-execs the CLI below a bootloader, so the
    recorded runtime pid sits one hop above ``os.getppid()``."""
    bootloader_pid = os.getpid() + 10_000
    monkeypatch.setattr(os, "getppid", lambda: bootloader_pid)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        watchdog_module, "_grandparent_pid", lambda pid: os.getpid()
    )
    assert parent_is_alive(os.getpid()) is True


def test_extra_parent_hop_fails_closed_without_frozen(monkeypatch):
    """Source runs must remain direct children; a random hop is rejected."""
    bootloader_pid = os.getpid() + 10_000
    monkeypatch.setattr(os, "getppid", lambda: bootloader_pid)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(
        watchdog_module, "_grandparent_pid", lambda pid: os.getpid()
    )
    assert parent_is_alive(os.getpid()) is False


def test_watch_helper_fails_closed_for_unowned_process_group():
    watch_parent_and_terminate_group(0, -1, 0)


@pytest.mark.skipif(os.name != "posix", reason="process-group watchdog is POSIX-only")
def test_immediately_exiting_server_is_reaped_without_raw_error():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "verilex_runtime",
            "--mcp-watchdog",
            "--parent-pid",
            str(os.getpid()),
            "--",
            "/usr/bin/true",
        ],
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert result.returncode == 0
    assert "Traceback" not in result.stderr


@pytest.mark.skipif(os.name != "posix", reason="process-group watchdog is POSIX-only")
def test_watchdog_reaps_server_and_grandchild(tmp_path):
    tree = WatchdogProcessTree.start(tmp_path)
    try:
        tree.kill_runtime_parent()
        tree.wait_for_watchdog_exit()
        assert pid_alive(tree.server_pid) is False
        assert pid_alive(tree.grandchild_pid) is False
    finally:
        tree.cleanup()


@pytest.mark.skipif(os.name != "posix", reason="process-group watchdog is POSIX-only")
def test_normal_wrapper_shutdown_reaps_stubborn_server_and_grandchild(tmp_path):
    server_pid_path = tmp_path / "stubborn.server.pid"
    grandchild_pid_path = tmp_path / "stubborn.grandchild.pid"
    wrapper = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "verilex_runtime",
            "--mcp-watchdog",
            "--parent-pid",
            str(os.getpid()),
            "--grace-seconds",
            "0.5",
            "--",
            sys.executable,
            str(Path(__file__).with_name("fake_server.py")),
            "stubborn-server",
            str(server_pid_path),
            str(grandchild_pid_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pids = [wrapper.pid]
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if server_pid_path.exists() and grandchild_pid_path.exists():
                break
            time.sleep(0.02)
        assert server_pid_path.exists() and grandchild_pid_path.exists()
        server_pid = int(server_pid_path.read_text())
        grandchild_pid = int(grandchild_pid_path.read_text())
        pids.extend((server_pid, grandchild_pid))

        # The official SDK closes stdin first, then SIGTERMs the wrapper group
        # after two seconds. This simulates the latter phase and requires the
        # wrapper to finish its own TERM->KILL escalation before SDK SIGKILL.
        wrapper.stdin.close()
        os.killpg(wrapper.pid, signal.SIGTERM)
        wrapper.wait(timeout=1.8)

        assert pid_alive(server_pid) is False
        assert pid_alive(grandchild_pid) is False
    finally:
        for pid in pids:
            if pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


@pytest.mark.skipif(os.name != "posix", reason="process-group watchdog is POSIX-only")
def test_server_exit_still_reaps_its_stubborn_grandchild(tmp_path):
    server_pid_path = tmp_path / "orphan.server.pid"
    grandchild_pid_path = tmp_path / "orphan.grandchild.pid"
    wrapper = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "verilex_runtime",
            "--mcp-watchdog",
            "--parent-pid",
            str(os.getpid()),
            "--grace-seconds",
            "0.2",
            "--",
            sys.executable,
            str(Path(__file__).with_name("fake_server.py")),
            "orphaning-server",
            str(server_pid_path),
            str(grandchild_pid_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    grandchild_pid = None
    try:
        wrapper.wait(timeout=3)
        assert server_pid_path.exists() and grandchild_pid_path.exists()
        server_pid = int(server_pid_path.read_text())
        grandchild_pid = int(grandchild_pid_path.read_text())
        with pytest.raises(ProcessLookupError):
            os.killpg(server_pid, 0)
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=3)
        if grandchild_pid is not None and pid_alive(grandchild_pid):
            try:
                os.kill(grandchild_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != "posix", reason="process-group watchdog is POSIX-only")
def test_term_after_server_becomes_unreaped_leader_still_reaps_grandchild(tmp_path):
    server_pid_path = tmp_path / "gated.server.pid"
    grandchild_pid_path = tmp_path / "gated.grandchild.pid"
    release_path = tmp_path / "release"
    wrapper = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "verilex_runtime",
            "--mcp-watchdog",
            "--parent-pid",
            str(os.getpid()),
            "--grace-seconds",
            "0.2",
            "--",
            sys.executable,
            str(Path(__file__).with_name("fake_server.py")),
            "gated-orphaning-server",
            str(server_pid_path),
            str(grandchild_pid_path),
            str(release_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    server_pid = None
    grandchild_pid = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if server_pid_path.exists() and grandchild_pid_path.exists():
                break
            time.sleep(0.01)
        assert server_pid_path.exists() and grandchild_pid_path.exists()
        server_pid = int(server_pid_path.read_text())
        grandchild_pid = int(grandchild_pid_path.read_text())

        os.kill(wrapper.pid, signal.SIGSTOP)
        release_path.write_text("go", encoding="utf-8")
        time.sleep(0.1)
        os.kill(wrapper.pid, signal.SIGTERM)
        os.kill(wrapper.pid, signal.SIGCONT)
        wrapper.wait(timeout=2)

        with pytest.raises(ProcessLookupError):
            os.killpg(server_pid, 0)
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=3)
        for pid in (server_pid, grandchild_pid):
            if pid is not None and pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
