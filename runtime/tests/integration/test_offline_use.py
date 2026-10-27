"""Synthetic loopback Ollama with external sockets denied by the QA plugin.
This is a protocol check, not a real downloaded model effectiveness test.
"""
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import httpx
import pytest
from verilex_runtime.providers.models import ChatMessage, ProviderDraft
from verilex_runtime.providers.ollama import OllamaProbe
from verilex_runtime.providers.openai_compatible import OpenAICompatibleChatStream
from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.storage.models import AuthMode, ProviderKind


@pytest.mark.asyncio
async def test_loopback_model_survives_external_network_failure():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def reply(self, value):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())
        def do_GET(self):
            assert self.path == '/api/tags'
            self.reply({'models':[{'name':'offline-fixture'}]})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append((self.path, body))
            if self.path == '/api/show':
                self.reply({'model_info':{'fixture.context_length':8192}})
                return
            assert self.path == '/v1/chat/completions'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            event = {'id':'offline', 'object':'chat.completion.chunk','created':0,'model':'offline-fixture',
                     'choices':[{'index':0,'delta':{'content':'本机协议测试完成'},'finish_reason':None}]}
            self.wfile.write(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    runner = Thread(target=server.serve_forever, daemon=True)
    runner.start()
    root = f'http://127.0.0.1:{server.server_port}'
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            # Invalid reserved host: no credential or real paid request, no fallback.
            remote = OpenAICompatibleChatStream(client, 'https://cloud.offline.invalid/v1', None, 'chosen-cloud-model')
            with pytest.raises(ProviderDomainError) as failure:
                _ = [delta async for delta in remote.stream([ChatMessage('user','合成检查')])]
            assert failure.value.code == 'provider_unreachable'
            assert seen == []
            draft = ProviderDraft(name='合成本机', kind=ProviderKind.OLLAMA, base_url=root,
                                  default_model='offline-fixture', auth_mode=AuthMode.NONE)
            probe = await OllamaProbe(client).probe(draft, None)
            assert probe.selected_model == 'offline-fixture'
            selected = OpenAICompatibleChatStream(client, root+'/v1', None, 'offline-fixture')
            output = [delta.text async for delta in selected.stream([ChatMessage('user','合成检查')])]
            assert ''.join(output) == '本机协议测试完成'
            assert seen[-1][1]['model'] == 'offline-fixture'
    finally:
        server.shutdown(); server.server_close(); runner.join(timeout=2)
