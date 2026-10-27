"""Workspace-owned records, exact source checks and deterministic exports.

Independent domain implementation: existing workspace authorization, DOCX and
PDF readers are reused. No external legal-agent implementation is copied.
"""
from __future__ import annotations
import json
import hashlib
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path
from datetime import datetime, timezone
from pydantic import ValidationError
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.tools.registry import ToolContext, ToolError
from verilex_runtime.tools.files import resolve_within_root, _read_document_file, _read_text_file
from .models import CaseInfo, Citation, Fact, FactsProposal, EvidenceProposal, DRAFT_NOTICE, merge_facts, IssuesProposal, StrategyProposal, ElementsProposal, FactSupplement

from .output_paths import output_path, rename_output_folder

OWNER = 'verilex-litigation-v1'
FILES = {'case': '案件信息.json', 'case_suggestions': '事实/case-suggestions.json', 'facts': '事实/facts.json', 'evidence': '证据/evidence.json', 'elements': '焦点与策略/elements.json', 'issues': '焦点与策略/issues.json', 'strategy': '焦点与策略/strategy.json', 'documents': '文书/elements.json'}
FILES.update({f'work_{kind}': f'文书起草/{kind}.json' for kind in ('facts', 'evidence', 'issues', 'strategy', 'documents', 'links')})
FILES['cross_examination'] = '文书起草/编辑记录/cross-examination.json'
MAX_BYTES = 2 * 1024 * 1024
_LOCK = threading.RLock()


def _failure(code='litigation_invalid', message='案件资料格式不正确，请检查后重试。'):
    return ToolError(code, message)


def _pdf_quote_text(text: str) -> str:
    # PDF layout can insert line breaks/spaces within Chinese sentences.
    # Keep digits, punctuation and Latin word boundaries intact; this is
    # layout tolerance, not fuzzy matching or a correction of the source.
    text = re.sub(r'\s+', ' ', text).strip()
    return re.sub(r'(?<=[\u3400-\u9fff])\s+|\s+(?=[\u3400-\u9fff])', '', text)


def safe_path(root: Path, relative: str) -> Path:
    relative = output_path(root, relative)
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or any(p.startswith('.') for p in path.parts):
        raise _failure('litigation_path_invalid', '只能读取本工作区内的普通材料文件。')
    cursor = root
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _failure('litigation_path_invalid', '材料和产物不能使用符号链接。')
    return resolve_within_root(root, relative)


