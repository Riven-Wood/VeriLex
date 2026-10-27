from __future__ import annotations

import os
import asyncio
import sys
from pathlib import Path

import httpx
import pytest

from verilex_runtime.mcp import client as client_module
from verilex_runtime.mcp.client import (
    BoundedStderrCapture,
    MCPResolvedServer,
    bounded_stderr_tail,
    build_minimal_stdio_environment,
    mcp_http_client_factory,
)
from verilex_runtime.mcp.importer import MCPServerDraft


def _server(transport: str) -> MCPResolvedServer:
    if transport == "stdio":
        draft = MCPServerDraft(
            display_name="Local",
            transport="stdio",
            command="fixture",
            env={"EXPLICIT": "value"},
        )
    else:
        draft = MCPServerDraft(
            display_name="Remote",
            transport=transport,
            url="https://example.test/mcp",
            headers={"X-Test": "1"},
        )
    return MCPResolvedServer("server-1", draft, 1)


def test_http_factory_disables_environment_and_redirects(monkeypatch):
    captured = {}

    class CapturingClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", CapturingClient)

    mcp_http_client_factory(headers={"X-Test": "1"}, timeout=5.0, auth=None)

    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
    assert captured["headers"] == {"X-Test": "1"}


@pytest.mark.asyncio
async def test_legacy_invoke_does_not_echo_third_party_exception_text():
    class FailingManager:
        async def call_tool(self, *_args):
            raise ValueError("https://secret.example/?token=do-not-echo")

    invoke = client_module._make_invoke(FailingManager(), "server", "tool")
    result = await invoke({})

    assert result.ok is False
    assert result.output == "mcp_tool_error"
    assert "do-not-echo" not in result.output


def test_minimal_stdio_environment_keeps_only_allowlist_then_explicit_values():
    host = {
        "PATH": "/usr/bin",
        "HOME": "/Users/example",
        "TMPDIR": "/tmp/example",
        "LANG": "zh_CN.UTF-8",
        "LC_ALL": "zh_CN.UTF-8",
        "VERILEX_API_TOKEN": "must-not-inherit",
        "OPENAI_API_KEY": "must-not-inherit",
        "PYTHONPATH": "/host/injection",
        "SHELL": "/bin/zsh",
    }

    result = build_minimal_stdio_environment(
        {"EXPLICIT": "ok", "OPENAI_API_KEY": "deliberately-scoped"},
        environ=host,
    )

    assert result == {
        "PATH": "/usr/bin",
        "HOME": "/Users/example",
        "TMPDIR": "/tmp/example",
        "LANG": "zh_CN.UTF-8",
        "LC_ALL": "zh_CN.UTF-8",
        "EXPLICIT": "ok",
        "OPENAI_API_KEY": "deliberately-scoped",
    }


def test_stderr_tail_is_last_8192_bytes_and_replacement_safe():
    raw = b"prefix:" + (b"x" * 9000) + b"\xff"

    rendered = bounded_stderr_tail(raw)

    assert len(rendered.encode("utf-8")) <= 8192
    assert "prefix:" not in rendered
    assert rendered.endswith("\ufffd")


def test_stderr_tail_redacts_a_detected_secret_without_echo():
    rendered = bounded_stderr_tail(b"Authorization: Bearer abcdefghijklmnop")
    assert rendered == "[内容已脱敏]"
    assert "abcdefghijklmnop" not in rendered


def test_stderr_tail_scans_across_the_truncation_boundary_before_rendering():
    raw = b"x" * 100 + b"Bearer " + b"a" * 40
    rendered = bounded_stderr_tail(raw, max_bytes=40)
    assert rendered == "[内容已脱敏]"
    assert "a" * 16 not in rendered
    assert len(rendered.encode("utf-8")) <= 40


def test_stderr_capture_redacts_a_known_secret_even_without_a_label():
    secret = "plain-secret-value-123456"
    capture = BoundedStderrCapture(max_bytes=40, known_secrets=(secret,))
    try:
        assert capture.stderr_file is not None
        capture.stderr_file.write(b"x" * 100 + secret.encode())
    finally:
        capture.close()

    assert capture.tail == "[内容已脱敏]"
    assert secret not in capture.tail


def test_stderr_capture_finds_a_long_secret_crossing_the_tail_scan_boundary():
    secret = "s" * 400
    capture = BoundedStderrCapture(max_bytes=40, known_secrets=(secret,))
    try:
        assert capture.stderr_file is not None
        capture.stderr_file.write(b"x" * 1000 + secret.encode())
    finally:
        capture.close()

    assert capture.tail == "[内容已脱敏]"


