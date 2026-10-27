"""Deterministic helpers for MCP pool and watchdog tests."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

def fake_tool(name: str, schema: dict | None = None):
    return SimpleNamespace(
        name=name,
        description=f"Description for {name}",
        inputSchema=(
            {"type": "object", "properties": {}} if schema is None else schema
        ),
    )


class FakeConnectedClient:
    def __init__(
        self,
        pages: dict[str | None, tuple[list[object], str | None]] | None = None,
    ) -> None:
        self.pages = pages or {None: ([fake_tool("lookup")], None)}
        self.closed = False
        self.calls: list[tuple[str, dict]] = []

    async def list_tools_page(self, cursor: str | None):
        tools, next_cursor = self.pages[cursor]
        return tuple(tools), next_cursor

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return {"name": name, "arguments": arguments}

    async def aclose(self) -> None:
        self.closed = True


class RecordingConnector:
    def __init__(
        self,
        factory: Callable[[], FakeConnectedClient] = FakeConnectedClient,
        *,
        failures: int = 0,
    ) -> None:
        self.factory = factory
        self.failures = failures
        self.connect_calls = 0
        self.created: list[FakeConnectedClient] = []

    async def __call__(self, _server):
        from verilex_runtime.mcp.client import MCPConnectionAttemptError

        self.connect_calls += 1
        client = self.factory()
        self.created.append(client)
        if self.connect_calls <= self.failures:
            raise MCPConnectionAttemptError(client)
        return client


def _ignore_shutdown() -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _grandchild(pid_path: Path, *, stubborn: bool = False) -> int:
    if stubborn:
        _ignore_shutdown()
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    while True:
        time.sleep(1)


def _server(
    pid_path: Path,
    grandchild_pid_path: Path,
    *,
    stubborn: bool = False,
) -> int:
    if stubborn:
        _ignore_shutdown()
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    subprocess.Popen(
        [
            sys.executable,
            __file__,
            "stubborn-grandchild" if stubborn else "grandchild",
            str(grandchild_pid_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    while True:
        time.sleep(1)


def _orphaning_server(
    server_pid_path: Path,
    grandchild_pid_path: Path,
    release_path: Path | None = None,
) -> int:
    server_pid_path.write_text(str(os.getpid()), encoding="utf-8")
    subprocess.Popen(
        [
            sys.executable,
            __file__,
            "stubborn-grandchild",
            str(grandchild_pid_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not grandchild_pid_path.exists():
        time.sleep(0.01)
    while release_path is not None and not release_path.exists():
        time.sleep(0.01)
    return 0 if grandchild_pid_path.exists() else 3


def _noisy_mcp_server() -> int:
    os.write(sys.stderr.fileno(), b"n" * (256 * 1024))

    import anyio
    from mcp import types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    server = Server("verilex-noisy-fixture")

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name="lookup",
                description="fixture",
                inputSchema={"type": "object", "properties": {}},
            )
        ]

    async def run() -> None:
        async with stdio_server() as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )

    anyio.run(run)
    return 0


def _end_to_end_mcp_server() -> int:
    """Serve one deterministic tool through the real stdio MCP protocol."""

    import anyio
    from mcp import types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    server = Server("verilex-end-to-end-fixture")

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name="read_case",
                description="Read a deterministic local case",
                inputSchema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                    "additionalProperties": False,
                },
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name != "read_case":
            raise ValueError("unknown tool")
        return [types.TextContent(type="text", text=f"case:{arguments['id']}")]

    async def run() -> None:
        async with stdio_server() as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )

    anyio.run(run)
    return 0


def _parent(metadata_path: Path) -> int:
    server_pid_path = metadata_path.with_suffix(".server.pid")
    grandchild_pid_path = metadata_path.with_suffix(".grandchild.pid")
    watchdog = subprocess.Popen(
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
            __file__,
            "server",
            str(server_pid_path),
            str(grandchild_pid_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if server_pid_path.exists() and grandchild_pid_path.exists():
            metadata_path.write_text(
                json.dumps(
                    {
                        "watchdog": watchdog.pid,
                        "server": int(server_pid_path.read_text()),
                        "grandchild": int(grandchild_pid_path.read_text()),
                    }
                ),
                encoding="utf-8",
            )
            return 0
        time.sleep(0.02)
    return 3


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        status = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return True
    return ") Z " not in status


@dataclass
class WatchdogProcessTree:
    metadata_path: Path
    parent: subprocess.Popen
    watchdog_pid: int
    server_pid: int
    grandchild_pid: int

    @classmethod
    def start(cls, root: Path) -> "WatchdogProcessTree":
        metadata_path = root / "watchdog-tree.json"
        parent = subprocess.Popen(
            [sys.executable, __file__, "parent", str(metadata_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not metadata_path.exists():
            if parent.poll() is not None:
                break
            time.sleep(0.02)
        if not metadata_path.exists():
            parent.kill()
            parent.wait(timeout=5)
            raise RuntimeError("watchdog fixture did not start")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return cls(
            metadata_path,
            parent,
            metadata["watchdog"],
            metadata["server"],
            metadata["grandchild"],
        )

    def kill_runtime_parent(self) -> None:
        os.kill(self.parent.pid, signal.SIGKILL)
        self.parent.wait(timeout=5)

    def wait_for_watchdog_exit(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not pid_alive(self.watchdog_pid):
                return
            time.sleep(0.05)
        raise AssertionError("watchdog did not exit")

    def cleanup(self) -> None:
        for pid in (self.watchdog_pid, self.server_pid, self.grandchild_pid):
            if pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if self.parent.poll() is None:
            self.parent.kill()
            self.parent.wait(timeout=5)


def main() -> int:
    mode, *args = sys.argv[1:]
    if mode == "grandchild":
        return _grandchild(Path(args[0]))
    if mode == "stubborn-grandchild":
        return _grandchild(Path(args[0]), stubborn=True)
    if mode == "server":
        return _server(Path(args[0]), Path(args[1]))
    if mode == "stubborn-server":
        return _server(Path(args[0]), Path(args[1]), stubborn=True)
    if mode == "orphaning-server":
        return _orphaning_server(Path(args[0]), Path(args[1]))
    if mode == "gated-orphaning-server":
        return _orphaning_server(Path(args[0]), Path(args[1]), Path(args[2]))
    if mode == "noisy-mcp":
        return _noisy_mcp_server()
    if mode == "end-to-end-mcp":
        return _end_to_end_mcp_server()
    if mode == "parent":
        return _parent(Path(args[0]))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
