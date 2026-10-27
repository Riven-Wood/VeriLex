"""MCP connection manager: persistent, lazily-connected, per-server sessions.

A single :class:`MCPManager` owns the connections for all enabled MCP servers
for the process lifetime. Each server is connected in its own asyncio task
(required because the SDK's transport cancel scopes must be entered and exited
on one task), the session is kept alive across turns, and ``tools/list`` is
discovered once at connect and cached.

A stdio server that fails to spawn or initialize is recorded as an error and
skipped, so the runtime keeps running without that server's tools (fail-open
for the session, fail-loud for the operator).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import threading
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

import httpx

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool

from verilex_runtime.mcp.canonical import connection_config_fingerprint
from verilex_runtime.mcp.config import MCPServerDef
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.importer import MCPServerDraft
from verilex_runtime.mcp.schema import CanonicalMCPToolSchema, validate_mcp_schema
from verilex_runtime.tools.registry import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

DEFAULT_TOOL_TIMEOUT = 120.0
_STDERR_TAIL_LINES = 20
_STDERR_TAIL_CHARS = 1500
_STDERR_MAX_BYTES = 8192
_STDERR_SCAN_OVERLAP_BYTES = 256
_SAFE_STDIO_ENVIRONMENT = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
_SECRET_IN_STDERR = re.compile(
    rb"(?:authorization\s*:\s*)?bearer\s+\S{8,}"
    rb"|(?:api[_-]?key|token|secret|password|passwd)\s*[:=]\s*\S{8,}",
    re.IGNORECASE,
)


@asynccontextmanager
async def _same_task_timeout(seconds: float):
    """Cancel the current operation at a deadline without moving task ownership."""

    owner = asyncio.current_task()
    assert owner is not None
    timed_out = False

    async def cancel_at_deadline() -> None:
        nonlocal timed_out
        await asyncio.sleep(seconds)
        timed_out = True
        owner.cancel()

    timer = asyncio.create_task(cancel_at_deadline())
    try:
        yield
    except asyncio.CancelledError:
        if timed_out:
            uncancel = getattr(owner, "uncancel", None)
            if uncancel is not None:
                uncancel()
            raise TimeoutError from None
        raise
    finally:
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)


@dataclass(frozen=True, slots=True)
class MCPConnectionKey:
    server_id: str
    config_fingerprint: str
    secret_revision: int


@dataclass(frozen=True, slots=True)
class MCPResolvedServer:
    server_id: str
    draft: MCPServerDraft
    secret_revision: int

    @property
    def key(self) -> MCPConnectionKey:
        return MCPConnectionKey(
            self.server_id,
            connection_config_fingerprint(self.draft),
            self.secret_revision,
        )


@dataclass(frozen=True, slots=True)
class MCPProbeResult:
    tools: tuple[Tool, ...]
    stderr_tail: str = ""


class MCPConnectionAttemptError(Exception):
    """A failed connection attempt with a partial resource to be closed."""

    def __init__(
        self,
        connection: Any | None = None,
        *,
        code: str = "mcp_connection_failed",
    ) -> None:
        super().__init__("mcp connection attempt failed")
        self.connection = connection
        self.code = code


def build_minimal_stdio_environment(
    explicit: dict[str, str] | Any,
    *,
    environ: dict[str, str] | Any = None,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    result = {
        name: source[name]
        for name in _SAFE_STDIO_ENVIRONMENT
        if name in source
    }
    result.update(dict(explicit))
    return result


def _utf8_tail(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[-max_bytes:]
    return truncated.decode("utf-8", "ignore")


def bounded_stderr_tail(
    data: bytes,
    max_bytes: int = _STDERR_MAX_BYTES,
    *,
    _known_secrets: tuple[bytes, ...] = (),
) -> str:
    """Return a redacted, replacement-safe stderr tail within a byte budget."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    if max_bytes == 0:
        return ""
    # Scan a small overlap before the displayed tail so a marker cannot be
    # split exactly at the truncation boundary and reveal its value.
    if _SECRET_IN_STDERR.search(data) or any(
        secret and secret in data for secret in _known_secrets
    ):
        return _utf8_tail("[内容已脱敏]", max_bytes)
    rendered = data[-max_bytes:].decode("utf-8", "replace")
    return _utf8_tail(rendered, max_bytes)


