import asyncio
import json
from types import SimpleNamespace
import pytest
from test_engine import env, emit_into
from agent_fakes import FakeChatStream, text_delta, tool_call_delta
from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.providers.errors import provider_error
from verilex_runtime.storage.compaction_repository import CompactionRepository
from verilex_runtime.storage.models import TurnStatus


class Script:
    def __init__(self, rounds):
        self.rounds = iter(rounds)
        self.calls = []

    def __call__(self, *_):
        script = next(self.rounds)
        outer = self
        class Stream:
            async def stream(self, messages, tools=None, options=None):
                outer.calls.append((messages, tools))
                for value in script:
                    if isinstance(value, Exception):
                        raise value
                    yield value
        return Stream()


@pytest.mark.asyncio
async def test_retry_only_empty_response_and_persist_notice(env):
    threads, turns, thread = env
    script = Script([[provider_error("provider_rate_limited")], [text_delta("完成")]])
    engine = AgentEngine(threads, turns, script)
    turn = engine.start(thread.id, "test")
    records = []
    result = await engine.run(turn.id, await emit_into(records))
    assert result.status == TurnStatus.COMPLETED
    assert len(script.calls) == 2
    assert [json.loads(r.payload_json)["attempt"] for r in records if r.type == "turn_retry"] == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", [[text_delta("partial")], [tool_call_delta("x", "read_file", "{}")]])
async def test_partial_output_never_retries(env, prefix):
    threads, turns, thread = env
    script = Script([prefix + [provider_error("provider_stream_interrupted")]])
    engine = AgentEngine(threads, turns, script)
    turn = engine.start(thread.id, "test")
    result = await engine.run(turn.id, await emit_into([]))
    assert result.status == TurnStatus.FAILED
    assert len(script.calls) == 1


@pytest.mark.asyncio
async def test_stop_during_retry_wait(env):
    threads, turns, thread = env
    script = Script([[provider_error("provider_timeout")]])
    engine = AgentEngine(threads, turns, script)
    turn = engine.start(thread.id, "test")
    async def emit(record):
        if record.type == "turn_retry": engine.cancel(turn.id)
    result = await asyncio.wait_for(engine.run(turn.id, emit), 1)
    assert result.status == TurnStatus.CANCELLED
    assert len(script.calls) == 1


