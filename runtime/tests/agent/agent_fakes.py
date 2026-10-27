"""Fake ChatStream implementations for Agent Engine tests."""

from __future__ import annotations

import asyncio

from verilex_runtime.providers.errors import provider_error
from verilex_runtime.providers.models import ChatDelta, ToolCallDelta


class FakeChatStream:
    """Yields a fixed sequence of deltas, then stops."""

    def __init__(self, deltas: list[ChatDelta]) -> None:
        self._deltas = deltas
        self.received_messages: list = []
        self.received_tools: list | None = None

    async def stream(self, messages, tools=None, options=None):
        self.received_messages = list(messages)
        self.received_tools = list(tools) if tools else []
        for delta in self._deltas:
            yield delta


class ErroringChatStream:
    """Raises a provider error when streamed."""

    def __init__(self, code: str) -> None:
        self._code = code

    async def stream(self, messages, tools=None, options=None):
        raise provider_error(self._code)
        yield  # pragma: no cover - make this an async generator


class CancellationProbeStream:
    """Yields deltas slowly so tests can cancel mid-stream."""

    async def stream(self, messages, tools=None, options=None):
        while True:
            yield ChatDelta(kind="text", text="x")
            await asyncio.sleep(0.001)


def fixed_factory(deltas: list[ChatDelta]):
    def factory(provider_id: str, model: str) -> FakeChatStream:
        return FakeChatStream(deltas)

    return factory


def scripted_factory(rounds: list[list[ChatDelta]]):
    """Return a factory that serves one delta list per model round.

    The engine calls the factory once per model step; each call advances to the
    next scripted round. After the script is exhausted it repeats the last round.
    """

    state = {"index": 0}

    def factory(provider_id: str, model: str) -> FakeChatStream:
        index = min(state["index"], len(rounds) - 1)
        state["index"] += 1
        return FakeChatStream(rounds[index])

    return factory


def erroring_factory(code: str):
    def factory(provider_id: str, model: str) -> ErroringChatStream:
        return ErroringChatStream(code)

    return factory


def probe_factory():
    def factory(provider_id: str, model: str) -> CancellationProbeStream:
        return CancellationProbeStream()

    return factory


def text_delta(text: str) -> ChatDelta:
    return ChatDelta(kind="text", text=text)


def reasoning_delta(text: str) -> ChatDelta:
    return ChatDelta(kind="reasoning", text=text)


def tool_delta() -> ChatDelta:
    return ChatDelta(kind="tool_call")


def tool_call_delta(tool_id: str, name: str, arguments: str) -> ChatDelta:
    return ChatDelta(
        kind="tool_call",
        tool_call=ToolCallDelta(id=tool_id, name=name, arguments=arguments),
    )