class LitigationService:
    def __init__(self, workspaces: WorkspaceService, law_cache=None, law_reads=None, messages=None, threads=None):
        self.workspaces = workspaces
        self.law_cache = law_cache
        self.law_reads = law_reads
        self.messages = messages
        from .workbench import Workbench
        self.workbench = Workbench(self, threads)
        from .products import Products
        self.products = Products(self)
        from .discussion_editor import EditorDiscussions
        self.editor_discussions = EditorDiscussions(self)

    def _register_internal(self, context, path, data=None):
        if context.workspace_id:
            root=self._root(context)
            self.workspaces.register_internal_file(context.workspace_id, path.relative_to(root).as_posix(),
                hashlib.sha256(data if data is not None else path.read_bytes()).hexdigest())

    def _internal_record(self, context, root, path):
        if not context.workspace_id or path.is_symlink() or not path.is_file(): return False
        digest=self.workspaces.internal_file_digest(context.workspace_id,path.relative_to(root).as_posix())
        if digest is not None:
            return hashlib.sha256(path.read_bytes()).hexdigest()==digest
        # Legacy domain records carry an explicit owner and validated schema.
        kind=next((kind for kind,name in FILES.items() if output_path(root,path.relative_to(root).as_posix())==output_path(root,name)),None)
        if kind is None: return False
        try:
            self.read(context,kind)
        except (ToolError,OSError,ValueError): return False
        return self.workspaces.internal_file_digest(context.workspace_id,path.relative_to(root).as_posix()) is not None

    def context(self, workspace_id: str, *, write=False) -> ToolContext:
        workspace = self.workspaces.get(workspace_id)
        context = ToolContext(Path(workspace.root_path), workspace.allow_read, workspace.allow_write, workspace_id=workspace.id)
        self._root(context, write=write)
        return context

    def _root(self, context: ToolContext, *, write=False) -> Path:
        if context.root_path is None or not context.allow_read or (write and not context.allow_write):
            raise _failure('litigation_workspace_required', '请先选择允许读取和保存产物的工作区。')
        can_write = context.allow_write
        if context.workspace_id:
            record = self.workspaces.get(context.workspace_id)
            if (not record.allow_read or (write and not record.allow_write)
                    or Path(record.root_path).resolve() != context.root_path.resolve()):
                raise _failure('litigation_workspace_required', '工作区授权已变化，请重新选择。')
        root = context.root_path.resolve()
        if can_write and (not context.workspace_id or record.allow_write):
            with _LOCK:
                rename_output_folder(root)
        return root

    def read(self, context: ToolContext, kind: str) -> dict:
        if kind not in FILES:
            raise _failure()
        root = self._root(context)
        path = safe_path(root, FILES[kind])
        if not path.exists():
            return {'revision': 0, 'data': None}
        try:
            if path.stat().st_size > MAX_BYTES:
                raise _failure()
            content = json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(content, dict) or content.get('generated_by') != OWNER
                    or content.get('kind') != kind or type(content.get('revision')) is not int):
                raise _failure('litigation_file_conflict', '同名文件不是本功能生成的产物，已保留，请先调整文件名。')
            self._register_internal(context,path)
            return content
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise _failure('litigation_unreadable', '案件记录无法读取，原文件已保留。') from None

    def _commit(self, context: ToolContext, kind: str, data: dict, expected: int, summary: str) -> dict:
        root = self._root(context, write=True)
        previous = self.read(context, kind)
        if previous['revision'] != expected:
            raise _failure('litigation_revision_conflict', '案件已有更新，请刷新后再保存。')
        record = {'generated_by': OWNER, 'kind': kind, 'revision': expected + 1,
                  'updated_at': datetime.now(timezone.utc).isoformat(), 'notice': DRAFT_NOTICE,
                  'data': data, 'history': [*previous.get('history', []),
                      {'revision': expected + 1, 'summary': summary}]}
        encoded = json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8')
        if len(encoded) > MAX_BYTES:
            raise _failure('litigation_too_large', '案件产物超过单次保存上限。')
        path = safe_path(root, FILES[kind])
        expected_bytes = path.read_bytes() if path.exists() else None
        if expected_bytes is not None:
            try:
                unchanged = json.loads(expected_bytes) == previous
            except (ValueError, UnicodeError):
                unchanged = False
            if not unchanged:
                raise _failure('litigation_file_conflict', '文件已被其他程序修改，已保留，请刷新后重试。')
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.verilex-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            # Recheck permissions and links immediately before replacing our own record.
            self._root(context, write=True)
            safe_path(root, FILES[kind])
            if expected_bytes is None:
                # Exclusive publication never replaces a concurrently created user file.
                os.link(temporary, path)
            else:
                if path.read_bytes() != expected_bytes:
                    raise _failure('litigation_file_conflict', '文件在保存过程中被修改，已保留，请刷新后重试。')
                # Preserve the full previous version before publishing. A competing
                # writer at the destination wins: hard-link creation never overwrites.
                backup = path.parent / ('.verilex-revision-' + uuid.uuid4().hex + '.json')
                os.rename(path, backup)
                if backup.read_bytes() != expected_bytes:
                    try:
                        os.link(backup, path)
                    except FileExistsError:
                        pass
                    raise _failure('litigation_file_conflict', '文件在保存过程中被修改，原记录已保留，请刷新后重试。')
                try:
                    os.link(temporary, path)
                except OSError:
                    try:
                        os.link(backup, path)
                    except FileExistsError:
                        pass
                    raise
        except FileExistsError:
            raise _failure('litigation_file_conflict', '保存位置出现同名文件，原件已保留，请刷新后重试。') from None
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self._register_internal(context,path,encoded)
        return record

    def save_case(self, context: ToolContext, data: dict, expected: int) -> dict:
        with _LOCK:
            try:
                info = CaseInfo.model_validate(data)
            except ValidationError:
                raise _failure() from None
            return self._commit(context, 'case', info.model_dump(), expected, '用户更新案件信息')

    def source_text(self, context: ToolContext, source: Citation) -> str:
        root = self._root(context)
        path = safe_path(root, source.path)
        if self._internal_record(context, root, path):
            raise _failure('litigation_source_invalid', '事实出处必须指向原始材料，不能引用生成结果自身。')
        try:
            if path.suffix.lower() == '.pdf':
                from pypdf import PdfReader
                if path.stat().st_size > 20 * 1024 * 1024:
                    raise _failure('litigation_source_invalid', '材料超过读取上限。')
                reader = PdfReader(path)
                if reader.is_encrypted or source.page is None or source.page > len(reader.pages):
                    raise _failure('litigation_source_invalid', 'PDF 引文需要有效页码。')
                return reader.pages[source.page - 1].extract_text() or ''
            if source.page is not None:
                raise _failure('litigation_source_invalid', '文字和 Word 材料使用文件出处，不虚构页码。')
            return _read_document_file(path) if path.suffix.lower() in {'.docx', '.doc'} else _read_text_file(path)
        except ToolError:
            raise
        except Exception:
            raise _failure('litigation_source_unreadable', '无法读取所引材料，请检查文件和页码。') from None

    def locate_source(self,context,source):
        from .locations import resolve
        return resolve(self,context,source)

    def verify_sources(self, context: ToolContext, sources: list[Citation]) -> None:
        for source in sources:
            original = self.source_text(context, source)
            from .models import SourceLocator
            located=self.locate_source(context,source)
            if located.get('locator'):source.locator=SourceLocator.model_validate(located['locator'])
            quote = source.quote
            if Path(source.path).suffix.lower() == '.pdf':
                original, quote = _pdf_quote_text(original), _pdf_quote_text(quote)
            if not quote or quote not in original:
                location = f'{source.path}' + (f'（PDF第{source.page}页）' if source.page else '')
                raise _failure('litigation_quote_mismatch', f'{location}：引文与原文不一致，未保存。请保留原文文字、数字和标点。')

    def save(self, context: ToolContext, kind: str, data: dict, expected: int, mode: str = 'merge') -> dict:
        with _LOCK:
            self._root(context, write=True)
            if mode not in {'merge', 'replace'} or (mode == 'replace' and kind != 'facts'):
                raise _failure()
            try:
                if kind == 'documents':
                    from .documents import prepare
                    return prepare(self, context, data, expected)
                if kind == 'case_suggestions':
                    from .models import CaseSuggestion
                    proposal = CaseSuggestion.model_validate(data)
                    for field in proposal.fields:
                        self.verify_sources(context, field.sources)
                    output = {**proposal.model_dump(), 'case_revision': self.read(context, 'case')['revision']}
                elif kind == 'facts':
                    proposal = FactsProposal.model_validate(data)
                    previous = self.read(context, kind)
                    for fact in proposal.facts:
                        if fact.status == 'confirmed' or fact.user_note:
                            raise _failure('litigation_review_required', '只有用户核对才能标记已确认。')
                        self.verify_sources(context, fact.sources)
                    old = [Fact.model_validate(f) for f in (previous.get('data') or {}).get('facts', [])]
                    replacing = mode == 'replace'
                    if replacing and (expected != previous['revision'] or
                            (previous.get('data') or {}).get('regeneration_revision') != expected):
                        raise _failure('litigation_regeneration_required', '请先在事实页点击整体重新梳理。')
                    merged, conflicts = merge_facts([] if replacing else old, proposal.facts)
                    output = proposal.model_dump()
                    output['facts'] = [f.model_dump() for f in merged]
                    output['conflicts'] = [*(previous.get('data') or {}).get('conflicts', []), *conflicts]
                    output['gaps'] = list(dict.fromkeys([*(previous.get('data') or {}).get('gaps', []), *proposal.gaps]))
                elif kind == 'elements':
                    proposal = ElementsProposal.model_validate(data)
                    output = {**proposal.model_dump(), 'confirmed': False,
                              'case_revision': self.read(context, 'case')['revision']}
                elif kind == 'evidence':
                    proposal = EvidenceProposal.model_validate(data)
                    for item in proposal.evidence:
                        self.verify_sources(context, item.sources)
                    output = proposal.model_dump()
                elif kind == 'issues':
                    proposal = IssuesProposal.model_validate(data)
                    outline = self.require_elements(context)
                    shape = [{'title': i.title, 'elements': [e.name for e in i.elements]} for i in proposal.issues]
                    if shape != outline['data']['issues']:
                        raise _failure('litigation_elements_mismatch', '焦点和要件与已确认清单不一致，请先核对清单。')
                    if self.law_cache is None or self.law_reads is None:
                        raise _failure('litigation_law_unavailable', '法条核验服务不可用，未保存焦点。')
                    for issue in proposal.issues:
                        for element in issue.elements:
                            self.verify_sources(context, [*element.supporting, *element.opposing])
                            if not element.supporting:
                                element.strength = 'missing'
                                if not element.gap:
                                    element.gap = '缺少支持该要件的材料，请补证。'
                        for law in issue.laws:
                            self.verify_law(context, law)
                    output = proposal.model_dump()
                    output['law_verification'] = 'verified_against_thread_read'
                    output['elements_revision'] = outline['revision']
                elif kind == 'strategy':
                    proposal = StrategyProposal.model_validate(data)
                    issues = self.read(context, 'issues')
                    if not issues.get('data'):
                        raise _failure('litigation_issues_required', '请先整理争议焦点和要件，再生成策略备忘。')
                    output = proposal.model_dump()
                    self.check_dependencies(context, issues['data'])
                    self.validate_issues(context, issues['data'])
                    output['issues_revision'] = issues['revision']
                else:
                    raise _failure()
            except ValidationError:
                raise _failure() from None
            if kind in {'issues', 'strategy'}:
                output['dependencies'] = {k:self.read(context, k)['revision'] for k in ('case','facts','evidence')}
            return self._commit(context, kind, output, expected, proposal.summary)

    def confirm_document(self, context: ToolContext, data: dict, expected: int) -> dict:
        from .documents import confirm
        with _LOCK:
            self._root(context, write=True)
            return confirm(self, context, data, expected)

    def request_regeneration(self, context: ToolContext, expected: int) -> dict:
        with _LOCK:
            previous = self.read(context, 'facts')
            if not previous.get('data'):
                raise _failure('litigation_output_missing', '请先梳理事实。')
            data = {**previous['data'], 'regeneration_revision': expected + 1}
            return self._commit(context, 'facts', data, expected, '用户要求整体重新梳理，旧版保留')

    def fact_versions(self, context: ToolContext) -> list[dict]:
        """Read only our existing full-version backups, never arbitrary paths."""
        with _LOCK:
            current = self.read(context, 'facts')
            if not current.get('data'):
                return []
            versions = {current['revision']: current}
            parent = safe_path(self._root(context), FILES['facts']).parent
            for path in parent.glob('.verilex-revision-*.json'):
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
                    continue
                try:
                    record = json.loads(path.read_text(encoding='utf-8'))
                    if (record.get('generated_by') == OWNER and record.get('kind') == 'facts'
                            and type(record.get('revision')) is int and record['revision'] < current['revision']
                            and isinstance(record.get('data'), dict)):
                        versions.setdefault(record['revision'], record)
                except (OSError, ValueError, AttributeError):
                    continue
            return [versions[key] for key in sorted(versions, reverse=True)]

    def restore_facts(self, context: ToolContext, revision: int, expected: int) -> dict:
        with _LOCK:
            version = next((v for v in self.fact_versions(context) if v['revision'] == revision), None)
            if version is None:
                raise _failure('litigation_version_missing', '找不到该事实版本，请刷新后重试。')
            data = {k: v for k, v in version['data'].items() if k != 'regeneration_revision'}
            # Restoring preserves the original review marks, but never vouches
            # for quotations whose material has since changed.
            try:
                for fact in data['facts']:
                    self.verify_sources(context, Fact.model_validate(fact).sources)
            except (ValidationError, KeyError, TypeError):
                raise _failure() from None
            return self._commit(context, 'facts', data, expected, f'用户恢复事实第 {revision} 版')

    def supplement_fact(self, context: ToolContext, data: dict, expected: int) -> dict:
        """Attach verbatim current user testimony; never grant human review."""
        from verilex_runtime.agent.events import user_message_parts
        from verilex_runtime.storage.models import MessageRole
        with _LOCK:
            try:
                supplement = FactSupplement.model_validate(data)
            except ValidationError:
                raise _failure() from None
            if not self.messages or not context.thread_id or not context.turn_id:
                raise _failure('litigation_user_statement_required', '请在本案件对话中补充事实。')
            messages = [m for m in self.messages.list(context.thread_id)
                        if m.thread_id == context.thread_id and m.turn_id == context.turn_id and m.role == MessageRole.USER]
            texts = [user_message_parts(json.loads(m.content_json))[0] for m in messages]
            if not any(supplement.user_quote in text for text in texts):
                raise _failure('litigation_user_statement_required', '补答必须逐字来自用户本轮陈述，不能引用材料或助手回复。')
            previous = self.read(context, 'facts')
            output = previous.get('data')
            if not output:
                raise _failure('litigation_fact_missing', '找不到要补充的事实。')
            for item in output['facts']:
                if item['id'] != supplement.fact_id:
                    continue
                # Existing human-reviewed text/date cannot be silently replaced.
                if supplement.date and item['date'] and item['date'] != supplement.date:
                    raise _failure('litigation_review_required', '补答日期与已有事实不同，请在核对或修改中处理冲突。')
                self.verify_sources(context, Fact.model_validate(item).sources)
                if supplement.date:
                    item['date'] = supplement.date
                    item['pending'] = [p for p in item['pending'] if p != '日期待确认']
                item['user_note'] = '\n'.join(filter(None, [item.get('user_note', ''), '用户口述：' + supplement.user_quote]))
                if item['text'] in supplement.user_quote:
                    item['pending'] = [p for p in item['pending'] if p != '事实来源待确认']
                # The newly supplemented record has not passed the human review
                # endpoint, even when its prior version had been reviewed.
                item['status'] = 'pending' if item['pending'] else 'supported'
                try:
                    Fact.model_validate(item)
                except ValidationError:
                    raise _failure('litigation_too_large', '事实补充说明过长，请在核对或修改中整理后保存。') from None
                output.pop('regeneration_revision', None)
                return self._commit(context, 'facts', output, expected, '对话补充事实 ' + supplement.fact_id)
            raise _failure('litigation_fact_missing', '找不到要补充的事实。')

    def confirm_elements(self, context: ToolContext, data: dict, expected: int) -> dict:
        with _LOCK:
            previous = self.read(context, 'elements')
            if not previous.get('data') or previous['data'].get('case_revision') != self.read(context, 'case')['revision']:
                raise _failure('litigation_output_stale', '案件信息已更新，请重新整理要件清单。')
            try:
                proposal = ElementsProposal.model_validate(data)
            except ValidationError:
                raise _failure() from None
            output = {**proposal.model_dump(), 'confirmed': True, 'case_revision': previous['data']['case_revision']}
            return self._commit(context, 'elements', output, expected, '用户核对要件清单')

    def require_elements(self, context: ToolContext) -> dict:
        outline = self.read(context, 'elements')
        data = outline.get('data') or {}
        if data.get('confirmed') is not True or data.get('case_revision') != self.read(context, 'case')['revision']:
            raise _failure('litigation_elements_review_required', '请先在工作台核对并确认要件清单。')
        return outline

    def validate_issues(self, context: ToolContext, data: dict) -> None:
        outline = self.require_elements(context)
        if data.get('elements_revision') != outline['revision']:
            raise _failure('litigation_output_stale', '要件清单已更新，请重新生成焦点与策略。')
        clean = {key: data[key] for key in ('issues', 'summary')}
        for issue in IssuesProposal.model_validate(clean).issues:
            for law in issue.laws:
                self.verify_law(context, law)
            for element in issue.elements:
                self.verify_sources(context, [*element.supporting, *element.opposing])

    def review_fact(self, context: ToolContext, fact_id: str, data: dict, expected: int) -> dict:
        with _LOCK:
            previous = self.read(context, 'facts')
            output = previous.get('data')
            if not output:
                raise _failure('litigation_fact_missing', '找不到要核对的事实。')
            try:
                updated = Fact.model_validate({**data, 'id': fact_id, 'status': 'confirmed'})
            except ValidationError:
                raise _failure() from None
            self.verify_sources(context, updated.sources)
            if not updated.user_note.strip():
                raise _failure('litigation_review_required', '请填写核对说明，以记录用户确认的依据。')
            for index, item in enumerate(output['facts']):
                if item['id'] == fact_id:
                    output['facts'][index] = updated.model_dump()
                    output.pop('regeneration_revision', None)
                    return self._commit(context, 'facts', output, expected, '用户核对事实 ' + fact_id)
            raise _failure('litigation_fact_missing', '找不到要核对的事实。')

    def materials(self, context: ToolContext) -> dict:
        root = self._root(context)
        result, visited = [], 0
        for parent, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != 'node_modules' and not (Path(parent)/d).is_symlink()]
            for name in sorted(files):
                visited += 1
                if visited > 2000:
                    return {'materials': result, 'truncated': True}
                path = Path(parent)/name
                if name.startswith('.') or path.is_symlink() or self._internal_record(context, root, path):
                    continue
                suffix = path.suffix.lower()
                from verilex_runtime.tools.files import _locate_textutil
                supported = suffix in {'.txt', '.md', '.csv', '.json', '.docx', '.pdf'} or (suffix == '.doc' and _locate_textutil() is not None)
                from .evidence_conversion import SUPPORTED
                if suffix not in SUPPORTED:
                    continue
                result.append({'path': str(path.relative_to(root)), 'name': name, 'supported': supported, 'book_supported': suffix in SUPPORTED,
                               'reason': '' if supported else ('请先另存为 .docx' if suffix == '.doc' else '此材料类型的结构化读取准备中')})
        return {'materials': result, 'truncated': False}

    def search_files(self,context,query):
        if not isinstance(query,str) or not query.strip() or len(query)>200:raise _failure()
        root=self._root(context);matches=[];visited=0
        for parent,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if not d.startswith('.') and d != 'node_modules' and not (Path(parent)/d).is_symlink()]
            for name in sorted(files):
                path=Path(parent)/name;visited+=1
                if name.startswith('.') or path.is_symlink() or self._internal_record(context,root,path):continue
                relative=str(path.relative_to(root))
                if query.casefold() in relative.casefold():matches.append({'path':relative,'name':name,'directory':False})
                if len(matches)>=100 or visited>=5000:return {'entries':matches,'truncated':True}
        return {'entries':matches,'truncated':False}

    def files(self, context: ToolContext, relative: str = '') -> dict:
        """List one authorized directory for the workbench, including outputs."""
        root = self._root(context)
        folder = safe_path(root, relative)
        if not folder.is_dir():
            raise _failure('litigation_folder_missing', '文件夹已移动或不存在，请刷新。')
        entries = []
        truncated = False
        try:
            with os.scandir(folder) as children:
                for child in children:
                    if child.name.startswith('.') or child.name == 'node_modules' or child.is_symlink():
                        continue
                    directory = child.is_dir(follow_symlinks=False)
                    if not directory and not child.is_file(follow_symlinks=False):
                        continue
                    if not directory and self._internal_record(context, root, Path(child.path)):
                        continue
                    if len(entries) >= 1000:
                        truncated = True
                        break
                    entries.append({'path': str(Path(child.path).relative_to(root)),
                                    'name': child.name, 'directory': directory})
        except OSError:
            raise _failure('litigation_folder_unreadable', '无法读取文件夹，请检查文件夹后重试。') from None
        entries.sort(key=lambda entry: (not entry['directory'], entry['name'].casefold()))
        return {'entries': entries, 'truncated': truncated}

    def snapshot(self, context: ToolContext) -> dict:
        with _LOCK:
            return {kind: self.read(context, kind) for kind in FILES if not kind.startswith('work_')}


    def check_dependencies(self, context: ToolContext, data: dict) -> None:
        dependencies = data.get('dependencies')
        if not isinstance(dependencies, dict) or any(self.read(context, kind)['revision'] != dependencies.get(kind) for kind in ('case','facts','evidence')):
            raise _failure('litigation_output_stale', '案件信息、事实或证据已更新，请重新生成焦点与策略。')

    def verify_law(self, context: ToolContext, law) -> None:
        from verilex_runtime.legal.parser import locate_clause
        from verilex_runtime.storage.models import ToolCallStatus
        if not context.thread_id or self.law_cache is None or self.law_reads is None:
            raise _failure('litigation_law_unverified', '请在本案件对话中重新读取并核验法条。')
        cached = self.law_cache.lookup(law.bbbs)
        located = locate_clause(cached.body_text.split('\n'), law.clause) if cached else None
        if not located or law.quote not in '\n'.join(located.clause):
            raise _failure('litigation_law_unverified', '法条引文未通过原文核验，请重新读取该条。')
        for record in self.law_reads.list_by_thread(context.thread_id):
            if record.name != 'law_read' or record.status != ToolCallStatus.SUCCEEDED:
                continue
            try:
                wrapper = json.loads(record.result_json or '{}')
                payload = json.loads(wrapper.get('output', '{}'))
                args = json.loads(record.arguments_json)
                if wrapper.get('ok') is not True or args.get('bbbs') != law.bbbs or payload.get('bbbs') != law.bbbs:
                    continue
                parts = payload.get('clause_text')
                if payload.get('clause') == law.clause and isinstance(parts,list):
                    read_text = '\n'.join(parts)
                else:
                    match = locate_clause(payload.get('body_text','').split('\n'), law.clause)
                    read_text = '\n'.join(match.clause) if match else ''
                if law.quote in read_text:
                    return
            except (ValueError, TypeError, AttributeError):
                continue
        raise _failure('litigation_law_unverified', '当前案件对话尚未成功读取该法条原文，请先检索核验。')

    def arrange_evidence(self, context: ToolContext, order: list[dict], expected: int) -> dict:
        with _LOCK:
            record = self.read(context, 'evidence')
            data = record.get('data')
            if not data:
                raise _failure('litigation_output_missing', '请先整理证据。')
            evidence = {item['id']: item for item in data['evidence']}
            if len(order) != len(evidence) or {item['id'] for item in order} != set(evidence):
                raise _failure('litigation_invalid', '排序必须保留全部证据且不能重复。')
            data['evidence'] = [{**evidence[item['id']], 'group':item['group']} for item in order]
            return self._commit(context, 'evidence', data, expected, '用户调整证据分组与顺序')
