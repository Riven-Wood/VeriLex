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
    assert "梳理法律制度还是查询具体法条" in system_text
    assert "最终回答末尾逐条列出相关法规名称、条号及实际读取到的完整条文原文" in system_text
    assert "无法取得完整原文或核实版本、效力时" in system_text
    assert "每条规范旁使用对应原文读取结果的 source_call_id" in system_text
    assert "原文链接未取得" in system_text
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
    assert "最终回答末尾逐条列出相关法规名称" not in _system_text(factory.streams[0])


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

@pytest.mark.asyncio
@pytest.mark.parametrize("scene", ["legal-retrieval", "case-retrieval"])
async def test_next_turn_uses_new_plugin_and_skills(web_env, scene):
    from dataclasses import replace
    from verilex_runtime.mcp.models import MCPServerRecord
    from verilex_runtime.storage.mcp_repository import MCPRepository

    repository = MCPRepository(web_env["database"])
    for server_id in ("plugin-a", "plugin-b"):
        repository.create_server(MCPServerRecord(
            id=server_id, display_name=server_id, transport="stdio", command="dummy", args=(),
            cwd=None, url=None, env={}, headers={}, env_refs={}, header_refs={},
            connect_timeout_ms=1000, request_timeout_ms=1000, config_revision=1,
            config_fingerprint=server_id, secret_revision=0, created_at="2026-01-01", updated_at="2026-01-01",
        ), ())
    original = web_env["mcp"]
    class Scope:
        def __init__(self, service, thread_id): self.service, self.thread_id = service, thread_id
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass
        async def refresh_catalog(self): return self.service.persisted_definitions_for_thread(self.thread_id)
    class Service:
        _repository = repository
        def _selectable_server(self, server_id): return repository.get_server(server_id)
        def turn_scope(self, thread_id, _turn_id): return Scope(self, thread_id)
        def persisted_definitions_for_thread(self, thread_id):
            return tuple(replace(original._definition, name=f"mcp__{server_id}__search", source_id=server_id)
                         for server_id in repository.selected_server_ids(thread_id))
    web_env["mcp"] = Service()
    thread = web_env["threads"].create("检索", "model-1", provider_id="provider-1", scene=scene)
    engine = _engine(web_env, [[text_delta("第一轮")], [text_delta("第二轮")]])
    class CapturingStream(_CapturingStream):
        async def stream(self, messages, tools=None, options=None):
            self.tools = tools
            async for item in super().stream(messages, tools, options): yield item
    class Factory:
        def __init__(self): self.streams = []
        def __call__(self, *_args):
            stream = CapturingStream(); self.streams.append(stream); return stream
    factory = Factory(); engine._chat_stream_factory = factory
    first = engine.start(thread.id, "第一次", search_selection={"channel":"plugin", "serverIds":["plugin-a"], "skills":["旧技能"]})
    from verilex_runtime.sessions.errors import SessionDomainError
    with pytest.raises(SessionDomainError):
        engine.start(thread.id, "并发请求", search_selection={"channel":"plugin", "serverIds":["plugin-b"], "skills":["不应生效"]})
    assert repository.selected_server_ids(thread.id) == ["plugin-a"]
    await engine.run(first.id, _sink)
    second = engine.start(thread.id, "第二次", search_selection={"channel":"plugin", "serverIds":["plugin-b"], "skills":["新技能"]})
    await engine.run(second.id, _sink)
    assert repository.selected_server_ids(thread.id) == ["plugin-b"]
    assert web_env["threads"].get(thread.id).legal_skills == ("新技能",)
    first_tools = str(factory.streams[0].tools)
    second_tools = str(factory.streams[1].tools)
    assert "mcp__plugin-a__search" in first_tools
    assert "mcp__plugin-b__search" in second_tools and "mcp__plugin-a__search" not in second_tools
    assert "新技能" in _system_text(factory.streams[1])
    assert "旧技能" not in _system_text(factory.streams[1])
    class Knowledge:
        def validate_selection(self, ids): assert ids == ["library-1"]
        def bind(self, thread_id, turn_id, ids): self.binding = (thread_id, turn_id, ids)
    knowledge = Knowledge()
    engine._knowledge_sources = knowledge
    third = engine.start(thread.id, "查知识库", knowledge_library_ids=["library-1"],
        search_selection={"channel":"knowledge", "serverIds":[], "skills":[]})
    assert repository.selected_server_ids(thread.id) == []
    assert web_env["threads"].get(thread.id).legal_channel is None
    assert knowledge.binding == (thread.id, third.id, ["library-1"])

@pytest.mark.asyncio
async def test_switching_to_online_records_channel_and_skill(web_env):
    thread = web_env["threads"].create("检索", "model-1", provider_id="provider-1", scene="legal-retrieval", legal_channel="plugin")
    engine = _engine(web_env, [[text_delta("完成")]])
    engine._mcp_service = None
    turn = engine.start(thread.id, "联网查询", search_selection={"channel":"web", "serverIds":[], "skills":["法条技能"]})
    assert web_env["threads"].get(thread.id).legal_channel == "web"
    assert web_env["threads"].get(thread.id).legal_skills == ("法条技能",)
    await engine.run(turn.id, _sink)
