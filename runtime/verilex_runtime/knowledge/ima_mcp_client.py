"""Call the bundled ima MCP server through the existing supervised MCP transport."""
import asyncio
import json
import shutil
import sys
from pathlib import Path

from verilex_runtime.mcp.client import MCPResolvedServer, _connect_sdk
from verilex_runtime.mcp.importer import MCPServerDraft
from verilex_runtime.tools.registry import ToolError

ASSETS = Path(__file__).with_name('ima_mcp')
MESSAGES = {
    'authentication': '凭据无效或已失效，请重新从 ima 获取并填写。',
    'rate_limit': 'ima 请求过于频繁，请稍后重试。',
    'network': '无法连接 ima，请检查网络后重试。',
    'response_limit': 'ima 返回的资料过大，请在 ima 中查看。',
    'response_invalid': 'ima 返回格式无法识别，请稍后重试。',
    'permission': '当前账号没有这份资料的读取权限，请在 ima 检查授权。',
    'original_unavailable': 'ima 不允许通过外部接口读取订阅知识库中的文件原文，请前往 ima 客户端查看。',
    'request_failed': 'ima 未完成请求，请稍后重试。',
    'operation_forbidden': '此连接只允许读取资料。',
}


def raise_mcp_error(code):
    code = code if code in MESSAGES else 'network'
    raise ToolError('ima_' + code, MESSAGES[code])


class ImaMcpClient:
    def call(self, credentials, name, arguments):
        try:
            return asyncio.run(self._call(credentials, name, arguments))
        except ToolError:
            raise
        except Exception:
            # Never project provider messages, subprocess output or credentials.
            raise_mcp_error('network')

    def command(self):
        if getattr(sys, 'frozen', False):
            node = ASSETS / ('node.exe' if sys.platform == 'win32' else 'node')
            script = ASSETS / 'server.cjs'
        else:
            node = shutil.which('node')
            script = ASSETS / 'server.mjs'
        if not node or not Path(node).is_file() or not script.is_file():
            raise ToolError('ima_component_missing', 'ima 连接组件缺失，请重新安装完整的 VeriLex 应用。')
        return str(node), (str(script),)

    async def _call(self, credentials, name, arguments):
        node, args = self.command()
        draft = MCPServerDraft(display_name='ima', transport='stdio', command=node,
            args=args, cwd=str(ASSETS), connect_timeout_ms=10000, request_timeout_ms=25000,
            env={'IMA_CLIENT_ID': credentials['client_id'], 'IMA_API_KEY': credentials['api_key']},
            secret_env_keys=('IMA_CLIENT_ID', 'IMA_API_KEY'))
        connection = await _connect_sdk(MCPResolvedServer('builtin-ima', draft, 0))
        try:
            result = await asyncio.wait_for(connection.call_tool(name, arguments), timeout=25)
            texts = [block.text for block in result.content if block.type == 'text']
            if len(texts) != 1 or len(texts[0].encode('utf8')) > 8 * 1024 * 1024:
                raise_mcp_error('response_invalid')
            value = json.loads(texts[0])
            if result.isError:
                raise_mcp_error(value.get('error') if isinstance(value, dict) else None)
            if not isinstance(value, dict):
                raise_mcp_error('response_invalid')
            return value
        finally:
            await connection.aclose()


def packaged_smoke():
    """Offline distribution check: MCP initialize/list_tools and bundled skill."""
    from .ima_skill import ima_skill_instruction
    async def check():
        node, args = ImaMcpClient().command()
        draft = MCPServerDraft(display_name='ima smoke', transport='stdio', command=node, args=args)
        connection = await _connect_sdk(MCPResolvedServer('ima-smoke', draft, 0))
        try:
            tools, cursor = await connection.list_tools_page(None)
            names = sorted(t.name for t in tools)
            expected = sorted(['search_knowledge_base', 'get_knowledge_base', 'get_knowledge_list',
                               'search_knowledge', 'get_media_info', 'get_note_content'])
            assert names == expected and cursor is None
            assert '# ima 知识库调用' in ima_skill_instruction()
            print(json.dumps({'ok': True, 'tools': names, 'skill': 'ima-knowledge'}))
        finally:
            await connection.aclose()
    asyncio.run(check())