class BoundedStderrCapture:
    """Continuously drain child stderr into one fixed-size in-memory ring."""

    def __init__(
        self,
        max_bytes: int = _STDERR_MAX_BYTES,
        *,
        known_secrets: tuple[str, ...] = (),
    ) -> None:
        self._max_bytes = max_bytes
        # If this server has any configured secret, do the simplest safe thing:
        # any non-empty stderr becomes a fixed redacted marker. We never retain
        # secret values or grow the capture ring based on secret length.
        self._redact_nonempty = any(bool(value) for value in known_secrets)
        self._ring_bytes = max_bytes + _STDERR_SCAN_OVERLAP_BYTES
        self._buffer = bytearray()
        self._sensitive_seen = False
        self._scan_suffix = b""
        self._lock = threading.Lock()
        read_fd, write_fd = os.pipe()
        self._read_fd: int | None = read_fd
        self.stderr_file: BinaryIO | None = os.fdopen(write_fd, "wb", buffering=0)
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        read_fd = self._read_fd
        if read_fd is None:
            return
        try:
            while chunk := os.read(read_fd, 4096):
                with self._lock:
                    scan = self._scan_suffix + chunk
                    if _SECRET_IN_STDERR.search(scan):
                        self._sensitive_seen = True
                    self._scan_suffix = scan[-_STDERR_SCAN_OVERLAP_BYTES:]
                    self._buffer.extend(chunk)
                    excess = len(self._buffer) - self._ring_bytes
                    if excess > 0:
                        del self._buffer[:excess]
        except OSError:
            pass
        finally:
            try:
                os.close(read_fd)
            except OSError:
                pass
            self._read_fd = None

    @property
    def tail(self) -> str:
        with self._lock:
            data = bytes(self._buffer)
            redact = self._redact_nonempty or self._sensitive_seen
        if data and redact:
            return _utf8_tail("[内容已脱敏]", self._max_bytes)
        return bounded_stderr_tail(data, self._max_bytes)

    def close(self) -> None:
        stderr_file = self.stderr_file
        self.stderr_file = None
        if stderr_file is not None:
            stderr_file.close()
        self._reader.join(timeout=1)


def mcp_http_client_factory(headers=None, timeout=None, auth=None):
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        auth=auth,
        trust_env=False,
        follow_redirects=False,
    )


@dataclass(slots=True)
class _TransportAdapter:
    context: Any
    http_client: Any | None = None
    stderr_file: BoundedStderrCapture | None = None

    async def aclose(self) -> None:
        try:
            if self.http_client is not None:
                await self.http_client.aclose()
        finally:
            if self.stderr_file is not None:
                self.stderr_file.close()


def _watchdog_argv(draft: MCPServerDraft) -> tuple[str, list[str]]:
    assert draft.command is not None
    from verilex_runtime.excel_plugin.launch import resolve_excel_command
    target_command, target_args = resolve_excel_command(draft)
    if draft.command == "verilex-pdf-plugin":
        from verilex_runtime.pdf_plugin.launcher import resolve_command
        target_command, target_args = resolve_command(draft)
    if os.name != "posix":
        return target_command, target_args
    # The full Runtime contains offline NLP assets; unpacking it for each probe
    # can exceed the connection deadline. The sibling helper runs the exact
    # same watchdog implementation without importing the application package.
    frozen = getattr(sys, "frozen", False)
    command = str(Path(sys.executable).with_name("verilex-mcp-watchdog")) if frozen else sys.executable
    wrapper_args = [] if frozen else ["-m", "verilex_runtime", "--mcp-watchdog"]
    wrapper_args.extend(
        [
            "--parent-pid",
            str(os.getpid()),
            "--grace-seconds",
            "0.5",
        ]
    )
    for name in draft.env:
        wrapper_args.extend(("--env-key", name))
    wrapper_args.extend(("--", target_command, *target_args))
    return command, wrapper_args


def _transport_context(server: MCPResolvedServer) -> _TransportAdapter:
    draft = server.draft
    connect_timeout = draft.connect_timeout_ms / 1000
    request_timeout = draft.request_timeout_ms / 1000
    if draft.transport == "http":
        timeout = httpx.Timeout(
            request_timeout,
            connect=connect_timeout,
            read=request_timeout,
            write=request_timeout,
            pool=connect_timeout,
        )
        http_client = mcp_http_client_factory(
            headers=dict(draft.headers),
            timeout=timeout,
            auth=None,
        )
        return _TransportAdapter(
            streamable_http_client(draft.url or "", http_client=http_client),
            http_client=http_client,
        )
    if draft.transport == "sse":
        def factory(headers=None, timeout=None, auth=None):
            return mcp_http_client_factory(headers, timeout, auth)

        return _TransportAdapter(
            sse_client(
                draft.url or "",
                headers=dict(draft.headers),
                timeout=connect_timeout,
                sse_read_timeout=request_timeout,
                httpx_client_factory=factory,
            )
        )

    if os.name != "posix":
        # SDK 1.29 merges its own inherited default environment even when an
        # explicit environment is supplied. Without the filtering watchdog,
        # VeriLex cannot uphold its minimum-environment contract.
        raise MCPDomainError("mcp_connection_failed", retryable=False)
    command, args = _watchdog_argv(draft)
    capture = BoundedStderrCapture(
        known_secrets=tuple(
            [*draft.secret_env.values(), *draft.secret_headers.values()]
        )
    )
    parameters = StdioServerParameters(
        command=command,
        args=args,
        env=build_minimal_stdio_environment(dict(draft.env)),
        cwd=draft.cwd,
    )
    return _TransportAdapter(
        stdio_client(parameters, errlog=capture.stderr_file),
        stderr_file=capture,
    )