def test_stderr_capture_remembers_a_sensitive_label_before_its_fixed_ring():
    capture = BoundedStderrCapture(max_bytes=40)
    try:
        assert capture.stderr_file is not None
        capture.stderr_file.write(b"Bearer very-secret-token-value" + b"x" * 10_000)
    finally:
        capture.close()

    assert capture.tail == "[内容已脱敏]"


@pytest.mark.asyncio
async def test_http_and_sse_use_distinct_sdk_adapters(monkeypatch):
    calls = []

    class Context:
        async def __aenter__(self):
            return object(), object(), lambda: None

        async def __aexit__(self, *_args):
            return None

    class SSEContext(Context):
        async def __aenter__(self):
            return object(), object()

    def streamable(*args, **kwargs):
        calls.append(("http", args, kwargs))
        return Context()

    def sse(*args, **kwargs):
        calls.append(("sse", args, kwargs))
        return SSEContext()

    monkeypatch.setattr(client_module, "streamable_http_client", streamable)
    monkeypatch.setattr(client_module, "sse_client", sse)
    http_adapter = client_module._transport_context(_server("http"))
    sse_adapter = client_module._transport_context(_server("sse"))

    http_client = http_adapter.http_client
    try:
        async with http_adapter.context:
            pass
        async with sse_adapter.context:
            pass
    finally:
        await http_client.aclose()

    assert [call[0] for call in calls] == ["http", "sse"]
    assert calls[0][2]["http_client"] is http_client
    assert "httpx_client_factory" in calls[1][2]
    assert isinstance(http_client.timeout, httpx.Timeout)
    assert http_client.follow_redirects is False
    assert os.environ.get("HTTPS_PROXY") not in repr(calls)


@pytest.mark.asyncio
async def test_transport_session_contexts_enter_and_exit_on_one_owner_task(monkeypatch):
    events = []

    class TransportContext:
        async def __aenter__(self):
            events.append(("transport-enter", __import__("asyncio").current_task()))
            return object(), object()

        async def __aexit__(self, *_args):
            events.append(("transport-exit", __import__("asyncio").current_task()))

    class Session:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            events.append(("session-enter", __import__("asyncio").current_task()))
            return self

        async def __aexit__(self, *_args):
            events.append(("session-exit", __import__("asyncio").current_task()))

        async def initialize(self):
            events.append(("initialize", __import__("asyncio").current_task()))

        async def list_tools(self, cursor=None):
            del cursor
            return type("Page", (), {"tools": [], "nextCursor": None})()

    adapter = type(
        "Adapter",
        (),
        {"context": TransportContext(), "http_client": None, "stderr_file": None},
    )()
    monkeypatch.setattr(client_module, "_transport_context", lambda _server: adapter)
    monkeypatch.setattr(client_module, "ClientSession", Session)
    pool = client_module.MCPConnectionPool()
    lease = await pool.acquire(_server("stdio"), "view", "one")
    await lease.aclose()
    await pool.drain_now()

    task_ids = {id(task) for _event, task in events}
    assert [event for event, _task in events] == [
        "transport-enter",
        "session-enter",
        "initialize",
        "session-exit",
        "transport-exit",
    ]
    assert len(task_ids) == 1


@pytest.mark.asyncio
async def test_initialize_timeout_exits_both_contexts_on_the_owner_task(monkeypatch):
    events = []

    class TransportContext:
        async def __aenter__(self):
            events.append(("transport-enter", asyncio.current_task()))
            return object(), object()

        async def __aexit__(self, *_args):
            events.append(("transport-exit", asyncio.current_task()))

    class Session:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            events.append(("session-enter", asyncio.current_task()))
            return self

        async def __aexit__(self, *_args):
            events.append(("session-exit", asyncio.current_task()))

        async def initialize(self):
            events.append(("initialize", asyncio.current_task()))
            await asyncio.Event().wait()

    adapter = type(
        "Adapter",
        (),
        {"context": TransportContext(), "http_client": None, "stderr_file": None},
    )()
    monkeypatch.setattr(client_module, "_transport_context", lambda _server: adapter)
    monkeypatch.setattr(client_module, "ClientSession", Session)
    original_timeout = client_module._same_task_timeout
    monkeypatch.setattr(
        client_module,
        "_same_task_timeout",
        lambda _seconds: original_timeout(0.02),
    )
    server = MCPResolvedServer(
        "slow-initialize",
        MCPServerDraft(
            display_name="Slow",
            transport="stdio",
            command="fixture",
            connect_timeout_ms=1_000,
            request_timeout_ms=1_000,
        ),
        1,
    )
    pool = client_module.MCPConnectionPool()

    with pytest.raises(client_module.MCPDomainError) as raised:
        await asyncio.wait_for(pool.acquire(server, "view", "one"), timeout=0.5)

    assert raised.value.code == "mcp_connect_timeout"
    assert [name for name, _task in events] == [
        "transport-enter",
        "session-enter",
        "initialize",
        "session-exit",
        "transport-exit",
        "transport-enter",
        "session-enter",
        "initialize",
        "session-exit",
        "transport-exit",
    ]
    assert len({id(task) for _name, task in events}) == 2
    await pool.aclose()


