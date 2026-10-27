"""Exercise real Node MCP handshake/dispatch with synthetic fetch, no network/model."""
import asyncio
import json
from pathlib import Path

import pytest

from verilex_runtime.knowledge.ima_mcp_client import ImaMcpClient, ASSETS
from verilex_runtime.mcp.client import MCPResolvedServer, _connect_sdk
from verilex_runtime.mcp.importer import MCPServerDraft
from verilex_runtime.tools.registry import ToolError


def credentials(client_id='synthetic-client', api_key='synthetic-key'):
    return {'client_id': client_id, 'api_key': api_key}


@pytest.fixture
def client(tmp_path):
    log = tmp_path / 'requests.jsonl'
    preload = tmp_path / 'fetch.mjs'
    preload.write_text('''import fs from 'node:fs';
const log = ''' + json.dumps(str(log)) + ''';
globalThis.fetch = async (url, options) => {
  const args = JSON.parse(options.body);
  fs.appendFileSync(log, JSON.stringify({url, args, redirect: options.redirect,
    hasSignal: !!options.signal}) + '\\n');
  if (args.query === 'error') return new Response(JSON.stringify({code: 20004, msg: 'PRIVATE'}));
  if (args.query === 'oversize') return new Response('x'.repeat(4*1024*1024+1));
  if (args.query === 'malformed') return new Response('not json');
  if (args.query === 'redirect') return new Response('', {status: 302});
  if (args.media_id === 'restricted') return new Response(JSON.stringify({code: 220030, msg: 'PRIVATE'}));
  let data = {info_list: [{media_id:'doc', title:'合成资料', highlight_content:'第十条：“保留”；\\r\\n第二行。'}], is_end:true};
  if (url.endsWith('get_media_info')) data = {media_type:11, notebook_ext_info:{notebook_id:'note1'}};
  if (url.endsWith('get_doc_content')) data = {content:'第十条：“保留”；\\r\\n第二行。'};
  return new Response(JSON.stringify({code:0, data}));
};
''')
    class SyntheticClient(ImaMcpClient):
        def command(self):
            node, args = super().command()
            return node, ('--import', str(preload), *args)
    return SyntheticClient(), log


def test_real_stdio_mcp_search_and_note_chain_preserves_text(client):
    transport, log = client
    result = transport.call(credentials(), 'search_knowledge', {'knowledge_base_id':'kb1','query':'合成','cursor':''})
    assert result['info_list'][0]['highlight_content'] == '第十条：“保留”；\r\n第二行。'
    note = transport.call(credentials(), 'get_media_info', {'media_id':'doc'})
    assert note['note_content'] == '第十条：“保留”；\r\n第二行。'
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r['url'].rsplit('/', 1)[1] for r in requests] == ['search_knowledge','get_media_info','get_doc_content']
    assert all(r['url'].startswith('https://ima.qq.com/') and r['redirect']=='error' and r['hasSignal'] for r in requests)


@pytest.mark.parametrize(('query','code'), [('error','authentication'),('oversize','response_limit'),('malformed','response_invalid'),('redirect','network')])
def test_real_mcp_errors_sanitized(client, query, code):
    with pytest.raises(ToolError) as error:
        client[0].call(credentials(), 'search_knowledge', {'knowledge_base_id':'kb1','query':query})
    assert error.value.code == 'ima_' + code
    assert 'PRIVATE' not in str(error.value)


def test_real_mcp_rejects_write_and_preserves_subscription_error(client):
    transport, log = client
    with pytest.raises(ToolError) as error:
        transport.call(credentials(), 'import_urls', {'knowledge_base_id':'kb1','urls':['https://example.test']})
    assert error.value.code == 'ima_operation_forbidden'
    assert not log.exists()
    with pytest.raises(ToolError) as error:
        transport.call(credentials(), 'get_media_info', {'media_id':'restricted'})
    assert error.value.code == 'ima_original_unavailable'


def test_real_mcp_discovery_lists_only_read_tools(client):
    async def run():
        node, args = client[0].command()
        draft = MCPServerDraft(display_name='test',transport='stdio',command=node,args=args)
        connection = await _connect_sdk(MCPResolvedServer('test',draft,0))
        try:
            tools, cursor = await connection.list_tools_page(None)
            assert {tool.name for tool in tools} == {'search_knowledge_base','get_knowledge_base','get_knowledge_list','search_knowledge','get_media_info','get_note_content'}
            assert cursor is None
        finally:
            await connection.aclose()
    asyncio.run(run())


def test_builtin_skill_is_packaged():
    from verilex_runtime.knowledge.ima_skill import ima_skill_instruction
    assert 'knowledge_search' in ima_skill_instruction()
    assert 'MCP' in ima_skill_instruction()
    assert (ASSETS/'LICENSE').is_file()