class _SDKConnection:
    """One SDK session whose async contexts belong to one long-lived task."""

    def __init__(self, server: MCPResolvedServer) -> None:
        self.server = server
        self._ready = asyncio.get_running_loop().create_future()
        self._shutdown = asyncio.Event()
        self._closed = asyncio.Event()
        self._session: ClientSession | None = None
        self._adapter: _TransportAdapter | None = None
        self._task = asyncio.create_task(self._owner())
        self._task.add_done_callback(self._owner_done)
        self.stderr_tail = ""

    def _owner_done(self, task: asyncio.Task) -> None:
        self._closed.set()
        if self._ready.done():
            return
        if task.cancelled():
            self._ready.cancel()
            return
        error = task.exception()
        if error is None:
            error = RuntimeError("MCP owner exited before initialization")
        self._ready.set_exception(error)

    @classmethod
    async def connect(cls, server: MCPResolvedServer) -> "_SDKConnection":
        connection = cls(server)
        try:
            await asyncio.shield(connection._ready)
        except asyncio.CancelledError:
            await connection.aclose()
            raise
        except Exception as error:
            await connection.aclose()
            code = (
                error.code
                if isinstance(error, MCPDomainError)
                else "mcp_connection_failed"
            )
            raise MCPConnectionAttemptError(connection, code=code) from error
        except BaseException:
            await connection.aclose()
            raise
        return connection

    async def _owner(self) -> None:
        try:
            self._adapter = _transport_context(self.server)
            connect_timeout = self.server.draft.connect_timeout_ms / 1000
            async with AsyncExitStack() as stack:
                try:
                    async with _same_task_timeout(connect_timeout):
                        streams = await stack.enter_async_context(
                            self._adapter.context
                        )
                except TimeoutError:
                    raise MCPDomainError(
                        "mcp_connect_timeout",
                        retryable=True,
                    ) from None
                read_stream, write_stream = streams[:2]
                try:
                    async with _same_task_timeout(connect_timeout):
                        session = await stack.enter_async_context(
                            ClientSession(read_stream, write_stream)
                        )
                except TimeoutError:
                    raise MCPDomainError(
                        "mcp_connect_timeout",
                        retryable=True,
                    ) from None
                self._session = session
                try:
                    async with _same_task_timeout(connect_timeout):
                        await session.initialize()
                except TimeoutError:
                    raise MCPDomainError(
                        "mcp_connect_timeout",
                        retryable=True,
                    ) from None
                if not self._ready.done():
                    self._ready.set_result(None)
                await self._shutdown.wait()
        except asyncio.CancelledError:
            if not self._ready.done():
                self._ready.cancel()
            raise
        except Exception as error:
            if not self._ready.done():
                self._ready.set_exception(error)
        finally:
            self._session = None
            if self._adapter is not None:
                try:
                    close_adapter = getattr(self._adapter, "aclose", None)
                    if close_adapter is not None:
                        await close_adapter()
                except Exception as error:  # noqa: BLE001 - teardown must finish
                    del error
                    logger.warning("MCP transport cleanup failed")
                finally:
                    stderr_file = getattr(self._adapter, "stderr_file", None)
                    if stderr_file is not None:
                        self.stderr_tail = stderr_file.tail
            self._closed.set()

    async def list_tools_page(self, cursor: str | None):
        session = self._session
        if session is None:
            raise ConnectionError("MCP session is closed")
        page = await session.list_tools(cursor=cursor)
        return tuple(page.tools or ()), page.nextCursor

    async def call_tool(self, name: str, arguments: dict):
        session = self._session
        if session is None:
            raise ConnectionError("MCP session is closed")
        return await session.call_tool(name, arguments)

    async def aclose(self) -> None:
        if self._closed.is_set():
            return
        self._shutdown.set()
        if not self._ready.done():
            self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass


async def _connect_sdk(server: MCPResolvedServer) -> _SDKConnection:
    return await _SDKConnection.connect(server)


async def _close_connection_uninterrupted(connection: Any | None) -> None:
    if connection is None:
        return
    try:
        await connection.aclose()
    except Exception as error:  # noqa: BLE001 - best-effort resource cleanup
        del error
        logger.warning("MCP connection cleanup failed")