@pytest.mark.asyncio
async def test_transport_connect_timeout_is_independent_and_explicit(monkeypatch):
    entered = 0

    class HangingTransport:
        async def __aenter__(self):
            nonlocal entered
            entered += 1
            await asyncio.Event().wait()

        async def __aexit__(self, *_args):
            raise AssertionError("an unentered context must not be exited")

    def adapter(_server):
        return type(
            "Adapter",
            (),
            {
                "context": HangingTransport(),
                "http_client": None,
                "stderr_file": None,
            },
        )()

    monkeypatch.setattr(client_module, "_transport_context", adapter)
    original_timeout = client_module._same_task_timeout
    monkeypatch.setattr(
        client_module,
        "_same_task_timeout",
        lambda _seconds: original_timeout(0.02),
    )
    server = MCPResolvedServer(
        "slow-connect",
        MCPServerDraft(
            display_name="Slow",
            transport="stdio",
            command="fixture",
            connect_timeout_ms=1_000,
            request_timeout_ms=1_000,
        ),
        1,
    )
    pool = client_module.MCPConnectionPool()

    with pytest.raises(client_module.MCPDomainError) as raised:
        await asyncio.wait_for(pool.acquire(server, "view", "one"), timeout=0.5)

    assert raised.value.code == "mcp_connect_timeout"
    assert entered == 2
    await pool.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="stdio watchdog is POSIX-only")
async def test_large_stderr_is_continuously_drained_before_initialize():
    fixture = Path(__file__).with_name("fake_server.py")
    server = MCPResolvedServer(
        "noisy",
        MCPServerDraft(
            display_name="Noisy",
            transport="stdio",
            command=sys.executable,
            args=(str(fixture), "noisy-mcp"),
            connect_timeout_ms=5_000,
            request_timeout_ms=2_000,
        ),
        1,
    )
    pool = client_module.MCPConnectionPool()
    lease = await asyncio.wait_for(
        pool.acquire(server, "catalog", "noisy"),
        timeout=8,
    )
    connection = lease._connection
    assert [tool.name for tool in await lease.list_all_tools()] == ["lookup"]
    await lease.aclose()
    await pool.drain_now()

    assert len(connection.stderr_tail.encode("utf-8")) <= 8192


def test_http_and_sse_read_timeouts_use_explicit_server_budget(monkeypatch):
    captured = []

    class DummyClient:
        def __init__(self, **kwargs):
            self.timeout = kwargs["timeout"]
            self.follow_redirects = kwargs["follow_redirects"]

        async def aclose(self):
            pass

    class Context:
        async def __aenter__(self):
            return object(), object()

        async def __aexit__(self, *_args):
            pass

    monkeypatch.setattr(client_module.httpx, "AsyncClient", DummyClient)
    monkeypatch.setattr(
        client_module,
        "streamable_http_client",
        lambda *args, **kwargs: captured.append(("http", args, kwargs)) or Context(),
    )
    monkeypatch.setattr(
        client_module,
        "sse_client",
        lambda *args, **kwargs: captured.append(("sse", args, kwargs)) or Context(),
    )
    http_server = _server("http")
    sse_server = _server("sse")
    client_module._transport_context(http_server)
    client_module._transport_context(sse_server)

    assert captured[0][2]["http_client"].timeout.connect == pytest.approx(10.0)
    assert captured[0][2]["http_client"].timeout.read == pytest.approx(30.0)
    assert captured[1][2]["timeout"] == pytest.approx(10.0)
    assert captured[1][2]["sse_read_timeout"] == pytest.approx(30.0)

@pytest.mark.skipif(os.name != "posix", reason="stdio watchdog is POSIX-only")
def test_frozen_stdio_uses_small_sibling_watchdog_without_relaunching_runtime(monkeypatch, tmp_path):
    executable = tmp_path / 'sidecar' / 'verilex-runtime'
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    command, args = client_module._watchdog_argv(_server('stdio').draft)
    assert command == str(executable.with_name('verilex-mcp-watchdog'))
    assert args[:2] == ['--parent-pid', str(os.getpid())]
    assert args[-2:] == ['--', 'fixture']
    assert '--env-key' in args and 'EXPLICIT' in args
    assert '--mcp-watchdog' not in args
