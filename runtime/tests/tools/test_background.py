"""Background task manager: lifecycle, buffering, limits, persistence, security."""

import asyncio
import os

import pytest

from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.storage.preferences import PreferenceRepository
from verilex_runtime.tools import ToolContext, ToolError, ToolRegistry
from verilex_runtime.tools import registry as tool_registry
from verilex_runtime.tools.background import (
    BackgroundTaskManager,
    HeadTailBuffer,
    register_background_tools,
)

HEAD_LIMIT_BYTES = 128 * 1024
READ_CHUNK_LIMIT_BYTES = 64 * 1024


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    _seed_parents(database)
    return database


@pytest.fixture
def preferences(database):
    return PreferenceRepository(database)


@pytest.fixture
def manager(database, preferences):
    return BackgroundTaskManager(database, preferences, flush_interval=0.02)


@pytest.fixture
def root(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _seed_parents(database: Database) -> None:
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO threads (id, title, provider_id, model, workspace_id, status, created_at, updated_at, archived_at, deleted_at, is_draft) VALUES (?, ?, NULL, ?, NULL, ?, ?, ?, NULL, NULL, 0)",
            (
                "thread-1",
                "Case",
                "model-1",
                "idle",
                "2026-08-25T00:00:00Z",
                "2026-08-25T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO turns (id, thread_id, status, error_code, started_at, finished_at, created_at) VALUES (?, ?, ?, NULL, ?, ?, ?)",
            (
                "turn-1",
                "thread-1",
                "completed",
                "2026-08-25T00:00:00Z",
                "2026-08-25T00:00:01Z",
                "2026-08-25T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO tool_calls (id, turn_id, provider_call_id, name, arguments_json, risk_level, status, result_json, started_at, finished_at) VALUES (?, ?, NULL, ?, ?, ?, ?, NULL, ?, NULL)",
            (
                "tc-1",
                "turn-1",
                "run_background_command",
                "{}",
                "high",
                "succeeded",
                "2026-08-25T00:00:01Z",
            ),
        )


def _make_context(
    root,
    thread_id="thread-1",
    turn_id="turn-1",
    tool_call_id="tc-1",
):
    return ToolContext(
        root_path=root,
        allow_read=True,
        allow_write=True,
        thread_id=thread_id,
        turn_id=turn_id,
        tool_call_id=tool_call_id,
    )


def _write_script(root, name: str, code: str) -> str:
    path = root / name
    path.write_text(code, encoding="utf-8")
    return f"python3 {name}"


async def _wait_terminal(manager, task_id, timeout=15.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        record = manager.get("thread-1", task_id)
        if record is not None and record["status"] != "running":
            return record
        await asyncio.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach a terminal state")


async def _wait_output(manager, task_id, minimum_bytes=1, timeout=10.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    cursor = 0
    while loop.time() < deadline:
        chunk = await manager.read("thread-1", task_id, after=cursor)
        cursor = chunk["cursor"]
        if len(chunk["text"].encode("utf-8")) >= minimum_bytes:
            return chunk
        await asyncio.sleep(0.02)
    raise AssertionError(f"task {task_id} produced no readable output")


# ---------------------------------------------------------------------------
# Head/tail buffer unit behavior


def test_head_tail_buffer_keeps_head_and_tail_with_omitted_marker():
    buffer = HeadTailBuffer(limit=8)
    buffer.append(b"AAAABBBB")
    buffer.append(b"CC")
    buffer.append(b"DDDDDDDDDDDD")

    snapshot = buffer.snapshot()

    assert snapshot.startswith(b"AAAABBBB")
    assert b"[6 bytes omitted]" in snapshot
    assert snapshot.endswith(b"DDDDDDDD")
    assert buffer.omitted_bytes == 6


def test_head_tail_buffer_without_omission_is_plain_append():
    buffer = HeadTailBuffer(limit=8)
    buffer.append(b"abc")
    buffer.append(b"def")

    assert buffer.snapshot() == b"abcdef"
    assert buffer.omitted_bytes == 0


# ---------------------------------------------------------------------------
# Start / lifecycle


@pytest.mark.asyncio
async def test_background_command_reads_quoted_filename(manager, root):
    (root / 'my document.txt').write_text('synthetic background content')
    result = await manager.start(context=_make_context(root),
        command='/bin/cat "my document.txt"', description='读取虚构文件', timeout_minutes=1)
    record = await _wait_terminal(manager, result['task_id'])
    assert record['status'] == 'completed'
    chunk = await manager.read('thread-1', result['task_id'], after=0)
    assert 'synthetic background content' in chunk['text']


@pytest.mark.asyncio
async def test_background_command_rejects_unclosed_quote(manager, root):
    with pytest.raises(ToolError) as error:
        await manager.start(context=_make_context(root), command='cat "unclosed',
                            description='读取虚构文件', timeout_minutes=1)
    assert error.value.code == 'invalid_command'


@pytest.mark.asyncio
async def test_start_returns_immediately_and_task_runs(manager, root):
    command = _write_script(root, "spin.py", "import time\ntime.sleep(30)\n")

    result = await manager.start(
        context=_make_context(root),
        command=command,
        description="long job",
        timeout_minutes=1,
    )

    assert result["status"] == "running"
    task_id = result["task_id"]
    assert manager.get("thread-1", task_id)["status"] == "running"
    await manager.stop("thread-1", task_id)


@pytest.mark.asyncio
async def test_completed_task_records_exit_code_and_final_output(manager, root):
    command = _write_script(root, "gen.py", "print('done marker')\n")

    result = await manager.start(
        context=_make_context(root), command=command, description="gen", timeout_minutes=1
    )

    record = await _wait_terminal(manager, result["task_id"])
    assert record["status"] == "completed"
    assert record["exit_code"] == 0
    assert record["finished_at"] is not None
    chunk = await manager.read("thread-1", result["task_id"], after=0)
    assert "done marker" in chunk["text"]
    assert chunk["status"] == "completed"


@pytest.mark.asyncio
async def test_failed_command_marks_task_failed(manager, root):
    command = _write_script(root, "fail.py", "import sys\nsys.exit(3)\n")

    result = await manager.start(
        context=_make_context(root), command=command, description="fail", timeout_minutes=1
    )

    record = await _wait_terminal(manager, result["task_id"])
    assert record["status"] == "failed"
    assert record["exit_code"] == 3


# ---------------------------------------------------------------------------
# Head+tail retention over a real process


@pytest.mark.asyncio
async def test_large_output_keeps_head_and_tail_with_omitted_bytes(manager, root):
    command = _write_script(
        root, "big.py", "print('x' * (400 * 1024))\nprint('TAIL-SENTINEL')\n"
    )

    result = await manager.start(
        context=_make_context(root), command=command, description="big", timeout_minutes=1
    )
    task_id = result["task_id"]
    record = await _wait_terminal(manager, task_id)

    assert record["status"] == "completed"
    assert record["omitted_bytes"] > 0

    cursor = 0
    collected = []
    while True:
        chunk = await manager.read("thread-1", task_id, after=cursor)
        cursor = chunk["cursor"]
        if not chunk["text"]:
            break
        collected.append(chunk["text"])
        assert len(chunk["text"].encode("utf-8")) <= READ_CHUNK_LIMIT_BYTES
    text = "".join(collected)
    assert text.startswith("xxx")
    assert "bytes omitted" in text
    assert "TAIL-SENTINEL" in text
    assert chunk["omitted_bytes"] == record["omitted_bytes"]


@pytest.mark.asyncio
async def test_cursor_read_returns_only_new_output(manager, root):
    command = _write_script(root, "small.py", "print('hello')\nprint('world')\n")

    result = await manager.start(
        context=_make_context(root), command=command, description="small", timeout_minutes=1
    )
    task_id = result["task_id"]
    await _wait_terminal(manager, task_id)

    first = await manager.read("thread-1", task_id, after=0)
    assert "hello" in first["text"]
    assert "world" in first["text"]
    second = await manager.read("thread-1", task_id, after=first["cursor"])
    assert second["text"] == ""
    assert second["cursor"] == first["cursor"]


# ---------------------------------------------------------------------------
# Stop semantics


@pytest.mark.asyncio
async def test_stop_terminates_process_group_with_grandchildren(manager, root):
    command = _write_script(
        root,
        "tree.py",
        "import subprocess, time\n"
        "child = subprocess.Popen(['sleep', '60'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n",
    )

    result = await manager.start(
        context=_make_context(root), command=command, description="tree", timeout_minutes=30
    )
    task_id = result["task_id"]
    chunk = await _wait_output(manager, task_id, minimum_bytes=1)
    grandchild_pid = int(chunk["text"].strip().splitlines()[0])

    stopped = await manager.stop("thread-1", task_id)

    assert stopped["status"] == "stopped"
    record = await _wait_terminal(manager, task_id)
    assert record["status"] == "stopped"
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 5.0
    while loop.time() < deadline:
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("grandchild process survived task stop")


@pytest.mark.asyncio
async def test_stop_on_terminal_task_is_rejected(manager, root):
    command = _write_script(root, "quick.py", "print('ok')\n")
    result = await manager.start(
        context=_make_context(root), command=command, description="q", timeout_minutes=1
    )
    task_id = result["task_id"]
    await _wait_terminal(manager, task_id)

    with pytest.raises(ToolError) as error:
        await manager.stop("thread-1", task_id)

    assert error.value.code == "background_task_not_running"


@pytest.mark.asyncio
async def test_stop_unknown_task_is_rejected(manager, root):
    with pytest.raises(ToolError) as error:
        await manager.stop("thread-1", "missing-task")
    assert error.value.code == "background_task_not_found"


# ---------------------------------------------------------------------------
# Expiry


@pytest.mark.asyncio
async def test_task_expires_after_effective_deadline(database, preferences, root):
    manager = BackgroundTaskManager(
        database, preferences, minute_seconds=0.1, flush_interval=0.02
    )
    command = _write_script(root, "forever.py", "import time\ntime.sleep(30)\n")

    result = await manager.start(
        context=_make_context(root), command=command, description="f", timeout_minutes=1
    )

    record = await _wait_terminal(manager, result["task_id"])
    assert record["status"] == "expired"


# ---------------------------------------------------------------------------
# Concurrency limit


@pytest.mark.asyncio
async def test_fourth_concurrent_task_is_rejected_until_one_stops(manager, root):
    command = _write_script(root, "hold.py", "import time\ntime.sleep(30)\n")
    context = _make_context(root, tool_call_id="tc-1")
    task_ids = []
    for index in range(3):
        result = await manager.start(
            context=context, command=command, description=f"hold {index}", timeout_minutes=30
        )
        task_ids.append(result["task_id"])

    with pytest.raises(ToolError) as error:
        await manager.start(
            context=context, command=command, description="fourth", timeout_minutes=30
        )
    assert error.value.code == "background_task_limit_reached"

    await manager.stop("thread-1", task_ids[0])
    await _wait_terminal(manager, task_ids[0])

    result = await manager.start(
        context=context, command=command, description="fourth retry", timeout_minutes=30
    )
    assert result["status"] == "running"
    await manager.stop("thread-1", result["task_id"])
    for task_id in task_ids[1:]:
        await manager.stop("thread-1", task_id)


# ---------------------------------------------------------------------------
# Thread ownership


@pytest.mark.asyncio
async def test_cross_thread_access_is_hidden(manager, root):
    command = _write_script(root, "spin.py", "import time\ntime.sleep(30)\n")
    result = await manager.start(
        context=_make_context(root), command=command, description="spin", timeout_minutes=1
    )
    task_id = result["task_id"]

    with pytest.raises(ToolError) as read_error:
        await manager.read("thread-2", task_id)
    with pytest.raises(ToolError) as stop_error:
        await manager.stop("thread-2", task_id)

    assert read_error.value.code == "background_task_not_found"
    assert stop_error.value.code == "background_task_not_found"
    assert manager.get("thread-2", task_id) is None
    await manager.stop("thread-1", task_id)


# ---------------------------------------------------------------------------
# Security chain parity with run_command


@pytest.mark.asyncio
async def test_security_rejections_match_run_command_semantics(manager, root):
    context = _make_context(root)
    for command, expected_code in (
        ("definitely-not-a-command-xyz --flag", "command_not_found"),
    ):
        with pytest.raises(ToolError) as error:
            await manager.start(
                context=context,
                command=command,
                description="rejected",
                timeout_minutes=1,
            )
        assert error.value.code == expected_code


@pytest.mark.asyncio
async def test_background_env_is_sanitized(manager, root, monkeypatch):
    monkeypatch.setenv("VERILEX_API_TOKEN", "LEAKED-TOKEN-12345")
    monkeypatch.setenv("MY_SECRET_KEY", "LEAKED-SECRET-67890")

    result = await manager.start(
        context=_make_context(root), command="env", description="env", timeout_minutes=1
    )
    await _wait_terminal(manager, result["task_id"])
    chunk = await manager.read("thread-1", result["task_id"], after=0)

    assert "LEAKED-TOKEN-12345" not in chunk["text"]
    assert "LEAKED-SECRET-67890" not in chunk["text"]
    assert "PATH" in chunk["text"]


# ---------------------------------------------------------------------------
# Persistence and recovery


@pytest.mark.asyncio
async def test_running_output_is_flushed_to_database(manager, root, database):
    command = _write_script(
        root,
        "drip.py",
        "import time\n"
        "for _ in range(40):\n"
        "    print('tick', flush=True)\n"
        "    time.sleep(0.05)\n",
    )

    result = await manager.start(
        context=_make_context(root), command=command, description="drip", timeout_minutes=5
    )

    async def _flushed_row():
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 10.0
        while loop.time() < deadline:
            with database.connect() as connection:
                row = connection.execute(
                    "SELECT status, output_text FROM background_tasks WHERE id = ?",
                    (result["task_id"],),
                ).fetchone()
            if row is not None and row["output_text"] and "tick" in row["output_text"]:
                return row
            await asyncio.sleep(0.02)
        pytest.fail("running output was never flushed to the database")

    row = await _flushed_row()
    assert row["status"] == "running"
    await manager.stop("thread-1", result["task_id"])
    final = await _wait_terminal(manager, result["task_id"])
    assert final["status"] == "stopped"
    assert final["finished_at"] is not None


def test_recover_marks_leftover_running_rows_interrupted(database, preferences, root):
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO background_tasks (id, thread_id, turn_id, tool_call_id, command, description, status, exit_code, output_text, omitted_bytes, started_at, finished_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, ?, NULL, ?)",
            (
                "leftover",
                "thread-1",
                "turn-1",
                "tc-1",
                "python3 spin.py",
                "leftover job",
                "running",
                "partial",
                "2026-08-25T00:00:02Z",
                "2026-08-25T00:00:01Z",
            ),
        )
    manager = BackgroundTaskManager(database, preferences)

    recovered = manager.recover()

    assert recovered == 1
    assert manager.get("thread-1", "leftover")["status"] == "interrupted"


@pytest.mark.asyncio
async def test_shutdown_all_terminates_and_marks_interrupted(manager, root, database):
    command = _write_script(root, "spin.py", "import time\ntime.sleep(30)\n")
    result = await manager.start(
        context=_make_context(root), command=command, description="spin", timeout_minutes=30
    )

    await manager.shutdown_all()

    record = manager.get("thread-1", result["task_id"])
    assert record["status"] == "interrupted"


# ---------------------------------------------------------------------------
# Tool registration and execution


@pytest.mark.asyncio
async def test_background_tools_registered_with_expected_metadata(database, preferences):
    registry = ToolRegistry()
    register_background_tools(registry, BackgroundTaskManager(database, preferences), preferences)

    start_tool = registry.get("run_background_command")
    assert start_tool is not None
    assert start_tool.risk_level is RiskLevel.HIGH
    assert start_tool.category is tool_registry.ToolCategory.TERMINAL
    assert start_tool.scopes == frozenset({tool_registry.ToolScope.EXECUTE})
    assert start_tool.requires_workspace is True

    output_tool = registry.get("background_task_output")
    assert output_tool is not None
    assert output_tool.risk_level is RiskLevel.LOW
    assert output_tool.scopes == frozenset({tool_registry.ToolScope.READ})
    assert output_tool.requires_workspace is False

    stop_tool = registry.get("background_task_stop")
    assert stop_tool is not None
    assert stop_tool.risk_level is RiskLevel.LOW
    assert stop_tool.scopes == frozenset({tool_registry.ToolScope.EXECUTE})
    assert stop_tool.requires_workspace is False


@pytest.mark.asyncio
async def test_run_background_command_tool_roundtrip(manager, preferences, root):
    registry = ToolRegistry()
    register_background_tools(registry, manager, preferences)
    preferences.set_int("background_task_max_minutes", 5)
    command = _write_script(root, "mark.py", "print('marker output')\n")
    context = _make_context(root)

    started = await registry.get("run_background_command").execute(
        context,
        {"command": command, "description": "produce marker", "timeout_minutes": 30},
    )

    assert started.ok is True
    assert "task_id" in started.output
    assert "5" in started.output
    task_id = manager.list_for_thread("thread-1")[-1]["id"]

    await _wait_terminal(manager, task_id)
    read = await registry.get("background_task_output").execute(
        context, {"task_id": task_id}
    )
    assert read.ok is True
    assert "marker output" in read.output


@pytest.mark.asyncio
async def test_background_task_output_tool_rejects_unknown_task(manager, preferences, root):
    registry = ToolRegistry()
    register_background_tools(registry, manager, preferences)

    result = await registry.get("background_task_output").execute(
        _make_context(root), {"task_id": "no-such-task"}
    )

    assert result.ok is False
    assert "background_task_not_found" in result.output