async def _close_connection(connection: Any | None) -> None:
    """Close a detached resource fully before delivering caller cancellation."""

    if connection is None:
        return
    close_task = asyncio.create_task(_close_connection_uninterrupted(connection))
    try:
        await asyncio.shield(close_task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(close_task)
        finally:
            raise


async def _list_all_tools(
    connection: Any,
    request_timeout: float,
) -> tuple[Tool, ...]:
    tools: list[Tool] = []
    names: set[str] = set()
    seen_cursors: set[str] = set()
    schema_bytes = 0
    cursor: str | None = None
    for page_number in range(64):
        try:
            async with _same_task_timeout(request_timeout):
                page_tools, next_cursor = await connection.list_tools_page(cursor)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise MCPDomainError("mcp_connection_failed", retryable=True) from None

        if not isinstance(page_tools, (list, tuple)):
            raise MCPDomainError("mcp_tool_catalog_invalid", retryable=False)
        for tool in page_tools:
            try:
                name = getattr(tool, "name", None)
                schema = getattr(tool, "inputSchema", None)
            except Exception:
                raise MCPDomainError("mcp_tool_catalog_invalid", retryable=False) from None
            if not isinstance(name, str) or not name or name in names:
                raise MCPDomainError("mcp_tool_catalog_invalid", retryable=False)
            if schema is None:
                schema = {}
            try:
                canonical: CanonicalMCPToolSchema = validate_mcp_schema(schema)
            except MCPDomainError:
                raise MCPDomainError(
                    "mcp_tool_catalog_invalid",
                    retryable=False,
                ) from None
            names.add(name)
            tools.append(tool)
            schema_bytes += len(canonical.canonical_bytes)
            if len(tools) > 1000 or schema_bytes > 2 * 1024 * 1024:
                raise MCPDomainError("mcp_tool_catalog_invalid", retryable=False)

        if next_cursor is None:
            return tuple(tools)
        if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
            raise MCPDomainError("mcp_tool_catalog_invalid", retryable=False)
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if page_number == 63:
            raise MCPDomainError("mcp_tool_catalog_invalid", retryable=False)
    raise MCPDomainError("mcp_tool_catalog_invalid", retryable=False)


@dataclass(slots=True)
class _PoolEntry:
    epoch: int = 0
    retiring: bool = False
    connection: Any | None = None
    spawn_inflight: asyncio.Task | None = None
    leases: set[str] | None = None
    waiters: int = 0
    last_error: MCPDomainError | None = None
    idle_task: asyncio.Task | None = None

    def __post_init__(self) -> None:
        if self.leases is None:
            self.leases = set()


class MCPLease:
    """A unique claim on a shared MCP connection."""

    def __init__(
        self,
        pool: "MCPConnectionPool",
        key: MCPConnectionKey,
        connection: Any,
        server: MCPResolvedServer,
        owner_kind: str,
        owner_id: str,
    ) -> None:
        self.lease_id = str(uuid4())
        self.owner_kind = owner_kind
        self.owner_id = owner_id
        self._pool = pool
        self._key = key
        self._connection = connection
        self._server = server
        self._closed = False
        self._close_task: asyncio.Task | None = None

    async def list_tools_page(self, cursor: str | None):
        if self._closed:
            raise MCPDomainError("mcp_connection_failed", retryable=True)
        try:
            async with _same_task_timeout(
                self._server.draft.request_timeout_ms / 1000
            ):
                return await self._connection.list_tools_page(cursor)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise MCPDomainError("mcp_connection_failed", retryable=True) from None

    async def list_all_tools(self) -> tuple[Tool, ...]:
        if self._closed:
            raise MCPDomainError("mcp_connection_failed", retryable=True)
        return await _list_all_tools(
            self._connection,
            self._server.draft.request_timeout_ms / 1000,
        )

    async def call_tool(self, name: str, arguments: dict):
        if self._closed:
            raise MCPDomainError("mcp_result_unknown", retryable=False)
        try:
            async with _same_task_timeout(
                self._server.draft.request_timeout_ms / 1000
            ):
                return await self._connection.call_tool(name, arguments)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Once a call may have reached the server it is never replayed.
            raise MCPDomainError("mcp_result_unknown", retryable=False) from None

    async def invalidate_connection(self) -> None:
        """Invalidate only the connection this lease actually observed."""
        await self._pool.invalidate(self._key, self._connection)

    async def aclose(self) -> None:
        if self._closed:
            return
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._pool._release(self._key, self.lease_id)
            )
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self._close_task)
            finally:
                self._closed = True
            raise
        self._closed = True


