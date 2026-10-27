"""Selected-library scope and result projection over the bundled ima MCP server."""
import hashlib
import json
import threading
from time import monotonic
from uuid import uuid4
from .ima_mcp_client import ImaMcpClient
from verilex_runtime.tools.registry import ToolError

WIKI = '/openapi/wiki/v1/'
NOTE = '/openapi/note/v1/get_doc_content'
ALLOWED = frozenset(WIKI + name for name in (
    'search_knowledge_base', 'get_knowledge_base', 'get_knowledge_list',
    'search_knowledge', 'get_media_info')) | {NOTE}


def fail(code, message):
    raise ToolError('ima_' + code, message)


class ImaService:
    REF = 'knowledge.ima.connection.v1'

    def __init__(self, secrets, transport=None):
        self.secrets = secrets
        self.transport = transport or ImaMcpClient()
        self.lock = threading.RLock()
        self.pending = None
        self.candidates = {}
        self.pages = {}
        self.folders = {}

    def _call(self, credentials, path, payload):
        if path not in ALLOWED:
            fail('operation_forbidden', '此连接只允许读取资料。')
        name = 'get_note_content' if path == NOTE else path.removeprefix(WIKI)
        return self.transport.call(credentials, name, payload)

    def _connection(self):
        raw = self.secrets.get(self.REF)
        if not raw:
            fail('disconnected', '请先接入 ima。')
        return json.loads(raw)

    @staticmethod
    def _items(data, field):
        items = data.get(field)
        if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
            fail('response_invalid', 'ima 返回的列表格式无法识别，请稍后重试。')
        return items

    @staticmethod
    def _next(data, cursor):
        if data.get('is_end') is True:
            return None
        nxt = data.get('next_cursor')
        if data.get('is_end') is not False or not isinstance(nxt, str) or not nxt or nxt == cursor:
            fail('pagination', 'ima 未返回有效的下一页，请重新搜索。')
        return nxt

    def discover(self, connection, query='', cursor=''):
        data = self._call(connection, WIKI + 'search_knowledge_base',
                          {'query': query, 'cursor': cursor, 'limit': 20})
        items = self._items(data, 'info_list')
        normalized = []
        for item in items:
            fields = {}
            # Public documentation uses id/name; the live search endpoint also
            # uses kb_id/kb_name. Normalize only these known aliases, never guess
            # an identifier or silently accept conflicting account data.
            for key, alias in (('id', 'kb_id'), ('name', 'kb_name')):
                values = [item[k] for k in (key, alias) if k in item]
                if (not values or any(not isinstance(v, str) or not v.strip() for v in values)
                        or any(v != values[0] for v in values[1:])):
                    fail('response_invalid', 'ima 返回的知识库编号或名称缺失、格式异常或相互冲突，请重试。')
                fields[key] = values[0]
            normalized.append({'id': fields['id'], 'name': fields['name'][:240]})
        return {'items': normalized,
                'next_cursor': self._next(data, cursor)}

    def probe(self, client_id, api_key):
        # Keep a single short-lived draft; invalid attempts preserve the saved account.
        for value in (client_id, api_key):
            if not value or len(value) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
                fail('credentials_invalid', '请填写官网提供的 Client ID 和 API Key，不要包含空格或换行。')
        with self.lock:
            credentials = {'client_id': client_id, 'api_key': api_key}
            result = self.discover(credentials)
            token = uuid4().hex
            self.pending = {'token': token, 'expires': monotonic() + 600,
                            'credentials': credentials, 'choices': {x['id']: x['name'] for x in result['items']}}
            return {**result, 'token': token}

    def resume(self):
        with self.lock:
            connection = self._connection()
            result = self.probe(connection["client_id"], connection["api_key"])
            self.pending["choices"].update({x["remote_id"]: x["name"] for x in connection["libraries"]})
            known = {x["id"] for x in result["items"]}
            result["items"] += [{"id": x["remote_id"], "name": x["name"]} for x in connection["libraries"] if x["remote_id"] not in known]
            result["selected"] = [x["remote_id"] for x in connection["libraries"]]
            return result

    def _draft(self, token):
        if not self.pending or self.pending['expires'] < monotonic() or self.pending['token'] != token:
            self.pending = None
            fail('draft_expired', '连接验证已过期，请重新填写凭据并连接。')
        return self.pending

    def choices(self, token, query='', cursor=''):
        with self.lock:
            draft = self._draft(token)
            result = self.discover(draft['credentials'], query, cursor)
            if len(draft['choices']) + len(result['items']) > 1000:
                fail('limit', '本次浏览的知识库过多，请重新连接并搜索。')
            draft['choices'].update({x['id']: x['name'] for x in result['items']})
            return result

    def save(self, token, ids):
        with self.lock:
            draft = self._draft(token)
            if not ids or len(ids) > 50 or len(set(ids)) != len(ids) or any(x not in draft['choices'] for x in ids):
                fail('selection_invalid', '请选择列表中的知识库（最多 50 个）。')
            deadline = monotonic() + 40
            for identifier in ids:
                if monotonic() > deadline:
                    fail("validation_timeout", "本次选库检查耗时较长，请减少所选知识库后重试。")
                # Verify read access, not merely presence in public search results.
                self._call(draft['credentials'], WIKI + 'get_knowledge_list',
                           {'knowledge_base_id': identifier, 'cursor': '', 'limit': 1})
            generation = uuid4().hex
            old_raw = self.secrets.get(self.REF)
            if old_raw:
                old = json.loads(old_raw)
                if all(old.get(k) == draft["credentials"][k] for k in ("client_id", "api_key")) and old["libraries"]:
                    generation = old["libraries"][0]["id"].split(":")[1]
            connection = {**draft['credentials'], 'libraries': [
                {'id': 'ima:' + generation + ':' + hashlib.sha256(x.encode()).hexdigest()[:24],
                 'remote_id': x, 'name': draft['choices'][x]} for x in ids]}
            self.secrets.put(self.REF, json.dumps(connection, ensure_ascii=False))
            self.pending = None
            self.candidates.clear(); self.pages.clear(); self.folders.clear()
            return {'ok': True}

    def status(self):
        with self.lock:
            return {'configured': bool(self.secrets.get(self.REF))}

    def disconnect(self):
        with self.lock:
            self.secrets.delete(self.REF)
            self.pending = None
            self.candidates.clear(); self.pages.clear(); self.folders.clear()

    def libraries(self):
        with self.lock:
            if not self.secrets.get(self.REF):
                return []
            return [{ 'id': x['id'], 'name': x['name'], 'kind': 'ima', 'root': 'ima',
                      'status': 'ready', 'error': None, 'excluded': [], 'directories': [],
                      'count': 0, 'synced_at': None} for x in self._connection()['libraries']]

    def library(self, identifier):
        for x in self._connection()['libraries']:
            if x['id'] == identifier:
                return x
        fail('scope', '这个 ima 知识库已解除关联，请重新选择。')

    def detach(self, identifier):
        with self.lock:
            connection = self._connection()
            self.library(identifier)
            connection['libraries'] = [x for x in connection['libraries'] if x['id'] != identifier]
            self.secrets.put(self.REF, json.dumps(connection, ensure_ascii=False))
            self.candidates.clear(); self.pages.clear(); self.folders.clear()

    def documents(self, identifier, query='', offset=0, limit=100, folder_id=''):
        with self.lock:
            library = self.library(identifier)
            if folder_id:
                known = self.folders.get((identifier, folder_id))
                if not known or known < monotonic():
                    fail('folder_expired', '文件夹列表已更新，请返回知识库根目录后重试。')
            key = (identifier, query, limit, folder_id)
            if offset == 0:
                cursor = ''
            else:
                cached = self.pages.get((*key, offset))
                if not cached or cached[1] < monotonic():
                    fail('page_expired', '列表已更新，请重新搜索后翻页。')
                cursor = cached[0]
            path = WIKI + ('search_knowledge' if query else 'get_knowledge_list')
            payload = {'knowledge_base_id': library['remote_id'], 'cursor': cursor}
            if query:
                payload['query'] = query
            else:
                payload['limit'] = min(limit, 50)
                if folder_id:
                    payload['folder_id'] = folder_id
            data = self._call(self._connection(), path, payload)
            values = self._items(data, 'info_list' if query else 'knowledge_list')
            nxt = self._next(data, cursor)
            if len(self.pages) > 1000:
                self.pages.clear()
            if len(self.candidates) > 4000:
                self.candidates.clear()
            if len(self.folders) > 4000:
                self.folders.clear()
            items = []
            for value in values:
                media = value.get('media_id')
                if not isinstance(media, str) or not media:
                    continue
                title = str(value.get('title') or '未命名资料')[:500]
                version = hashlib.sha256((media + title).encode()).hexdigest()
                if value.get('media_type') == 99:
                    self.folders[(identifier, media)] = monotonic() + 600
                    items.append({'id': media, 'path': title, 'version': version, 'status': 'ready',
                                  'error': None, 'kind': 'folder', 'snippet': '文件夹 · 点击进入'})
                    continue
                self.candidates[(identifier, media)] = (title, version, query, cursor, monotonic() + 600, folder_id)
                excerpt = value.get('highlight_content')
                excerpt = excerpt if isinstance(excerpt, str) else ''
                items.append({'id': media, 'path': title, 'version': version, 'status': 'ready',
                              'error': None, 'snippet': excerpt[:400] or 'ima 资料 · 点击查看',
                              'source_kind': 'ima', 'library_name': library['name'],
                              'content_kind': 'search_excerpt' if excerpt else 'metadata_only',
                              'excerpt': excerpt[:4000], 'excerpt_truncated': len(excerpt) > 4000})
            next_offset = offset + max(1, len(items)) if nxt is not None else None
            if next_offset is not None:
                self.pages[(*key, next_offset)] = (nxt, monotonic() + 600)
            return {'items': items, 'has_more': nxt is not None, 'next_offset': next_offset}

    def read(self, identifier, document_id, version, start=0, max_chars=20000):
        with self.lock:
            library = self.library(identifier)
            candidate = self.candidates.get((identifier, document_id))
            if not candidate or candidate[1] != version or candidate[4] < monotonic():
                fail('candidate_expired', '资料列表已更新，请重新搜索再打开。')
            title, _, query, cursor, _, folder_id = candidate
            # The caller cannot supply an arbitrary media ID; recheck its library membership.
            payload = {'knowledge_base_id': library['remote_id'], 'cursor': cursor}
            payload.update({'query': query} if query else {'limit': 50})
            if folder_id and not query:
                payload['folder_id'] = folder_id
            data = self._call(self._connection(), WIKI + ('search_knowledge' if query else 'get_knowledge_list'), payload)
            if not any(x.get('media_id') == document_id and x.get('media_type') != 99 for x in self._items(data, 'info_list' if query else 'knowledge_list')):
                fail('source_changed', '资料已移出当前知识库，请重新搜索。')
            media = self._call(self._connection(), WIKI + 'get_media_info', {'media_id': document_id})
            text = ''
            notice = '此资料由 ima 提供访问链接，请在 ima 查看原文；当前不能生成精确引用卡。'
            if media.get('media_type') == 11 or 'note_content' in media:
                note_id = (media.get('notebook_ext_info') or {}).get('notebook_id')
                if type(note_id) not in (str, int) or not str(note_id):
                    fail('unavailable', 'ima 未提供笔记正文，请在 ima 查看。')
                # Upstream get_media_info already fetches note content through MCP.
                if media.get('note_content_error'):
                    fail('unavailable', 'ima 未提供笔记正文，请在 ima 查看。')
                text = media.get('note_content')
                if not isinstance(text, str):
                    fail('response_invalid', 'ima 未返回有效的笔记正文。')
                notice = '以下为 ima 接口返回的笔记文本，尚未核对原始格式，不能作为逐字核验的法律引文。'
            if start > len(text):
                fail('range', '正文位置已变化，请重新打开资料。')
            end = min(start + max_chars, len(text))
            return {'id': document_id, 'path': title, 'version': version, 'text': text[start:end],
                    'start': start, 'end': end, 'has_more': end < len(text), 'total_chars': len(text),
                    'external': True, 'notice': notice, 'open_url': 'https://ima.qq.com/',
                    'receipt_id': None, 'quote_arguments': None}
