"""Legal research guidance is scoped to legal conversations and survives later turns."""

from __future__ import annotations

import pytest

from verilex_runtime.providers.models import ChatDelta
from verilex_runtime.storage.models import TurnStatus

from agent_fakes import text_delta
from test_web_tool_exposure import _engine, web_env




class _CapturingStream:
    def __init__(self) -> None:
        self.received_messages = []

    async def stream(self, messages, tools=None, options=None):
        self.received_messages = list(messages)
        yield text_delta("完成")


class _CapturingFactory:
    def __init__(self) -> None:
        self.streams: list[_CapturingStream] = []

    def __call__(self, _provider_id: str, _model: str) -> _CapturingStream:
        stream = _CapturingStream()
        self.streams.append(stream)
        return stream


def _system_text(stream: _CapturingStream) -> str:
    return "\n".join(
        message.content
        for message in stream.received_messages
        if message.role == "system"
    )


async def _sink(record) -> None:
    del record


@pytest.mark.asyncio
async def test_legal_scene_guidance_keeps_source_identity_with_actual_tool_results(web_env):
    thread = web_env["threads"].create(
        "法律检索",
        "model-1",
        provider_id="provider-1",
        scene="legal-retrieval",
    )
    engine = _engine(web_env, [[ChatDelta(kind="text", text="unused")]])
    factory = _CapturingFactory()
    engine._chat_stream_factory = factory

    turn = engine.start(thread.id, "查一下劳动合同解除的赔偿标准")
    result = await engine.run(turn.id, _sink)

    assert result.status is TurnStatus.COMPLETED
    system_text = _system_text(factory.streams[0])
    assert system_text.count("根据用户问题选择研究范围和工具") == 1
    assert "来源名称和读取信息以真实返回为准" in system_text
    assert "元典智库（插件）" not in system_text


@pytest.mark.asyncio
async def test_general_scene_turn_does_not_inject_legal_rules(web_env):
    engine = _engine(web_env, [[ChatDelta(kind="text", text="unused")]])
    factory = _CapturingFactory()
    engine._chat_stream_factory = factory

    turn = engine.start(web_env["app_thread"].id, "普通对话")
    result = await engine.run(turn.id, _sink)

    assert result.status is TurnStatus.COMPLETED
    assert "根据用户问题选择研究范围和工具" not in _system_text(factory.streams[0])
    assert "law_search" not in _system_text(factory.streams[0])


@pytest.mark.asyncio
async def test_legal_scene_instruction_persists_across_turns(web_env):
    thread = web_env["threads"].create(
        "法律检索",
        "model-1",
        provider_id="provider-1",
        scene="legal-retrieval",
    )
    engine = _engine(
        web_env,
        [
            [text_delta("第一轮")],
            [text_delta("第二轮")],
        ],
    )
    factory = _CapturingFactory()
    engine._chat_stream_factory = factory

    first = engine.start(thread.id, "查劳动合同法")
    await engine.run(first.id, _sink)
    second = engine.start(thread.id, "继续")
    await engine.run(second.id, _sink)

    for stream in factory.streams:
        assert "根据用户问题选择研究范围和工具" in _system_text(stream)