class MCPConnectionPool:
    """CAS-published MCP connections shared only through unique leases."""

    def __init__(
        self,
        connector=None,
        *,
        idle_grace_seconds: float = 30.0,
    ) -> None:
        self._connector = connector or _connect_sdk
        self._idle_grace_seconds = idle_grace_seconds
        self._entries: dict[MCPConnectionKey, _PoolEntry] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._close_task: asyncio.Task | None = None
        self._suppress_idle_schedule = 0
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._active_probe_tasks: set[asyncio.Task] = set()

    def _track_close_locked(self, connection: Any) -> asyncio.Task:
        task = asyncio.create_task(_close_connection(connection))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)
        return task

    @staticmethod
    async def _await_cleanup(task: asyncio.Task | None) -> None:
        if task is None:
            return
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise

    def is_ready(self, key: MCPConnectionKey) -> bool:
        entry = self._entries.get(key)
        return bool(entry is not None and entry.connection is not None)

    def cached_error(self, key: MCPConnectionKey) -> MCPDomainError | None:
        """Return only the last in-memory connection error; never connect."""
        entry = self._entries.get(key)
        return None if entry is None else entry.last_error

    def ready_keys(self) -> tuple[MCPConnectionKey, ...]:
        return tuple(
            key for key, entry in self._entries.items() if entry.connection is not None
        )

    async def _connect_with_retry(self, server: MCPResolvedServer) -> Any:
        last_code = "mcp_connection_failed"
        for attempt in range(2):
            try:
                if self._connector is _connect_sdk:
                    return await self._connector(server)
                async with _same_task_timeout(
                    server.draft.connect_timeout_ms / 1000
                ):
                    return await self._connector(server)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                last_code = "mcp_connect_timeout"
            except MCPConnectionAttemptError as error:
                await _close_connection(error.connection)
                last_code = error.code
            except MCPDomainError as error:
                last_code = error.code
            except Exception:
                last_code = "mcp_connection_failed"
            if attempt == 0:
                await asyncio.sleep(0)
        raise MCPDomainError(last_code, retryable=True)

    async def _spawn_connection(
        self,
        key: MCPConnectionKey,
        entry: _PoolEntry,
        server: MCPResolvedServer,
        epoch: int,
    ) -> Any:
        connection = None
        try:
            connection = await self._connect_with_retry(server)
            async with self._lock:
                if (
                    self._closed
                    or self._entries.get(key) is not entry
                    or entry.retiring
                    or entry.epoch != epoch
                ):
                    code = (
                        "mcp_connection_retiring"
                        if entry.retiring
                        else "mcp_connection_failed"
                    )
                else:
                    entry.connection = connection
                    entry.last_error = None
                    return connection
            await _close_connection(connection)
            connection = None
            raise MCPDomainError(code, retryable=False)
        except MCPDomainError as error:
            async with self._lock:
                entry.last_error = error
            raise
        except asyncio.CancelledError:
            if connection is not None:
                await _close_connection(connection)
            raise
        finally:
            async with self._lock:
                if entry.spawn_inflight is asyncio.current_task():
                    entry.spawn_inflight = None

    async def acquire(
        self,
        server: MCPResolvedServer,
        owner_kind: str,
        owner_id: str,
    ) -> MCPLease:
        key = server.key
        async with self._lock:
            if self._closed:
                raise MCPDomainError("mcp_connection_failed", retryable=False)
            entry = self._entries.setdefault(key, _PoolEntry())
            if entry.retiring:
                raise MCPDomainError("mcp_connection_retiring", retryable=False)
            if entry.idle_task is not None:
                entry.idle_task.cancel()
            entry.waiters += 1
            if entry.connection is not None:
                spawn = None
                connection = entry.connection
            else:
                if entry.spawn_inflight is None:
                    entry.spawn_inflight = asyncio.create_task(
                        self._spawn_connection(
                            key,
                            entry,
                            server,
                            entry.epoch,
                        )
                    )
                spawn = entry.spawn_inflight
                connection = None

        registered_waiter = True
        close_task = None
        try:
            if spawn is not None:
                connection = await asyncio.shield(spawn)
            assert connection is not None
            async with self._lock:
                entry.waiters -= 1
                registered_waiter = False
                if (
                    self._closed
                    or entry.retiring
                    or entry.connection is not connection
                ):
                    code = (
                        "mcp_connection_retiring"
                        if entry.retiring
                        else "mcp_connection_failed"
                    )
                    close_unclaimed = (
                        not entry.leases and entry.connection is connection
                    )
                    if close_unclaimed:
                        entry.connection = None
                        close_task = self._track_close_locked(connection)
                else:
                    lease = MCPLease(
                        self,
                        key,
                        connection,
                        server,
                        owner_kind,
                        owner_id,
                    )
                    assert entry.leases is not None
                    entry.leases.add(lease.lease_id)
                    return lease
            if close_unclaimed:
                await self._await_cleanup(close_task)
            raise MCPDomainError(code, retryable=False)
        finally:
            if registered_waiter:
                cleanup = asyncio.create_task(self._abandon_waiter(key, entry))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await asyncio.shield(cleanup)
                    raise

    async def _abandon_waiter(
        self,
        key: MCPConnectionKey,
        entry: _PoolEntry,
    ) -> None:
        close_task = None
        async with self._lock:
            entry.waiters = max(0, entry.waiters - 1)
            assert entry.leases is not None
            if entry.waiters == 0 and not entry.leases:
                if entry.connection is not None:
                    connection = entry.connection
                    entry.connection = None
                    close_task = self._track_close_locked(connection)
                else:
                    entry.epoch += 1
        await self._await_cleanup(close_task)

    async def probe(self, server: MCPResolvedServer) -> MCPProbeResult:
        current_task = asyncio.current_task()
        assert current_task is not None
        async with self._lock:
            if self._closed:
                raise MCPDomainError("mcp_connection_failed", retryable=False)
            self._active_probe_tasks.add(current_task)
        connection = None
        try:
            connection = await self._connect_with_retry(server)
            tools = await _list_all_tools(
                connection,
                server.draft.request_timeout_ms / 1000,
            )
        finally:
            try:
                await _close_connection(connection)
            finally:
                async with self._lock:
                    self._active_probe_tasks.discard(current_task)
        return MCPProbeResult(
            tools=tools,
            stderr_tail=getattr(connection, "stderr_tail", ""),
        )

    async def retire(self, key: MCPConnectionKey) -> None:
        close_task = None
        idle_task = None
        async with self._lock:
            entry = self._entries.setdefault(key, _PoolEntry())
            entry.retiring = True
            entry.epoch += 1
            if entry.idle_task is not None:
                idle_task = entry.idle_task
                idle_task.cancel()
            assert entry.leases is not None
            if not entry.leases and entry.connection is not None:
                connection = entry.connection
                entry.connection = None
                close_task = self._track_close_locked(connection)
        await self._await_cleanup(close_task)
        await self._await_cleanup(idle_task)

    async def invalidate(
        self,
        key: MCPConnectionKey,
        expected_connection: Any,
    ) -> None:
        """Drop one uncertain connection without retiring its configuration.

        Existing leases remain unique bookkeeping claims, but all point at the
        now-closed connection. A later acquire may establish a fresh connection
        for the same verified key.
        """
        close_task = None
        idle_task = None
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.connection is not expected_connection:
                return
            entry.epoch += 1
            entry.last_error = None
            if entry.idle_task is not None:
                idle_task = entry.idle_task
                idle_task.cancel()
            connection = entry.connection
            entry.connection = None
            if connection is not None:
                close_task = self._track_close_locked(connection)
        await self._await_cleanup(close_task)
        await self._await_cleanup(idle_task)

    async def _release(self, key: MCPConnectionKey, lease_id: str) -> None:
        close_task = None
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            assert entry.leases is not None
            entry.leases.discard(lease_id)
            if entry.leases:
                return
            if entry.retiring or self._closed:
                connection = entry.connection
                entry.connection = None
                if connection is not None:
                    close_task = self._track_close_locked(connection)
            elif entry.connection is not None and entry.idle_task is None:
                entry.idle_task = asyncio.create_task(self._idle_drain(key, entry))
        await self._await_cleanup(close_task)

    async def _idle_drain(
        self,
        key: MCPConnectionKey,
        entry: _PoolEntry,
    ) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._idle_grace_seconds)
            close_task = None
            async with self._lock:
                assert entry.leases is not None
                if (
                    self._entries.get(key) is entry
                    and not entry.leases
                    and entry.waiters == 0
                ):
                    connection = entry.connection
                    entry.connection = None
                    if connection is not None:
                        close_task = self._track_close_locked(connection)
            await self._await_cleanup(close_task)
        except asyncio.CancelledError:
            return
        finally:
            async with self._lock:
                if entry.idle_task is current_task:
                    entry.idle_task = None
                    assert entry.leases is not None
                    if (
                        self._suppress_idle_schedule == 0
                        and not self._closed
                        and not entry.retiring
                        and not entry.leases
                        and entry.waiters == 0
                        and entry.connection is not None
                    ):
                        entry.idle_task = asyncio.create_task(
                            self._idle_drain(key, entry)
                        )

    async def _drain_all_now(self) -> None:
        async with self._lock:
            self._suppress_idle_schedule += 1
            pending = [
                entry.spawn_inflight
                for entry in self._entries.values()
                if entry.spawn_inflight is not None
                and entry.waiters == 0
                and not entry.leases
            ]
            idle_tasks = [
                entry.idle_task
                for entry in self._entries.values()
                if entry.idle_task is not None
            ]
            for task in idle_tasks:
                task.cancel()
        try:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if idle_tasks:
                await asyncio.gather(*idle_tasks, return_exceptions=True)

            close_tasks = []
            async with self._lock:
                for entry in self._entries.values():
                    assert entry.leases is not None
                    if entry.idle_task is not None:
                        entry.idle_task.cancel()
                    if (
                        not entry.leases
                        and entry.waiters == 0
                        and entry.connection is not None
                    ):
                        connection = entry.connection
                        entry.connection = None
                        close_tasks.append(self._track_close_locked(connection))
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)
        finally:
            async with self._lock:
                self._suppress_idle_schedule -= 1

    async def drain_now(self) -> None:
        drain_task = asyncio.create_task(self._drain_all_now())
        try:
            await asyncio.shield(drain_task)
        except asyncio.CancelledError:
            await asyncio.shield(drain_task)
            raise

    async def _close_all(self) -> None:
        async with self._lock:
            pending = []
            close_tasks = []
            idle_tasks = []
            probe_tasks = list(self._active_probe_tasks)
            for task in probe_tasks:
                task.cancel()
            for entry in self._entries.values():
                entry.epoch += 1
                entry.retiring = True
                if entry.idle_task is not None:
                    entry.idle_task.cancel()
                    idle_tasks.append(entry.idle_task)
                if entry.spawn_inflight is not None:
                    pending.append(entry.spawn_inflight)
                if entry.connection is not None:
                    connection = entry.connection
                    entry.connection = None
                    close_tasks.append(self._track_close_locked(connection))
            close_tasks.extend(self._cleanup_tasks)
        if probe_tasks:
            await asyncio.gather(*probe_tasks, return_exceptions=True)
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        if idle_tasks:
            await asyncio.gather(*idle_tasks, return_exceptions=True)
        if pending:
            done, unfinished = await asyncio.wait(pending, timeout=2)
            for task in unfinished:
                task.cancel()
            await asyncio.gather(*done, *unfinished, return_exceptions=True)
        while True:
            async with self._lock:
                cleanup = tuple(self._cleanup_tasks)
            if not cleanup:
                break
            await asyncio.gather(*cleanup, return_exceptions=True)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close_all())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise


class _ServerConnection:
    """A live MCP connection: session + discovered tools, owned by one task."""

    def __init__(self, server: MCPServerDef) -> None:
        self.server = server
        self.session: ClientSession | None = None
        self.tools: tuple[Tool, ...] = ()
        self.ready = asyncio.get_event_loop().create_future()
        self.task: asyncio.Task | None = None
        self.stderr_tail: str = ""
        self.shutdown = asyncio.Event()

    def set_error(self, message: str) -> None:
        if not self.ready.done():
            self.ready.set_exception(RuntimeError(message))

    async def close(self) -> None:
        self.shutdown.set()
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass


def _read_tail(text: str) -> str:
    lines = text.splitlines()[-_STDERR_TAIL_LINES:]
    tail = "\n".join(lines)
    return tail[-_STDERR_TAIL_CHARS:]


async def _serve(conn: _ServerConnection) -> None:
    """Connect one server and hold its session open until shutdown."""
    server = conn.server
    try:
        if server.transport == "http":
            if not server.url:
                raise ValueError("http transport requires a url")
            transport_cm = _http_client(server)
        else:
            transport_cm = _stdio_client(server)

        async with transport_cm as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                conn.session = session
                conn.tools = tuple(listed.tools or ())
                if not conn.ready.done():
                    conn.ready.set_result(None)
                await conn.shutdown.wait()
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - degrade to server error state
        del error
        message = "mcp_connection_failed"
        logger.warning("Legacy MCP connection failed")
        conn.set_error(message)


