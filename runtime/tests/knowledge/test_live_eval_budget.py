"""Offline checks only: never loads a provider or sends a live request."""
import importlib.util
import json
from pathlib import Path
import sys

import httpx
import pytest


def load_eval(monkeypatch):
    scripts = Path(__file__).resolve().parents[3] / 'scripts' / 'knowledge'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('knowledge_live_eval', scripts / 'deepseek_eval.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_budget_persists_before_dispatch_and_cannot_exceed_cap(tmp_path, monkeypatch):
    module = load_eval(monkeypatch)
    ledger = tmp_path / 'budget.json'
    ledger.write_text(json.dumps({'requests': 59, 'reserved_usd': .2}))
    transport = module.BudgetTransport(ledger)
    await transport.inner.aclose()
    observed = []
    async def respond(request):
        observed.append(json.loads(request.content))
        assert json.loads(ledger.read_text())['requests'] == 60
        return httpx.Response(200)
    transport.inner = httpx.MockTransport(respond)
    request = httpx.Request('POST', 'https://api.deepseek.com/chat/completions', json={'messages': []})
    await transport.handle_async_request(request)
    assert observed[0]['max_tokens'] == 1536
    assert observed[0]['thinking'] == {'type': 'disabled'}
    await transport.aclose()
    restarted = module.BudgetTransport(ledger)
    try:
        with pytest.raises(RuntimeError, match='budget_exhausted'):
            await restarted.handle_async_request(request)
        assert json.loads(ledger.read_text())['requests'] == 60
    finally:
        await restarted.aclose()


@pytest.mark.asyncio
async def test_budget_rejects_cost_overrun_and_other_endpoints(tmp_path, monkeypatch):
    module = load_eval(monkeypatch)
    ledger = tmp_path / 'budget.json'
    ledger.write_text(json.dumps({'requests': 1, 'reserved_usd': 1}))
    transport = module.BudgetTransport(ledger)
    try:
        for url in ('https://example.com/chat/completions', 'http://api.deepseek.com/chat/completions'):
            with pytest.raises(RuntimeError, match='endpoint_rejected'):
                await transport.handle_async_request(httpx.Request('POST', url, json={}))
        with pytest.raises(RuntimeError, match='budget_exhausted'):
            await transport.handle_async_request(httpx.Request('POST', 'https://api.deepseek.com/chat/completions', json={}))
        assert json.loads(ledger.read_text())['requests'] == 1
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_smaller_authorized_budget_stops_at_its_own_cap(tmp_path, monkeypatch):
    module = load_eval(monkeypatch)
    ledger = tmp_path / 'small-budget.json'
    ledger.write_text(json.dumps({'requests': 10, 'reserved_usd': .05}))
    transport = module.BudgetTransport(ledger, max_requests=10, max_usd=.2)
    try:
        with pytest.raises(RuntimeError, match='budget_exhausted'):
            await transport.handle_async_request(httpx.Request('POST', 'https://api.deepseek.com/chat/completions', json={}))
        assert json.loads(ledger.read_text())['requests'] == 10
        assert transport.blocked_reason == 'evaluation_budget_exhausted'
    finally:
        await transport.aclose()
    for options in ({'max_requests': 61}, {'max_requests': 0}, {'max_usd': 2}, {'max_usd': float('nan')}):
        with pytest.raises(ValueError): module.BudgetTransport(ledger, **options)


def test_long_acceptance_fixture_targets_are_exact_and_deep(monkeypatch):
    load_eval(monkeypatch)
    from long_fixture import corpus
    sources, questions = corpus()
    source = sources[0]
    assert len(source.raw) > 4 * 1024 * 1024
    assert source.text.startswith('\ufeff') and '\r\n' in source.text
    positions = [source.text.index(q['required_passage']) for q in questions]
    assert positions[0] < 100 and positions[1] > 700000 and positions[2] > 1500000
    assert all(q['required_passage'].split('：')[0] in q['query'] for q in questions)
