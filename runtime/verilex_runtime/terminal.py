"""User-operated PTYs; separate from model tools and conversation history."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import os
from pathlib import Path
import signal
import sys

from verilex_runtime.tools.registry import ToolError
from verilex_runtime.tools.sandbox import build_sandbox_profile, sandbox_available

MAX_SESSIONS = 3
MAX_RETAINED_SESSIONS = 12
MAX_OUTPUT = 512 * 1024


@dataclass
class TerminalSession:
    id: str
    workspace_id: str
    root: str
    pid: int
    fd: int
    columns: int
    rows: int
    status: str = "running"
    exit_code: int | None = None
    output: bytearray = field(default_factory=bytearray)
    offset: int = 0
    input_sequence: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    monitor: asyncio.Task | None = None

    def snapshot(self) -> dict:
        return {"id": self.id, "workspaceId": self.workspace_id,
                "status": self.status, "exitCode": self.exit_code,
                "columns": self.columns, "rows": self.rows,
                "inputSequence": self.input_sequence}


class TerminalManager:
    def __init__(self, workspaces, *, audit=None):
        self.workspaces = workspaces
        self.audit = audit
        self.sessions: dict[str, TerminalSession] = {}

    @property
    def supported(self) -> bool:
        return sys.platform == "darwin" and sandbox_available()

    def active_count(self) -> int:
        return sum(s.status == "running" for s in self.sessions.values())

    def has_active(self, workspace_id: str) -> bool:
        return any(s.workspace_id == workspace_id and s.status == "running"
                   for s in self.sessions.values())

    def _workspace(self, workspace_id: str):
        workspace = self.workspaces.get(workspace_id)
        if workspace is None or not workspace.allow_read or not workspace.allow_write:
            raise ToolError("terminal_workspace", "请选择已授权读写的文件夹。")
        root = Path(workspace.root_path)
        if not root.is_dir() or str(root.resolve()) != workspace.root_path:
            raise ToolError("terminal_workspace", "文件夹已移动或无法访问，请重新选择。")
        return workspace

    def list(self, workspace_id: str) -> dict:
        self._workspace(workspace_id)
        return {"supported": self.supported, "sessions": [s.snapshot() for s in self.sessions.values()
                if s.workspace_id == workspace_id]}

    def get(self, workspace_id: str, session_id: str) -> TerminalSession:
        session = self.sessions.get(session_id)
        if session is None or session.workspace_id != workspace_id:
            raise ToolError("terminal_not_found", "终端会话已结束或后台已重启，请新建终端。")
        return session

    def create(self, workspace_id: str, session_id: str, columns: int, rows: int) -> dict:
        workspace = self._workspace(workspace_id)
        if session_id in self.sessions:
            return self.get(workspace_id, session_id).snapshot()
        if not self.supported:
            raise ToolError("terminal_unsupported", "终端面板目前仅支持具备系统命令沙箱的 macOS。")
        if self.active_count() >= MAX_SESSIONS:
            raise ToolError("terminal_limit", "最多同时打开三个终端，请先结束一个。")
        # No shell rc files, credentials, runtime token or arbitrary environment
        # cross this boundary. The shell is human-operated, never model-driven.
        import fcntl
        import pty
        import struct
        import termios

        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
            tty_name = os.ttyname(slave)
            profile = build_sandbox_profile(Path(workspace.root_path))
            profile += ('\n(allow file-write* (literal "/dev/null") (literal "/dev/tty") '
                        f'(literal "{tty_name}"))\n(allow file-ioctl)\n')
            # A standalone helper acquires the controlling terminal explicitly,
            # avoiding fork/preexec callbacks in the threaded Runtime.
            env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                   "HOME": workspace.root_path, "ZDOTDIR": workspace.root_path,
                   "TERM": "xterm-256color", "LANG": "en_US.UTF-8",
                   "LC_CTYPE": "UTF-8", "PS1": "%1~ ❯ ", "HISTFILE": "/dev/null"}
            # -f disables startup scripts; positional $1 safely supplies cwd.
            argv = ["/usr/bin/sandbox-exec", "-p", profile, "/bin/zsh", "-f", "-c",
                    'cd -- "$1" && exec /bin/zsh -f -i', "verilex-terminal", workspace.root_path]
            if getattr(sys, "frozen", False):
                helper = Path(sys.executable).parent / "verilex-terminal-host" / "verilex-terminal-host"
                command = [str(helper), *argv]
            else:
                command = [sys.executable, str(Path(__file__).with_name("terminal_host.py")), *argv]
            pid = os.posix_spawn(command[0], command, env, setsid=True, setsigmask=(),
                setsigdef=(signal.SIGINT, signal.SIGQUIT, signal.SIGHUP, signal.SIGPIPE,
                           signal.SIGTERM, signal.SIGTSTP, signal.SIGTTIN, signal.SIGTTOU),
                file_actions=[
                (os.POSIX_SPAWN_OPEN, 0, tty_name, os.O_RDWR, 0),
                (os.POSIX_SPAWN_DUP2, 0, 1), (os.POSIX_SPAWN_DUP2, 0, 2),
            ])
        except (OSError, NotImplementedError):
            os.close(master)
            raise ToolError("terminal_start_failed", "终端启动失败，请检查文件夹后重试。") from None
        finally:
            os.close(slave)
        os.set_blocking(master, False)
        session = TerminalSession(session_id, workspace_id, workspace.root_path, pid, master, columns, rows)
        self.sessions[session_id] = session
        loop = asyncio.get_running_loop()
        loop.add_reader(master, self._read_ready, session)
        session.monitor = loop.create_task(self._watch(session))
        # Bound finished-session memory as well as each live output buffer.
        for old_id in list(self.sessions):
            if len(self.sessions) <= MAX_RETAINED_SESSIONS:
                break
            if self.sessions[old_id].status != "running":
                del self.sessions[old_id]
        self._audit(session, "started")
        return session.snapshot()

    def _audit(self, session, outcome):
        if self.audit:
            self.audit.record_terminal(session_id=session.id, workspace_id=session.workspace_id, outcome=outcome)

    def _read_ready(self, session):
        for _ in range(4):
            try:
                chunk = os.read(session.fd, 65536)
            except BlockingIOError:
                return
            except OSError:
                chunk = b""
            if not chunk:
                if session.fd >= 0:
                    asyncio.get_running_loop().remove_reader(session.fd)
                return
            session.output.extend(chunk)
            if len(session.output) > MAX_OUTPUT:
                dropped = len(session.output) - MAX_OUTPUT
                del session.output[:dropped]
                session.offset += dropped
            session.changed.set()

    async def _watch(self, session):
        while True:
            pid, status = os.waitpid(session.pid, os.WNOHANG)
            if pid:
                self._read_ready(session)
                session.exit_code = os.waitstatus_to_exitcode(status)
                if session.status == "running":
                    session.status = "exited"
                self._close_fd(session)
                session.changed.set()
                self._audit(session, session.status)
                return
            await asyncio.sleep(0.1)

    def _close_fd(self, session):
        if session.fd >= 0:
            asyncio.get_running_loop().remove_reader(session.fd)
            os.close(session.fd)
            session.fd = -1

    async def output(self, workspace_id, session_id, after):
        session = self.get(workspace_id, session_id)
        session.changed.clear()
        if after >= session.offset + len(session.output) and session.status == "running":
            try:
                await asyncio.wait_for(session.changed.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
        start = max(session.offset, min(after, session.offset + len(session.output)))
        data = bytes(session.output[start - session.offset:start - session.offset + 65536])
        return {**session.snapshot(), "cursor": start + len(data),
                "data": base64.b64encode(data).decode("ascii"), "truncated": after < session.offset}

    def write(self, workspace_id, session_id, text, sequence):
        session = self.get(workspace_id, session_id)
        self._workspace(workspace_id)
        if session.status != "running" or session.fd < 0:
            raise ToolError("terminal_exited", "终端已结束，请新建终端。")
        if sequence != session.input_sequence + 1:
            raise ToolError("terminal_input_order", "输入顺序已变化，请重新连接后核对终端内容。")
        raw = text.encode("utf-8")
        try:
            written = os.write(session.fd, raw)
        except (OSError, BlockingIOError):
            raise ToolError("terminal_input_failed", "输入未能确认，请先检查终端内容再重试。") from None
        session.input_sequence = sequence
        if written != len(raw):
            raise ToolError("terminal_input_failed", "输入仅部分送达，请检查终端内容；不会自动重发。")
        return {"inputSequence": sequence}

    def resize(self, workspace_id, session_id, columns, rows):
        session = self.get(workspace_id, session_id)
        if session.status == "running" and session.fd >= 0:
            import fcntl
            import struct
            import termios
            fcntl.ioctl(session.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
            session.columns, session.rows = columns, rows
        return session.snapshot()

    async def stop(self, workspace_id, session_id):
        session = self.get(workspace_id, session_id)
        if session.status == "running":
            session.status = "closed"
            groups = {session.pid}
            if session.fd >= 0:
                try:
                    groups.add(os.tcgetpgrp(session.fd))
                except OSError:
                    pass
            for group in groups:
                if group > 0:
                    try:
                        os.killpg(group, signal.SIGHUP)
                    except ProcessLookupError:
                        pass
            self._close_fd(session)
            await asyncio.sleep(0.15)
            for group in groups:
                if group > 0:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if session.monitor:
                await session.monitor
        return session.snapshot()

    async def shutdown(self):
        await asyncio.gather(*(self.stop(s.workspace_id, s.id)
                               for s in list(self.sessions.values()) if s.status == "running"))