def _stdio_client(server: MCPServerDef):
    draft = MCPServerDraft(
        display_name=server.name,
        transport="stdio",
        command=server.command,
        args=tuple(server.args),
        env=dict(server.env),
        secret_env_keys=tuple(server.env),
        cwd=server.cwd,
    )
    return _legacy_transport_context(MCPResolvedServer(server.name, draft, 0))


def _http_client(server: MCPServerDef):
    draft = MCPServerDraft(
        display_name=server.name,
        transport="http",
        url=server.url,
        headers=dict(server.headers),
        secret_header_keys=tuple(server.headers),
    )
    return _legacy_transport_context(MCPResolvedServer(server.name, draft, 0))


@asynccontextmanager
async def _legacy_transport_context(server: MCPResolvedServer):
    adapter = _transport_context(server)
    try:
        async with adapter.context as streams:
            yield streams[:2]
    finally:
        await adapter.aclose()


def _result_to_output(result) -> str:
    """Flatten an MCP ``CallToolResult`` into a model-facing string."""
    if getattr(result, "isError", False):
        return "mcp_tool_error: " + _text_content(result)
    structured = getattr(result, "structuredContent", None)
    if structured:
        import json

        return json.dumps(structured, ensure_ascii=False)
    return _text_content(result)


def _text_content(result) -> str:
    content = getattr(result, "content", None) or []
    texts: list[str] = []
    for block in content:
        block_type = getattr(block, "type", "")
        if block_type == "text":
            texts.append(getattr(block, "text", ""))
    return "\n".join(texts) if texts else "(no content)"


