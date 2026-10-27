"""Authenticated WebSocket channel for streaming agent Turns.

The endpoint is ``/v1/ws`` and authenticates the launch token via the
``Sec-WebSocket-Protocol`` subprotocol value, mirroring the HTTP token header.

Client -> server messages:

- ``turn.start``  (``threadId``, ``content``, ``attachmentUploadIds``): create
  and run a Turn. ``content`` may be empty when Upload ids are present, but the
  two must not both be empty.
- ``turn.stop``   (``turnId``): cancel a running Turn.
- ``turn.subscribe`` (``turnId``, ``after``): replay persisted events and keep
  streaming new ones.

Server -> client messages:

- ``ack``    (``turnId``, ``status``): synchronous acknowledgement.
- ``event``  (unified event object): streamed/replayed events.
- ``error``  (``code``, ``message``, ``retryable``): request rejection.

Events are always persisted before they are broadcast; reconnects replay by
``sequence`` and never re-execute the model.
"""

from __future__ import annotations

from verilex_runtime.tools.registry import ToolError

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from verilex_runtime.agent.engine import AgentEngine, ApprovalDelivery
from verilex_runtime.agent.events import EventType, wire_event, wire_view
from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.contract_review.errors import ContractReviewError
from verilex_runtime.auth import launch_token_matches
from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.sessions.errors import SessionDomainError
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.models import EventRecord

logger = logging.getLogger(__name__)

# Upper bound for the per-connection send queue. Event frames are replayed by
# sequence on reconnect, so a slow client is disconnected once its queue fills
# instead of letting memory grow without bound.
_SEND_QUEUE_MAXSIZE = 1024


class _ConnectionOutbox:
    """Bounded per-connection send queue with overflow-triggered disconnect.

    Replayable event frames use ``put_nowait``; when the queue is full the
    overflow callback fires so the connection can close and the client can
    reconnect and replay from its last sequence. Control frames (ack/error)
    use ``put`` and wait for space instead of being dropped.
    """

    def __init__(self, maxsize: int, on_overflow: Callable[[], None]) -> None:
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._on_overflow = on_overflow

    async def get(self) -> dict:
        return await self._queue.get()

    def put_nowait(self, item: dict) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self._on_overflow()

    async def put(self, item: dict) -> None:
        await self._queue.put(item)

    def qsize(self) -> int:
        return self._queue.qsize()


async def _close_websocket(websocket: WebSocket) -> None:
    try:
        await websocket.close(code=1011)
    except Exception:
        pass


def _make_overflow_callback(websocket: WebSocket) -> Callable[[], None]:
    """Return a once-only callback that closes the socket on queue overflow."""
    triggered = False

    def on_overflow() -> None:
        nonlocal triggered
        if triggered:
            return
        triggered = True
        logger.warning(
            "WebSocket send queue overflowed; closing connection so the "
            "client can reconnect and replay by sequence"
        )
        asyncio.create_task(_close_websocket(websocket))

    return on_overflow