@pytest.mark.asyncio
async def test_retry_limit_and_auth(env):
    threads, turns, thread = env
    script = Script([[provider_error("provider_busy")]] * 3)
    engine = AgentEngine(threads, turns, script)
    turn = engine.start(thread.id, "test")
    result = await engine.run(turn.id, await emit_into([]))
    assert result.status == TurnStatus.FAILED
    assert len(script.calls) == 3
    auth = Script([[provider_error("provider_auth_failed")]])
    engine = AgentEngine(threads, turns, auth)
    turn = engine.start(thread.id, "test")
    await engine.run(turn.id, await emit_into([]))
    assert len(auth.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("degraded", [False, True])
async def test_compaction_persists_keeps_history_and_survives_restart(env, degraded):
    threads, turns, thread = env
    warm = AgentEngine(threads, turns, lambda *_: FakeChatStream([text_delta("旧回答" * 900)]))
    turn = warm.start(thread.id, "早期约定")
    await warm.run(turn.id, await emit_into([]))
    before = turns.list_messages(thread.id)
    repo = CompactionRepository(threads.repository._database)
    caps = SimpleNamespace(resolve=lambda *_: SimpleNamespace(context_window_tokens=1000))
    summaries = [[provider_error("provider_busy")]] * 2 if degraded else [[text_delta("保留早期约定")]]
    script = Script(summaries + [[text_delta("新回答")]])
    engine = AgentEngine(threads, turns, script, compactions=repo, model_capabilities=caps)
    turn = engine.start(thread.id, "继续")
    result = await engine.run(turn.id, await emit_into([]))
    assert result.status == TurnStatus.COMPLETED
    record = repo.latest_for_thread(thread.id)
    assert record and bool(record["degraded"]) == degraded
    assert len(record["summary_text"]) <= 90
    assert turns.list_messages(thread.id)[:len(before)] == before
    assert not script.calls[0][1]
    rebuilt = AgentEngine(threads, turns, script, compactions=repo)._build_context(thread)
    outbound = str(rebuilt.messages)
    assert "早期" in outbound and "继续" in outbound
    assert "旧回答" * 50 not in outbound


@pytest.mark.asyncio
async def test_unknown_window_and_single_current_turn_do_not_summarize(env):
    threads, turns, thread = env
    repo = CompactionRepository(threads.repository._database)
    script = Script([[text_delta("正常")]])
    caps = SimpleNamespace(resolve=lambda *_: SimpleNamespace(context_window_tokens=None))
    engine = AgentEngine(threads, turns, script, compactions=repo, model_capabilities=caps)
    turn = engine.start(thread.id, "长" * 2000)
    assert (await engine.run(turn.id, await emit_into([]))).status == TurnStatus.COMPLETED
    assert repo.latest_for_thread(thread.id) is None

@pytest.mark.asyncio
async def test_explicit_overflow_compacts_once_but_protocol_error_does_not(env):
    threads, turns, thread = env
    warm = AgentEngine(threads, turns, lambda *_: FakeChatStream([text_delta("旧答复")]))
    first = warm.start(thread.id, "旧问题")
    await warm.run(first.id, await emit_into([]))
    repo = CompactionRepository(threads.repository._database)
    caps = SimpleNamespace(resolve=lambda *_: SimpleNamespace(context_window_tokens=100000))
    script = Script([[provider_error("provider_context_exceeded")], [text_delta("旧问答摘要")], [text_delta("继续完成")]])
    engine = AgentEngine(threads, turns, script, compactions=repo, model_capabilities=caps)
    turn = engine.start(thread.id, "新问题")
    assert (await engine.run(turn.id, await emit_into([]))).status == TurnStatus.COMPLETED
    assert len(script.calls) == 3
    record = repo.latest_for_thread(thread.id)
    bad = Script([[provider_error("provider_protocol_error")]])
    engine = AgentEngine(threads, turns, bad, compactions=repo, model_capabilities=caps)
    turn = engine.start(thread.id, "不兼容请求")
    assert (await engine.run(turn.id, await emit_into([]))).status == TurnStatus.FAILED
    assert repo.latest_for_thread(thread.id) == record
    assert len(bad.calls) == 1


@pytest.mark.asyncio
async def test_repeated_compaction_includes_previous_summary_and_caps_output(env):
    threads, turns, thread = env
    warm = AgentEngine(threads, turns, lambda *_: FakeChatStream([text_delta("长回答" * 1000)]))
    first = warm.start(thread.id, "最初约定")
    await warm.run(first.id, await emit_into([]))
    repo = CompactionRepository(threads.repository._database)
    caps = SimpleNamespace(resolve=lambda *_: SimpleNamespace(context_window_tokens=1000))
    script = Script([[text_delta("旧摘要" * 1000)], [text_delta("较新回答" * 1000)], [text_delta("更新摘要")], [text_delta("完成")]])
    engine = AgentEngine(threads, turns, script, compactions=repo, model_capabilities=caps)
    second = engine.start(thread.id, "第二个问题")
    await engine.run(second.id, await emit_into([]))
    previous = repo.latest_for_thread(thread.id)
    assert len(previous["summary_text"]) == 90
    third = engine.start(thread.id, "第三个问题")
    assert (await engine.run(third.id, await emit_into([]))).status == TurnStatus.COMPLETED
    assert previous["summary_text"] in str(script.calls[2][0])
    assert repo.latest_for_thread(thread.id)["boundary_sequence"] > previous["boundary_sequence"]
    assert all(v.turn_id != third.id or v.sequence >= repo.latest_for_thread(thread.id)["boundary_sequence"] for v in turns.list_messages(thread.id))


@pytest.mark.asyncio
async def test_compaction_summarizes_attachment_evidence_not_only_user_caption(tmp_path):
    from test_engine import attachment_env, _chunked
    threads, turns, thread, attachments, caps = attachment_env.__wrapped__(tmp_path)
    caps.put(thread.provider_id, thread.model, False, context_window_tokens=1000)
    upload = await attachments.upload(thread.id, 'fictional.txt', 'text/plain',
        _chunked(('约定付款日是十二月三十一日。' + '虚构填充。'*500).encode()))
    script = Script([[text_delta('收到附件，等候问题。')], [text_delta('fictional.txt约定付款日为十二月三十一日。')], [text_delta('完成')]])
    engine = AgentEngine(threads, turns, script, attachments=attachments, model_capabilities=caps,
        compactions=CompactionRepository(threads.repository._database))
    first = engine.start(thread.id, '先保存材料', [upload.upload.id])
    await engine.run(first.id, await emit_into([]))
    second = engine.start(thread.id, '付款日是哪天？')
    assert (await engine.run(second.id, await emit_into([]))).status == TurnStatus.COMPLETED
    summary_input = str(script.calls[1][0])
    assert '十二月三十一日' in summary_input and 'fictional.txt' in summary_input
    assert '十二月三十一日' in str(script.calls[2][0])


@pytest.mark.asyncio
async def test_compaction_includes_verified_image_parts_in_summary_request(tmp_path):
    from test_engine import attachment_env, _chunked
    from verilex_runtime.providers.models import ImagePart
    threads, turns, thread, attachments, caps = attachment_env.__wrapped__(tmp_path)
    caps.put(thread.provider_id, thread.model, True, context_window_tokens=1000)
    upload = await attachments.upload(thread.id, 'fictional.png', 'image/png',
        _chunked(b'\x89PNG\r\n\x1a\nfictional-image-bytes'))
    script = Script([[text_delta('较长的答复。'*500)], [text_delta('图片资料摘要')], [text_delta('继续')]])
    engine = AgentEngine(threads, turns, script, attachments=attachments, model_capabilities=caps,
        compactions=CompactionRepository(threads.repository._database))
    first = engine.start(thread.id, '请记住这张图片', [upload.upload.id])
    await engine.run(first.id, await emit_into([]))
    second = engine.start(thread.id, '继续讨论图片')
    assert (await engine.run(second.id, await emit_into([]))).status == TurnStatus.COMPLETED
    parts = script.calls[1][0][-1].content
    assert isinstance(parts, tuple)
    images = [part for part in parts if isinstance(part, ImagePart)]
    assert len(images) == 1 and images[0].expected_sha256 == upload.blob.sha256