def _make_invoke(manager: "MCPManager", server_name: str, tool_name: str):
    """Return an async ``(args) -> ToolResult`` forwarding to an MCP tool."""

    async def invoke(args: dict) -> ToolResult:
        try:
            result = await manager.call_tool(server_name, tool_name, args)
        except TimeoutError:
            return ToolResult(ok=False, output="mcp_tool_timeout")
        except Exception as error:  # noqa: BLE001 - surface to the model
            del error
            return ToolResult(ok=False, output="mcp_tool_error")
        output = _result_to_output(result)
        return ToolResult(ok=not getattr(result, "isError", False), output=output)

    return invoke


class MCPManager:
    """Process-wide manager of MCP server connections."""

    def __init__(self, servers: list[MCPServerDef]) -> None:
        self._defs = {server.name: server for server in servers}
        self._connections: dict[str, _ServerConnection] = {}

    def enabled_servers(self) -> list[MCPServerDef]:
        return [server for server in self._defs.values() if server.enabled]

    async def start(self) -> None:
        """Pre-connect every enabled server in the background (non-blocking).

        Each server connects in its own task so a slow or failing server never
        blocks the runtime. Tools become visible in ``definitions()`` once a
        server finishes listing them.
        """
        for server in self.enabled_servers():
            if server.name not in self._connections:
                conn = _ServerConnection(server)
                self._connections[server.name] = conn
                conn.task = asyncio.create_task(_serve(conn))

    def definitions(self) -> list[ToolDefinition]:
        """Return ToolDefinitions for all successfully-connected servers.

        Synchronous (reads cached tools only), so it is safe to call from the
        engine's sync ``_tools_for_model``. A server that is still connecting or
        failed is omitted until it becomes ready.
        """
        from verilex_runtime.mcp.tools import build_mcp_tool

        definitions: list[ToolDefinition] = []
        for server in self.enabled_servers():
            conn = self._connections.get(server.name)
            if conn is None or not conn.ready.done() or conn.ready.cancelled():
                continue
            if conn.ready.exception() is not None:
                continue
            for tool in conn.tools:
                definitions.append(
                    build_mcp_tool(
                        server.name,
                        tool.name,
                        getattr(tool, "description", None) or "",
                        getattr(tool, "inputSchema", None) or {},
                        _make_invoke(self, server.name, tool.name),
                    )
                )
        return definitions

    async def ensure(self, name: str) -> dict[str, Tool]:
        """Return the named server's tools, connecting it lazily if needed.

        Raises RuntimeError if the server fails to start (stdio spawn, HTTP
        handshake, initialize), so callers can degrade gracefully.
        """
        conn = self._connections.get(name)
        if conn is None:
            server = self._defs.get(name)
            if server is None:
                raise KeyError(f"unknown mcp server: {name}")
            conn = _ServerConnection(server)
            self._connections[name] = conn
            conn.task = asyncio.create_task(_serve(conn))
        if not conn.ready.done():
            await conn.ready
        return {tool.name: tool for tool in conn.tools}

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict,
        *,
        timeout: float = DEFAULT_TOOL_TIMEOUT,
    ) -> Any:
        """Call an MCP tool on a live server connection."""
        conn = self._connections.get(server_name)
        if conn is None or conn.session is None:
            await self.ensure(server_name)
            conn = self._connections[server_name]
        if conn.session is None:
            raise RuntimeError(f"MCP server {server_name} is not connected")
        result = await asyncio.wait_for(
            conn.session.call_tool(tool_name, arguments),
            timeout=timeout,
        )
        return result

    async def verify(self, name: str) -> dict[str, Tool]:
        """Round-trip health check, re-listing tools and reconnecting if dead."""
        await self.close(name)
        return await self.ensure(name)

    async def reload(self, servers: list[MCPServerDef]) -> None:
        """Drop all connections and adopt a new server list (config reload)."""
        await self.close(None)
        self._defs = {server.name: server for server in servers}
        self._connections.clear()

    async def close(self, name: str | None = None) -> None:
        targets = [name] if name is not None else list(self._connections)
        for target in targets:
            conn = self._connections.pop(target, None)
            if conn is not None:
                await conn.close()

    async def aclose(self) -> None:
        await self.close(None)

    def server_error(self, name: str) -> str | None:
        conn = self._connections.get(name)
        if conn is not None and conn.ready.done() and not conn.ready.cancelled():
            exc = conn.ready.exception() if conn.ready.exception() is not None else None
            if exc is not None:
                return str(exc) or conn.stderr_tail or None
        return None
