"""Phase-4 gap-audit fact verification (read-only, no implementation change).

These tests are NOT regression guards for the current behavior. They exist to
confirm *facts* about five candidate gaps flagged during the stage-four backend
reference audit (OpenWorker / Codex / DeepSeek Harness). Each test documents the
observed behavior so the gap can be rated Critical / Important / Minor before the
``agent-mvp-e2e`` and ``agent-mvp-security`` specs are written.

If a gap is later fixed, the corresponding test here must be updated or removed —
it deliberately asserts the *current* (possibly undesirable) behavior, not the
desired contract.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tracemalloc
from types import SimpleNamespace

import pytest

from verilex_runtime.api.ws import TurnEventBus, _handle_subscribe
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import AuditEntry
from verilex_runtime.storage.models import EventRecord
from verilex_runtime.tools import ToolContext
from verilex_runtime.tools.terminal import _run_command, _terminate


PAYLOAD_BYTES = 8 * 1024 * 1024
_BIG_OUTPUT_COMMAND = "head -c 8388608 /dev/zero"  # 8388608 == PAYLOAD_BYTES


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return ToolContext(root_path=workspace, allow_read=True, allow_write=True)


# ---------------------------------------------------------------------------
# Gap 1: the run_command subprocess inherits the full Runtime environment,
# including any credentials that were put there to launch the Runtime.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gap1_command_subprocess_sees_runtime_environment(ctx, monkeypatch):
    """The command subprocess no longer inherits runtime-private or credential vars.

    After the stage-four fix the launch token and credential-shaped variables
    are scrubbed from the subprocess environment.
    """
    monkeypatch.setenv("VERILEX_API_TOKEN", "LEAKED-TOKEN-12345")
    result = await _run_command(ctx, {"command": "env"})
    assert "LEAKED-TOKEN-12345" not in result.output


# ---------------------------------------------------------------------------
# Gap 2: command output is now stream-read up to the 32 KiB cap and the rest is
# drained without buffering, so peak memory no longer scales with raw output.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gap2_output_buffering_memory_is_not_bounded_at_32k(ctx):
    """Peak parent-process memory stays bounded regardless of raw output size."""
    tracemalloc.start()
    try:
        result = await _run_command(ctx, {"command": _BIG_OUTPUT_COMMAND})
    finally:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    # The returned result is still truncated to the documented cap ...
    assert "truncated" in result.output
    assert len(result.output) < PAYLOAD_BYTES
    # ... and the parent no longer buffers the full payload.
    assert peak < 4 * 1024 * 1024


# ---------------------------------------------------------------------------
# Gap 3: cancellation terminates only the direct child; a grandchild spawned by
# the command is left running (orphaned) rather than being reaped with the
# process tree.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gap3_cancel_leaves_grandchild_process_running():
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
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    grandchild_pid = None
    try:
        first_line = await proc.stdout.readline()
        grandchild_pid = int(first_line.decode().strip())

        await _terminate(proc)

        # The direct child and its grandchild are both gone after teardown.
        try:
            os.kill(grandchild_pid, 0)
            grandchild_alive = True
        except ProcessLookupError:
            grandchild_alive = False
        assert grandchild_alive is False
    finally:
        if grandchild_pid is not None:
            try:
                os.kill(grandchild_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if proc.returncode is None:
            proc.kill()


# ---------------------------------------------------------------------------
# Gap 4: the per-connection WebSocket send queue has no capacity bound, so a
# slow client lets the outbox grow without limit.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gap4_send_queue_is_bounded():
    from verilex_runtime.api.ws import _ConnectionOutbox

    overflowed: list[bool] = []
    outbox = _ConnectionOutbox(maxsize=2, on_overflow=lambda: overflowed.append(True))

    outbox.put_nowait({"type": "event", "event": {"sequence": 1}})
    outbox.put_nowait({"type": "event", "event": {"sequence": 2}})
    outbox.put_nowait({"type": "event", "event": {"sequence": 3}})

    assert outbox.qsize() == 2
    assert overflowed == [True]


# ---------------------------------------------------------------------------
# Gap 5: turn.subscribe registers the live subscription before replaying
# history, so an event persisted but not yet broadcast is delivered twice.
# ---------------------------------------------------------------------------


def _view(record: EventRecord):
    return SimpleNamespace(
        thread_id=record.thread_id,
        turn_id=record.turn_id,
        sequence=record.sequence,
        type=record.type,
        payload=json.loads(record.payload_json),
        created_at=record.created_at,
    )


@pytest.mark.asyncio
async def test_gap5_subscribe_does_not_duplicate_event_broadcast_after_snapshot():
    bus = TurnEventBus()
    outbox = asyncio.Queue()
    subscriptions: set[str] = set()

    record = EventRecord(
        id="ev-3",
        thread_id="thread-1",
        turn_id="turn-1",
        sequence=3,
        type="message_delta",
        payload_json='{"text": "live"}',
        created_at="2026-08-13T00:00:03Z",
    )

    class Turns:
        def get(self, turn_id):
            return object()

        def list_events(self, turn_id, *, after=0):
            # The snapshot is taken before the live subscription, so an event
            # persisted after the snapshot arrives only via live broadcast.
            return []

    async def broadcast_late():
        await bus.publish("turn-1", record)

    broadcast_task = asyncio.create_task(broadcast_late())

    await _handle_subscribe(
        {"type": "turn.subscribe", "turnId": "turn-1", "after": 2},
        Turns(),
        bus,
        outbox,
        subscriptions,
    )
    await broadcast_task

    messages = []
    while not outbox.empty():
        messages.append(outbox.get_nowait())

    sequence_3 = [
        m
        for m in messages
        if m.get("type") == "event" and m["event"]["sequence"] == 3
    ]
    assert len(sequence_3) == 1


def test_mcp_audit_records_only_stable_identity_hashes_and_counts(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repository = AuditRepository(database)
    recorder = AuditRecorder(
        repository, now=lambda: "2026-08-29T00:00:00Z"
    )

    recorder.record_mcp_tool_call(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id="call-1",
        server_id="server-law",
        stable_name="mcp__law__lookup",
        argument_hash="a" * 64,
        output_hash="b" * 64,
        byte_count=42,
        duration_ms=7,
        status="failed",
        reason_code="mcp_tool_changed",
    )

    entry = repository.list_by_thread("thread-1")[0]
    assert entry.action == "mcp_tool_call"
    assert entry.subject == "mcp__law__lookup"
    assert entry.outcome == "failed"
    assert entry.details == {
        "serverId": "server-law",
        "stableName": "mcp__law__lookup",
        "argumentHash": "a" * 64,
        "outputHash": "b" * 64,
        "byteCount": 42,
        "durationMs": 7,
        "status": "failed",
        "reasonCode": "mcp_tool_changed",
    }


def test_generic_audit_recursively_redacts_and_bounds_nested_values(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    repository = AuditRepository(database)
    repository.insert(
        AuditEntry(
            id=None,
            thread_id="thread-1",
            turn_id=None,
            tool_call_id=None,
            action="test",
            subject="nested",
            outcome="recorded",
            details={
                "outer": {
                    "token": "API Key: sk-sensitive-value-123456789",
                    "items": [
                        "/private/cases/case-secret.txt",
                        {"long": "x" * 10_000},
                    ],
                }
            },
            created_at="2026-08-29T00:00:00Z",
        )
    )

    entry = repository.list_by_thread("thread-1")[0]
    serialized = json.dumps(entry.details, ensure_ascii=False)
    assert "sk-sensitive" not in serialized
    assert "/private/cases" not in serialized
    assert len(serialized) <= 8 * 1024
    assert entry.details["outer"]["token"] == "[内容已脱敏]"
    assert entry.details["outer"]["items"][0] == "[内容已脱敏]"
    assert entry.details["outer"]["items"][1]["long"].endswith("…")
