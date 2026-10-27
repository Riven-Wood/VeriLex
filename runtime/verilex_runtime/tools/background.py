"""Managed background commands: start, poll, and stop long-running tasks.

``run_background_command`` starts a command under the exact security chain of
``run_command`` (existence precheck, minimal
environment, macOS Seatbelt wrap, own process group) but returns immediately
with a task id instead of blocking the Turn. ``background_task_output`` and
``background_task_stop`` are LOW-risk companions so progress reads and stops
never need an approval.

Resource model: at most 3 concurrent running tasks, a per-task wall clock
capped by the user-adjustable ``background_task_max_minutes`` preference, and
a head+tail retained output buffer (128 KiB each end) so a chatty process can
never exhaust parent memory or block on a full pipe.

Persistence: every task is mirrored into the ``background_tasks`` table. The
retained output snapshot is flushed while running and finalized on exit; tasks
left ``running`` by an abnormal shutdown are marked ``interrupted`` on the next
startup so the UI never shows a phantom running command.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.storage.preferences import (
    BACKGROUND_TASK_MAX_MINUTES,
    PreferenceRepository,
)
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
from verilex_runtime.tools.terminal import (
    _command_available,
    _sanitized_env,
    _split_command,
    _terminate,
)

HEAD_TAIL_LIMIT_BYTES = 128 * 1024
READ_RETURN_LIMIT_BYTES = 64 * 1024
MAX_RUNNING_TASKS = 3
DEFAULT_MAX_MINUTES = 30
PUMP_CHUNK_BYTES = 4096

_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "stopped", "expired", "interrupted"}
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class HeadTailBuffer:
    """Retain the first and last ``limit`` bytes of a byte stream.

    Once the head window is full, newer bytes rotate through the tail window;
    bytes pushed out of the tail's front are counted in ``omitted_bytes`` and
    represented by a marker between head and tail in ``snapshot()``.
    """

    def __init__(self, *, limit: int = HEAD_TAIL_LIMIT_BYTES):
        self._limit = limit
        self._head = bytearray()
        self._tail = bytearray()
        self.omitted_bytes = 0

    def append(self, chunk: bytes) -> None:
        if len(self._head) < self._limit:
            room = self._limit - len(self._head)
            self._head += chunk[:room]
            chunk = chunk[room:]
            if not chunk:
                return
        self._tail += chunk
        if len(self._tail) > self._limit:
            excess = len(self._tail) - self._limit
            del self._tail[:excess]
            self.omitted_bytes += excess

    def snapshot(self) -> bytes:
        if self.omitted_bytes:
            marker = f"\n…[{self.omitted_bytes} bytes omitted]…\n".encode("utf-8")
            return bytes(self._head) + marker + bytes(self._tail)
        return bytes(self._head + self._tail)


@dataclass
class _RunningTask:
    id: str
    thread_id: str
    turn_id: str
    tool_call_id: str
    command: str
    description: str
    root: Path
    effective_minutes: int
    proc: asyncio.subprocess.Process
    started_at: str
    created_at: str
    buffer: HeadTailBuffer = field(default_factory=HeadTailBuffer)
    status: str = "running"
    exit_code: int | None = None
    finished_at: str | None = None
    finalized: bool = False
    timer_task: asyncio.Task | None = None
    flush_task: asyncio.Task | None = None
    supervise_task: asyncio.Task | None = None


class BackgroundTaskManager:
    def __init__(
        self,
        database: Database,
        preferences: PreferenceRepository,
        *,
        minute_seconds: float = 60.0,
        flush_interval: float = 2.0,
    ):
        self._database = database
        self._preferences = preferences
        self._minute_seconds = minute_seconds
        self._flush_interval = flush_interval
        self._tasks: dict[str, _RunningTask] = {}

    # ------------------------------------------------------------------ start

    async def start(
        self,
        *,
        context: ToolContext,
        command: str,
        description: str,
        timeout_minutes: int | None = None,
    ) -> dict:
        if context.root_path is None:
            raise ToolError("workspace_not_configured", "No workspace is authorized.")
        if (
            context.thread_id is None
            or context.turn_id is None
            or context.tool_call_id is None
        ):
            raise ToolError(
                "background_context_missing",
                "Background commands may only start from an agent tool call.",
            )
        if not isinstance(command, str) or not command.strip():
            raise ToolError(
                "background_command_rejected", "Command must not be empty."
            )
        if not isinstance(description, str) or not description.strip():
            raise ToolError(
                "background_command_rejected",
                "A description of the command purpose is required.",
            )
        argv = _split_command(command)
        if not _command_available(argv[0]):
            raise ToolError(
                "command_not_found", f"'{argv[0]}' is not in PATH"
            )
        running = sum(
            1 for task in self._tasks.values() if task.status == "running"
        )
        if running >= MAX_RUNNING_TASKS:
            raise ToolError(
                "background_task_limit_reached",
                f"At most {MAX_RUNNING_TASKS} background tasks may run at once.",
            )

        max_minutes = self._preferences.get_int(
            BACKGROUND_TASK_MAX_MINUTES, default=DEFAULT_MAX_MINUTES
        )
        requested = (
            timeout_minutes
            if isinstance(timeout_minutes, int) and timeout_minutes > 0
            else max_minutes
        )
        effective_minutes = max(1, min(requested, max_minutes))

        exec_argv = build_sandbox_command(argv, context.root_path, protected_paths=context.protected_paths, allow_write=context.allow_write)
        try:
            proc = await asyncio.create_subprocess_exec(
                *exec_argv,
                cwd=str(context.root_path),
                env=_sanitized_env(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError:
            raise ToolError(
                "command_not_found", f"'{argv[0]}' is not in PATH"
            ) from None
        except PermissionError:
            raise ToolError(
                "permission_denied", f"cannot execute '{argv[0]}'"
            ) from None

        now = _utc_now()
        task = _RunningTask(
            id=_task_id(),
            thread_id=context.thread_id,
            turn_id=context.turn_id,
            tool_call_id=context.tool_call_id,
            command=command,
            description=description,
            root=context.root_path,
            effective_minutes=effective_minutes,
            proc=proc,
            started_at=now,
            created_at=now,
        )
        self._tasks[task.id] = task
        self._insert_row(task)
        task.supervise_task = asyncio.ensure_future(self._supervise(task))
        task.timer_task = asyncio.ensure_future(self._expiry_timer(task))
        task.flush_task = asyncio.ensure_future(self._flush_loop(task))
        return {
            "task_id": task.id,
            "status": "running",
            "effective_minutes": effective_minutes,
        }

    # ------------------------------------------------------------- read/stop

    async def read(
        self, thread_id: str | None, task_id: str, *, after: int = 0
    ) -> dict:
        task = self._tasks.get(task_id)
        if task is not None and task.thread_id == thread_id:
            snapshot = task.buffer.snapshot()
            omitted = task.buffer.omitted_bytes
            status = task.status
        else:
            row = self._row_for(thread_id, task_id)
            if row is None:
                raise ToolError(
                    "background_task_not_found",
                    "Background task was not found for this conversation.",
                )
            snapshot = (row["output_text"] or "").encode("utf-8")
            omitted = row["omitted_bytes"]
            status = row["status"]
        start = max(0, after if isinstance(after, int) else 0)
        if start >= len(snapshot):
            return {
                "cursor": start,
                "text": "",
                "omitted_bytes": omitted,
                "status": status,
            }
        chunk = snapshot[start : start + READ_RETURN_LIMIT_BYTES]
        return {
            "cursor": start + len(chunk),
            "text": chunk.decode("utf-8", errors="replace"),
            "omitted_bytes": omitted,
            "status": status,
        }

    async def stop(self, thread_id: str | None, task_id: str) -> dict:
        task = self._tasks.get(task_id)
        if task is None or task.thread_id != thread_id:
            row = self._row_for(thread_id, task_id)
            if row is None:
                raise ToolError(
                    "background_task_not_found",
                    "Background task was not found for this conversation.",
                )
            raise ToolError(
                "background_task_not_running",
                "Background task has already finished.",
            )
        if task.status != "running":
            raise ToolError(
                "background_task_not_running",
                "Background task has already finished.",
            )
        task.status = "stopped"
        await _terminate(task.proc)
        self._finalize(task, "stopped", task.proc.returncode)
        return {"task_id": task.id, "status": "stopped"}

    # ------------------------------------------------------------- listings

    def get(self, thread_id: str | None, task_id: str) -> dict | None:
        task = self._tasks.get(task_id)
        if task is not None and task.thread_id == thread_id:
            return self._live_record(task)
        row = self._row_for(thread_id, task_id)
        return None if row is None else self._row_record(row)

    def list_for_thread(self, thread_id: str) -> list[dict]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM background_tasks WHERE thread_id = ? ORDER BY created_at, id",
                (thread_id,),
            ).fetchall()
        return [self._row_record(row) for row in rows]

    # ---------------------------------------------------- lifecycle plumbing

    def recover(self) -> int:
        """Mark ``running`` rows from a previous process as ``interrupted``."""
        with self._database.connect() as connection:
            cursor = connection.execute(
                "UPDATE background_tasks SET status = 'interrupted', finished_at = ? WHERE status = 'running'",
                (_utc_now(),),
            )
            return cursor.rowcount

    async def shutdown_all(self) -> None:
        """Terminate every running task; used on normal Runtime shutdown."""
        for task in list(self._tasks.values()):
            if task.status != "running":
                continue
            task.status = "interrupted"
            await _terminate(task.proc)
            self._finalize(task, "interrupted", task.proc.returncode)

    async def _supervise(self, task: _RunningTask) -> None:
        await asyncio.gather(
            self._pump(task.proc.stdout, task.buffer),
            self._pump(task.proc.stderr, task.buffer),
        )
        exit_code = await task.proc.wait()
        # Stop/expiry/shutdown owns finalization while waiting for the entire
        # process group; the leader exiting must not overwrite its outcome.
        if task.finalized or task.status != "running":
            return
        status = "completed" if exit_code == 0 else "failed"
        self._finalize(task, status, exit_code)

    @staticmethod
    async def _pump(stream, buffer: HeadTailBuffer) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(PUMP_CHUNK_BYTES)
            if not chunk:
                return
            buffer.append(chunk)

    async def _expiry_timer(self, task: _RunningTask) -> None:
        await asyncio.sleep(task.effective_minutes * self._minute_seconds)
        if task.finalized or task.status != "running":
            return
        task.status = "expired"
        await _terminate(task.proc)
        self._finalize(task, "expired", task.proc.returncode)

    async def _flush_loop(self, task: _RunningTask) -> None:
        try:
            while task.status == "running" and not task.finalized:
                await asyncio.sleep(self._flush_interval)
                if task.status == "running" and not task.finalized:
                    self._persist_output(task)
        except asyncio.CancelledError:
            return

    def _finalize(
        self, task: _RunningTask, status: str, exit_code: int | None
    ) -> None:
        if task.finalized:
            return
        task.finalized = True
        task.status = status
        task.exit_code = exit_code
        task.finished_at = _utc_now()
        for background in (task.timer_task, task.flush_task):
            if background is not None and not background.done():
                background.cancel()
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE background_tasks SET status = ?, exit_code = ?, output_text = ?, omitted_bytes = ?, finished_at = ? WHERE id = ?",
                (
                    task.status,
                    task.exit_code,
                    task.buffer.snapshot().decode("utf-8", errors="replace"),
                    task.buffer.omitted_bytes,
                    task.finished_at,
                    task.id,
                ),
            )
        self._tasks.pop(task.id, None)

    def _persist_output(self, task: _RunningTask) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE background_tasks SET output_text = ?, omitted_bytes = ? WHERE id = ?",
                (
                    task.buffer.snapshot().decode("utf-8", errors="replace"),
                    task.buffer.omitted_bytes,
                    task.id,
                ),
            )

    def _insert_row(self, task: _RunningTask) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO background_tasks (id, thread_id, turn_id, tool_call_id, command, description, status, exit_code, output_text, omitted_bytes, started_at, finished_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, ?, NULL, ?)",
                (
                    task.id,
                    task.thread_id,
                    task.turn_id,
                    task.tool_call_id,
                    task.command,
                    task.description,
                    task.status,
                    "",
                    task.started_at,
                    task.created_at,
                ),
            )

    def _row_for(self, thread_id: str | None, task_id: str):
        with self._database.connect() as connection:
            return connection.execute(
                "SELECT * FROM background_tasks WHERE id = ? AND thread_id = ?",
                (task_id, thread_id),
            ).fetchone()

    @staticmethod
    def _live_record(task: _RunningTask) -> dict:
        return {
            "id": task.id,
            "thread_id": task.thread_id,
            "turn_id": task.turn_id,
            "tool_call_id": task.tool_call_id,
            "command": task.command,
            "description": task.description,
            "status": task.status,
            "exit_code": task.exit_code,
            "output_text": task.buffer.snapshot().decode(
                "utf-8", errors="replace"
            ),
            "omitted_bytes": task.buffer.omitted_bytes,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "created_at": task.created_at,
        }

    @staticmethod
    def _row_record(row) -> dict:
        return {
            "id": row["id"],
            "thread_id": row["thread_id"],
            "turn_id": row["turn_id"],
            "tool_call_id": row["tool_call_id"],
            "command": row["command"],
            "description": row["description"],
            "status": row["status"],
            "exit_code": row["exit_code"],
            "output_text": row["output_text"] or "",
            "omitted_bytes": row["omitted_bytes"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "created_at": row["created_at"],
        }


def _task_id() -> str:
    import uuid

    return f"bt_{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Tool registrations


def register_background_tools(
    registry: ToolRegistry,
    manager: BackgroundTaskManager,
    preferences: PreferenceRepository,
) -> None:
    async def start_tool(context: ToolContext, args: dict) -> ToolResult:
        try:
            max_minutes = preferences.get_int(
                BACKGROUND_TASK_MAX_MINUTES, default=DEFAULT_MAX_MINUTES
            )
            result = await manager.start(
                context=context,
                command=args["command"],
                description=args["description"],
                timeout_minutes=args.get("timeout_minutes"),
            )
        except ToolError as error:
            return ToolResult(ok=False, output=f"{error.code}: {error.message}")
        output = (
            f"task_id: {result['task_id']}\n"
            f"status: running\n"
            f"effective timeout: {result['effective_minutes']} minutes "
            f"(limit {max_minutes})\n"
            "poll with background_task_output, stop with background_task_stop"
        )
        return ToolResult(ok=True, output=output)

    async def output_tool(context: ToolContext, args: dict) -> ToolResult:
        try:
            data = await manager.read(
                context.thread_id, args["task_id"], after=args.get("after", 0)
            )
        except ToolError as error:
            return ToolResult(ok=False, output=f"{error.code}: {error.message}")
        body = data["text"] if data["text"] else "(no new output)"
        output = (
            f"status: {data['status']}\n"
            f"cursor: {data['cursor']}\n"
            f"omitted_bytes: {data['omitted_bytes']}\n"
            f"--- new output ---\n{body}"
        )
        return ToolResult(ok=True, output=output)

    async def stop_tool(context: ToolContext, args: dict) -> ToolResult:
        try:
            result = await manager.stop(context.thread_id, args["task_id"])
        except ToolError as error:
            return ToolResult(ok=False, output=f"{error.code}: {error.message}")
        return ToolResult(
            ok=True,
            output=f"task_id: {result['task_id']}\nstatus: {result['status']}",
        )

    registry.register(
        ToolDefinition(
            name="run_background_command",
            description=(
                "Start a long-running command in the authorized workspace "
                "(requires approval). Returns a task id immediately; poll "
                "output with background_task_output, stop it with "
                "background_task_stop. Include a short description of the "
                "command purpose."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "Command to execute (e.g. 'python3 convert.py'). "
                            "Arguments are passed directly to the executable; shell expansion, pipes and redirects are not interpreted."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "What this command is for, shown on the approval "
                            "card."
                        ),
                    },
                    "timeout_minutes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 120,
                        "description": (
                            "Maximum runtime in minutes before the task is "
                            "stopped. Defaults to the configured limit."
                        ),
                    },
                },
                "required": ["command", "description"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.HIGH,
            execute=start_tool,
            source=ToolSource.BUILTIN,
            category=ToolCategory.TERMINAL,
            scopes=frozenset({ToolScope.EXECUTE}),
            requires_workspace=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="background_task_output",
            description=(
                "Read new output from a background task in this conversation "
                "since the given cursor. No approval needed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "after": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Cursor from a previous read.",
                    },
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.LOW,
            execute=output_tool,
            source=ToolSource.BUILTIN,
            category=ToolCategory.TERMINAL,
            scopes=frozenset({ToolScope.READ}),
            requires_workspace=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="background_task_stop",
            description=(
                "Stop a running background task in this conversation "
                "(terminates its whole process group). No approval needed."
            ),
            parameters={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            risk_level=RiskLevel.LOW,
            execute=stop_tool,
            source=ToolSource.BUILTIN,
            category=ToolCategory.TERMINAL,
            scopes=frozenset({ToolScope.EXECUTE}),
            requires_workspace=False,
        )
    )