class TurnEventBus:
    """Broadcasts persisted events to subscribed WebSocket outboxes."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set] = {}

    def subscribe(self, turn_id: str, outbox: asyncio.Queue) -> None:
        self._subscribers.setdefault(turn_id, set()).add(outbox)

    def unsubscribe(self, turn_id: str, outbox: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(turn_id)
        if subscribers is None:
            return
        subscribers.discard(outbox)
        if not subscribers:
            self._subscribers.pop(turn_id, None)

    async def publish(self, turn_id: str, record: EventRecord) -> None:
        message = {"type": "event", "event": wire_event(record)}
        for outbox in list(self._subscribers.get(turn_id, ())):
            outbox.put_nowait(message)

    def publish_storage_failure(self, turn_id: str) -> None:
        """End the live UI even when storage is unavailable.

        This frame is intentionally not replayable: persistence itself failed.
        Startup recovery will mark the leftover running turn interrupted once
        SQLite is available again.
        """
        message = {
            "type": "error",
            "turnId": turn_id,
            "code": "internal_error",
            "message": "本地会话记录写入失败，请重启应用后重试。",
            "retryable": False,
        }
        for outbox in list(self._subscribers.get(turn_id, ())):
            outbox.put_nowait(message)


async def _send_loop(websocket: WebSocket, outbox: asyncio.Queue) -> None:
    try:
        while True:
            message = await outbox.get()
            await websocket.send_json(message)
    except asyncio.CancelledError:
        raise
    except WebSocketDisconnect:
        pass


def _error_message(
    code: str,
    message: str,
    retryable: bool,
    *,
    request_type: str | None = None,
    turn_id: str | None = None,
    tool_call_id: str | None = None,
) -> dict:
    frame = {
        "type": "error",
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if request_type is not None:
        frame["requestType"] = request_type
    if turn_id is not None:
        frame["turnId"] = turn_id
    if tool_call_id is not None:
        frame["toolCallId"] = tool_call_id
    return frame


def _make_run_failure_guard(
    turn_id: str, turns: TurnService, bus: "TurnEventBus"
) -> Callable[[asyncio.Task], None]:
    """Finalize a turn whose run task escaped with an unexpected exception.

    engine.run normally maps every failure to a stable event, but BaseExceptions
    (or a failing finalize) escape into the fire-and-forget task where nobody
    retrieves them. Without this guard the turn would stay running forever and
    the thread would reject every new turn with ``turn_conflict``.
    """

    def guard(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        logger.error(
            "agent run task for turn %s escaped with %s: %s",
            turn_id,
            type(error).__name__,
            error,
        )
        try:
            turns.fail(turn_id, "internal_error")
        except sqlite3.Error:
            bus.publish_storage_failure(turn_id)
            return
        except Exception:
            # A SQLite failure can happen after the Turn row became terminal
            # but before its terminal event was appended. Distinguish that
            # partial finalize from a fully persisted terminal Turn.
            if isinstance(error, sqlite3.Error):
                try:
                    terminal_event_exists = any(
                        event.type
                        in {
                            EventType.TURN_COMPLETED.value,
                            EventType.TURN_FAILED.value,
                            EventType.TURN_CANCELLED.value,
                        }
                        for event in turns.list_events(turn_id)
                    )
                except Exception:
                    terminal_event_exists = False
                if not terminal_event_exists:
                    bus.publish_storage_failure(turn_id)
            return
        try:
            record = turns.append_event(
                turn_id,
                EventType.TURN_FAILED.value,
                {"errorCode": "internal_error", "retryable": False},
            )
        except Exception:
            bus.publish_storage_failure(turn_id)
            return
        asyncio.create_task(bus.publish(turn_id, record))

    return guard


async def _handle_start(
    message: dict,
    engine: AgentEngine,
    turns: TurnService,
    bus: TurnEventBus,
    outbox: asyncio.Queue,
    subscriptions: set[str],
) -> None:
    thread_id = message.get("threadId")
    content = message.get("content")
    upload_ids = message.get("attachmentUploadIds", [])
    replace_turn_id = message.get("replaceTurnId")
    knowledge_ids = message.get("knowledgeLibraryIds", [])
    litigation_materials = message.get("litigationMaterials")
    editor_context = message.get("editorContext")
    contract_editor_context = message.get("contractEditorContext")
    approval_mode = message.get("approvalMode", "ask")
    reasoning_effort = message.get("reasoningEffort", "auto")
    valid_upload_ids = isinstance(upload_ids, list) and all(
        isinstance(value, str) and value for value in upload_ids
    )
    if (
        not isinstance(thread_id, str)
        or not isinstance(content, str)
        or not isinstance(knowledge_ids, list)
        or len(knowledge_ids) > 10
        or any(not isinstance(i, str) or not i for i in knowledge_ids)
        or (litigation_materials is not None and not isinstance(litigation_materials, dict))
        or (editor_context is not None and not isinstance(editor_context, dict))
        or (contract_editor_context is not None and not isinstance(contract_editor_context, dict))
        or (replace_turn_id is not None and (not isinstance(replace_turn_id, str) or not replace_turn_id))
        or not valid_upload_ids
        or not isinstance(approval_mode, str)
        or reasoning_effort not in {"auto", "low", "medium", "high"}
        or (not content.strip() and not upload_ids and replace_turn_id is None)
    ):
        await outbox.put(
            _error_message("invalid_turn_request", "Turn request is invalid.", False)
        )
        return

    try:
        turn = engine.start(
            thread_id,
            content,
            attachment_upload_ids=upload_ids,
            **({"replace_turn_id": replace_turn_id} if replace_turn_id is not None else {}),
            **({"litigation_materials": litigation_materials} if litigation_materials is not None else {}),
            **({"editor_context": editor_context} if editor_context is not None else {}),
            **({"contract_editor_context": contract_editor_context} if contract_editor_context is not None else {}),
            **({"knowledge_library_ids": knowledge_ids} if knowledge_ids else {}),
            approval_mode=approval_mode,
            reasoning_effort=reasoning_effort,
        )
    except (
        SessionDomainError,
        AttachmentDomainError,
        ProviderDomainError,
    ) as error:
        await outbox.put(_error_message(error.code, error.message, error.retryable))
        return
    except (ToolError, ContractReviewError) as error:
        await outbox.put(_error_message(error.code, error.message, False))
        return
    except WebSocketDisconnect:
        raise
    except Exception:
        # A raw storage or mapping failure must reject this request without
        # tearing down the whole socket.
        await outbox.put(
            _error_message(
                "internal_error", "The Runtime could not complete the request.", False
            )
        )
        return

    bus.subscribe(turn.id, outbox)
    subscriptions.add(turn.id)
    await outbox.put({"type": "ack", "turnId": turn.id, "status": "queued"})

    async def emit(record: EventRecord) -> None:
        await bus.publish(record.turn_id, record)

    task = asyncio.create_task(engine.run(turn.id, emit))
    task.add_done_callback(_make_run_failure_guard(turn.id, turns, bus))


async def _handle_stop(
    message: dict,
    engine: AgentEngine,
    outbox: asyncio.Queue,
) -> None:
    turn_id = message.get("turnId")
    if not isinstance(turn_id, str):
        await outbox.put(
            _error_message("invalid_turn_request", "Turn request is invalid.", False)
        )
        return
    cancelled = engine.cancel(turn_id)
    await outbox.put(
        {"type": "ack", "turnId": turn_id, "status": "cancelling" if cancelled else "not_active"}
    )


async def _handle_resolve(
    message: dict,
    engine: AgentEngine,
    turns: TurnService,
    bus: TurnEventBus,
    outbox: asyncio.Queue,
) -> None:
    turn_id = message.get("turnId")
    tool_call_id = message.get("toolCallId")
    decision = message.get("decision")
    reason = message.get("reason")
    if (
        not isinstance(turn_id, str)
        or not isinstance(tool_call_id, str)
        or decision not in ("allowed", "denied")
    ):
        await outbox.put(
            _error_message(
                "invalid_turn_request",
                "Turn request is invalid.",
                False,
                request_type="turn.resolve",
                turn_id=turn_id if isinstance(turn_id, str) else None,
                tool_call_id=(
                    tool_call_id if isinstance(tool_call_id, str) else None
                ),
            )
        )
        return
    # Production Uvicorn bounds the complete WebSocket frame at 16 MiB. Keep
    # the full reason here so AgentEngine can scan across its 500-char storage
    # boundary; the engine applies the tighter 8 KiB sensitive-scan limit.
    if not isinstance(reason, str):
        reason = None
    try:
        delivery = engine.resolve(turn_id, tool_call_id, decision, reason)
    except SessionDomainError as error:
        await outbox.put(
            _error_message(
                error.code,
                error.message,
                error.retryable,
                request_type="turn.resolve",
                turn_id=turn_id,
                tool_call_id=tool_call_id,
            )
        )
        return
    except Exception:
        await outbox.put(
            _error_message(
                "internal_error",
                "The Runtime could not complete the request.",
                False,
                request_type="turn.resolve",
                turn_id=turn_id,
                tool_call_id=tool_call_id,
            )
        )
        return
    await outbox.put(
        {
            "type": "ack",
            "requestType": "turn.resolve",
            "turnId": turn_id,
            "toolCallId": tool_call_id,
            "status": "resolved",
        }
    )
    if delivery is ApprovalDelivery.RESTART_RESUME:
        async def emit(record: EventRecord) -> None:
            await bus.publish(record.turn_id, record)

        task = asyncio.create_task(
            engine.resume_approval(turn_id, tool_call_id, emit)
        )
        task.add_done_callback(_make_run_failure_guard(turn_id, turns, bus))


async def _handle_subscribe(
    message: dict,
    turns: TurnService,
    bus: TurnEventBus,
    outbox: asyncio.Queue,
    subscriptions: set[str],
) -> None:
    turn_id = message.get("turnId")
    after = message.get("after", 0)
    if not isinstance(turn_id, str) or not isinstance(after, int) or after < 0:
        await outbox.put(
            _error_message("invalid_turn_request", "Turn request is invalid.", False)
        )
        return
    try:
        turns.get(turn_id)
    except SessionDomainError as error:
        await outbox.put(_error_message(error.code, error.message, error.retryable))
        return

    # Snapshot history *before* subscribing. Snapshot and subscribe are both
    # synchronous, so no event can be persisted between them; replayed events
    # land ahead of the ack and any live events, in sequence order, with no
    # duplication.
    views = turns.list_events(turn_id, after=after)
    bus.subscribe(turn_id, outbox)
    subscriptions.add(turn_id)
    for view in views:
        outbox.put_nowait({"type": "event", "event": wire_view(view)})
    await outbox.put({"type": "ack", "turnId": turn_id, "status": "subscribed"})


async def _connection(
    websocket: WebSocket,
    settings: RuntimeSettings,
    engine: AgentEngine,
    turns: TurnService,
    bus: TurnEventBus,
) -> None:
    token = websocket.headers.get("sec-websocket-protocol")
    if not launch_token_matches(token, settings.api_token):
        await websocket.close(code=1008)
        return
    await websocket.accept(subprotocol=token)

    outbox: _ConnectionOutbox = _ConnectionOutbox(
        _SEND_QUEUE_MAXSIZE,
        _make_overflow_callback(websocket),
    )
    subscriptions: set[str] = set()
    send_task = asyncio.create_task(_send_loop(websocket, outbox))
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await outbox.put(
                    _error_message("invalid_turn_request", "Turn request is invalid.", False)
                )
                continue
            if not isinstance(message, dict):
                continue
            message_type = message.get("type")
            try:
                if message_type == "turn.start":
                    await _handle_start(message, engine, turns, bus, outbox, subscriptions)
                elif message_type == "turn.stop":
                    await _handle_stop(message, engine, outbox)
                elif message_type == "turn.subscribe":
                    await _handle_subscribe(message, turns, bus, outbox, subscriptions)
                elif message_type == "turn.resolve":
                    await _handle_resolve(
                        message, engine, turns, bus, outbox
                    )
                else:
                    await outbox.put(
                        _error_message(
                            "invalid_turn_request", "Turn request is invalid.", False
                        )
                    )
            except WebSocketDisconnect:
                raise
            except Exception:
                # Handler bugs must reject the request with an error frame, not
                # close the connection without any feedback.
                await outbox.put(
                    _error_message(
                        "internal_error",
                        "The Runtime could not complete the request.",
                        False,
                    )
                )
    except WebSocketDisconnect:
        pass
    finally:
        for turn_id in subscriptions:
            bus.unsubscribe(turn_id, outbox)
        send_task.cancel()
        try:
            await send_task
        except (asyncio.CancelledError, Exception):
            pass


def build_ws_router(
    settings: RuntimeSettings,
    engine: AgentEngine,
    turns: TurnService,
    bus: TurnEventBus,
) -> APIRouter:
    router = APIRouter()

    @router.websocket("/v1/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await _connection(websocket, settings, engine, turns, bus)

    return router
