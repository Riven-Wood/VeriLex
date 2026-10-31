"""User-owned rich documents: one authoritative DOCX, persistent drafts and history.

New editor metadata lives inside the DOCX package, so a successful publication
cannot leave the visible document and editor body at different revisions.
Legacy work_* records are read separately and never rewritten by this store.
"""
from __future__ import annotations
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from datetime import datetime, timezone
from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.tools.registry import ToolError
from .service import _LOCK, _failure, safe_path
from .editor import fingerprint, render_docx, validate_document, inspect_docx, document_text
from verilex_runtime.documents.docx_package import parts, pack, checked_bytes

FOLDER='文书起草'
META='verilex/editor.json'
OWNER='verilex-rich-document-v1'
KINDS={'facts','evidence','issues','strategy','documents'}
MAX_BYTES=20*1024*1024


class Products:
    def __init__(self, service): self.service=service

    def _id(self, value):
        if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value): raise _failure()
        return value

    def _file(self, context, product_id):
        self._recover_publications(context)
        root=self.service._root(context)
        # Resolve the stable id from owned document metadata, not a supplied path.
        index=safe_path(root,f'{FOLDER}/编辑记录/{self._id(product_id)}-index.json')
        if index.exists():
            try:
                value=json.loads(index.read_text(encoding='utf8'))
                if value['record']['id']!=product_id or value['record'].get('owner')!=OWNER or value['record'].get('schema_version')!=1:raise ValueError()
                relative=Path(value['path'])
                if len(relative.parts)!=2 or relative.parts[0] not in {FOLDER,'工作成果'} or relative.suffix.lower()!='.docx':raise ValueError()
                target=safe_path(root,value['path'])
                if target.is_file():
                    metadata=parts(checked_bytes(target)).get(META)
                    if metadata and json.loads(metadata).get('id')!=product_id:raise ValueError()
                    self.service._register_internal(context,index)
                    return target
            except (ValueError,KeyError,TypeError):raise _failure('litigation_unreadable','文书索引损坏，原文件已保留。') from None
        matches=[];self._id(product_id)
        folder=safe_path(root,FOLDER)
        if folder.exists():
            for path in folder.glob('*.docx'):
                if path.name.startswith('.'):continue
                source=safe_path(root,str(path.relative_to(root)))
                try:
                    data=checked_bytes(source);metadata=parts(data).get(META)
                    if metadata and json.loads(metadata).get('id')==product_id:matches.append(source)
                except (ValueError,OSError,AttachmentDomainError):continue
        if len(matches)!=1: raise _failure('litigation_product_missing','文书已移动或不存在，请刷新。')
        return matches[0]

    def _read(self,path):
        if path.stat().st_size>MAX_BYTES: raise _failure('litigation_too_large','文书超过读取上限。')
        data=checked_bytes(path);items=parts(data)
        try:
            if META in items:
                record=json.loads(items[META])
            else:
                candidates=[]
                index_folder=path.parent/'编辑记录'
                if index_folder.is_symlink():raise ValueError()
                for index in index_folder.glob('*-index.json'):
                    if index.is_symlink():continue
                    try:value=json.loads(index.read_text(encoding='utf8'))
                    except (ValueError,OSError):continue
                    if value.get('path') in {f'{FOLDER}/{path.name}',f'工作成果/{path.name}'}:candidates.append(value['record'])
                if len(candidates)!=1:raise ValueError()
                record=candidates[0]
            if record['owner']!=OWNER or record['schema_version']!=1 or record['kind'] not in KINDS: raise ValueError()
            self._id(record['id']);validate_document(record['document'])
            if type(record['revision']) is not int or record['revision']<1: raise ValueError()
        except (KeyError,ValueError,TypeError): raise _failure('litigation_unreadable','文书编辑记录损坏，原文件已保留。') from None
        # An external application may preserve the custom part while changing Word.
        # Hash only Word parts, ignoring zip timestamps and our own custom part.
        actual=self._parts_hash(items)
        external=META not in items or actual!=record['word_hash']
        return data,{**record,'file_hash':fingerprint(data),'external_update':external}

    @staticmethod
    def _parts_hash(items):
        return fingerprint(b''.join(name.encode()+b'\0'+items[name] for name in sorted(items) if name!=META))

    def _encode(self, record, source=None):
        items=parts(source if source is not None else render_docx(record['document'],compact=record['kind']=='documents',pleading=bool(record.get('draft_format_version'))))
        record={**record,'word_hash':self._parts_hash(items)}
        items[META]=json.dumps(record,ensure_ascii=False).encode()
        # Register the custom JSON part in OPC content types.
        from verilex_runtime.documents.docx_package import xml, serialize, CT, REL
        from lxml import etree
        ct=xml(items['[Content_Types].xml'])
        for node in list(ct):
            if node.get('PartName')=='/'+META:ct.remove(node)
        override=etree.SubElement(ct,f'{{{CT}}}Override');override.set('PartName','/'+META);override.set('ContentType','application/json')
        items['[Content_Types].xml']=serialize(ct)
        rels=xml(items['_rels/.rels'])
        for node in list(rels):
            if node.get('Id')=='verilexEditor':rels.remove(node)
        relationship=etree.SubElement(rels,f'{{{REL}}}Relationship')
        relationship.set('Id','verilexEditor');relationship.set('Type','https://verilex.local/relationships/editor')
        relationship.set('Target',META)
        items['_rels/.rels']=serialize(rels)
        record['word_hash']=self._parts_hash(items)
        items[META]=json.dumps(record,ensure_ascii=False).encode()
        return pack(items)

    def _recover_publications(self, context):
        """Restore only journalled interruptions, never a user-deleted document."""
        root = self.service._root(context)
        folder = safe_path(root, FOLDER)
        if not folder.exists():
            return
        for journal in safe_path(root,f'{FOLDER}/编辑记录').glob('rename-pending-*.json'):
            self._finish_rename(context,journal)
        for journal in folder.glob('.verilex-edit-pending-*.json'):
            self.service._root(context, write=True)
            try:
                if journal.is_symlink() or journal.stat().st_size > 2048:
                    raise ValueError()
                entry = json.loads(journal.read_text(encoding='utf8'))
                target_name, backup_name = entry['target'], entry['backup']
                if (not isinstance(target_name, str) or Path(target_name).name != target_name
                        or target_name.startswith('.') or not target_name.endswith('.docx')
                        or not isinstance(backup_name, str)
                        or not re.fullmatch(r'\.verilex-edit-history-[a-f0-9]{32}\.docx', backup_name)):
                    raise ValueError()
                target = safe_path(root, f'{FOLDER}/{target_name}')
                backup = folder / backup_name
                if backup.is_symlink():
                    raise ValueError()
                if not target.exists():
                    # link is no-clobber: an external save always wins.
                    try:
                        os.link(backup, target)
                    except FileExistsError:
                        pass
                journal.unlink()
            except (ValueError, KeyError, TypeError, OSError):
                raise _failure('litigation_save_failed', '上次保存尚未恢复，原文和草稿已保留，请检查文件夹后重试。') from None

    def _publish(self,context,path,data,expected=None,*,history=True,internal=False):
        root=self.service._root(context,write=True)
        relative=str(path.relative_to(root));safe_path(root,relative)
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(prefix='.verilex-edit-',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            self.service._root(context,write=True);safe_path(root,relative)
            if expected is None:
                os.link(temp,path)
            else:
                if not path.exists() or path.read_bytes()!=expected:
                    raise _failure('litigation_file_conflict','文件已有外部更新，已保留你的草稿，请重新读取。')
                if not history:
                    os.replace(temp,path)
                    if internal:self.service._register_internal(context,path,data)
                    return
                # Preserve displaced bytes even if an external writer races us.
                backup=path.parent/('.verilex-edit-history-'+uuid.uuid4().hex+path.suffix)
                journal = None
                if path.suffix == '.docx':
                    journal = path.parent / ('.verilex-edit-pending-'+uuid.uuid4().hex+'.json')
                    journal_fd, journal_temp = tempfile.mkstemp(prefix='.verilex-edit-journal-', dir=path.parent)
                    try:
                        with os.fdopen(journal_fd, 'w', encoding='utf8') as stream:
                            json.dump({'target': path.name, 'backup': backup.name}, stream)
                            stream.flush(); os.fsync(stream.fileno())
                        # Recovery must never observe an empty/partial journal.
                        os.link(journal_temp, journal)
                    finally:
                        os.unlink(journal_temp)
                os.rename(path,backup)
                try:
                    if backup.read_bytes()!=expected:
                        raise _failure('litigation_file_conflict','文件在保存时更新，恢复副本已保留。')
                    os.link(temp,path)
                except BaseException:
                    try: os.link(backup,path)
                    except FileExistsError: pass
                    raise
                finally:
                    if journal is not None and path.exists():
                        journal.unlink()
        except FileExistsError: raise _failure('litigation_file_conflict','同名文件已存在，未覆盖。') from None
        except OSError: raise _failure('litigation_save_failed','保存失败，原文件已保留，请检查文件夹后重试。') from None
        finally:
            if os.path.exists(temp):os.unlink(temp)
        if internal:self.service._register_internal(context,path,data)

    def from_work(self,context,kind,expected):
        with _LOCK:
            record=self.service.workbench.record(context,kind)
            if record['revision']!=expected or not record.get('data'):raise _failure('litigation_file_conflict','成果已有更新，请刷新后编辑。')
            value=record['data'];content=[{'type':'heading','attrs':{'level':1},'content':[{'type':'text','text':value['title']}]}]
            for section in value['sections']:
                content.append({'type':'heading','attrs':{'level':2},'content':[{'type':'text','text':section['title']}]})
                for line in section['body'].splitlines():
                    content.append({'type':'paragraph','content':[{'type':'text','text':line}]} if line else {'type':'paragraph'})
            product=self.create(context,value['title'],kind,{'type':'doc','content':content})
            # Internal source/pending notes remain outside the delivered Word body.
            path=safe_path(self.service._root(context),f"{FOLDER}/编辑记录/{product['id']}-sources.json")
            self._publish(context,path,json.dumps({'work_kind':kind,'revision':expected,'sections':value['sections']},ensure_ascii=False).encode(),internal=True)
            return product

    def create(self,context,title,kind,document,document_type=None,*,source=None):
        with _LOCK:
            root=self.service._root(context,write=True)
            validate_document(document)
            if kind not in KINDS or not isinstance(title,str) or not title.strip() or len(title)>200:raise _failure()
            if document_type is not None and (not isinstance(document_type,str) or not document_type.strip() or len(document_type)>80):raise _failure()
            if kind=='documents':
                title=re.sub(r'\.docx$', '', title.strip(), flags=re.IGNORECASE).strip()
                if not title or not title.strip('. '):raise _failure('litigation_invalid','请填写有效的文件名称。')
            product_id=uuid.uuid4().hex
            name=re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',title.strip()).strip('. ')[:80] or '文书'
            path=safe_path(root,f'{FOLDER}/{name}.docx');number=2
            while path.exists():
                path=safe_path(root,f'{FOLDER}/{name}（{number}）.docx');number+=1
            display_title=title.strip() if number==2 else title.strip()[:190]+f'（{number-1}）'
            record={'owner':OWNER,'schema_version':1,'id':product_id,'title':display_title,'kind':kind,
                    'revision':1,'document':document,'updated_at':datetime.now(timezone.utc).isoformat()}
            if kind=='documents':
                record.update(draft_spacing_version=1,draft_format_version=1,document_type=document_type or title.strip())
            if source is not None:
                # The source Word package owns its formatting; JSON is only its AI projection.
                record.update(native_word=True,draft_spacing_version=1)
                from .native_word import projection_is_complete
                record['projection_complete']=projection_is_complete(source)
                record.pop('draft_format_version',None)
            encoded=self._encode(record,source)
            stored=json.loads(parts(encoded)[META])
            index=safe_path(root,f'{FOLDER}/编辑记录/{product_id}-index.json')
            self._publish(context,index,json.dumps({'path':str(path.relative_to(root)),'record':stored},ensure_ascii=False).encode(),internal=True)
            self._publish(context,path,encoded)
            return self.read(context,product_id)

    def list(self,context,kind=None):
        with _LOCK:
            self._recover_publications(context)
            root=self.service._root(context);folder=safe_path(root,FOLDER);rows=[];errors=[]
            if not folder.exists():return {'products':[],'errors':[]}
            indexed=set()
            index_folder=safe_path(root,f'{FOLDER}/编辑记录')
            if index_folder.exists():
                for index in index_folder.glob('*-index.json'):
                    if index.is_symlink():continue
                    try:indexed.add(str(safe_path(root,json.loads(index.read_text(encoding='utf8'))['path']).relative_to(root)))
                    except (ValueError,KeyError,TypeError):continue
            for path in sorted(folder.glob('*.docx')):
                if path.name.startswith('.'):continue
                if len(rows)+len(errors)>=1000:break
                try:
                    safe_path(root,str(path.relative_to(root)))
                    if META not in parts(checked_bytes(path)) and str(path.relative_to(root)) not in indexed:continue
                    _,r=self._read(path)
                    if kind is None or r['kind']==kind:rows.append({**{k:r[k] for k in ('id','title','kind','revision','updated_at','external_update')},'path':str(path.relative_to(root))})
                except Exception as e:
                    if not isinstance(e,(ToolError,AttachmentDomainError,OSError)):raise
                    errors.append({'path':str(path.relative_to(root)),'notice':'此文书暂时无法读取，原件已保留。'})
            return {'products':rows,'errors':errors}

    def read(self,context,product_id):
        with _LOCK:
            root=self.service._root(context);path=self._file(context,product_id);data,record=self._read(path)
            record['path']=str(path.relative_to(root))
            if record['external_update']:
                # Do not expose obsolete embedded JSON as the current Word body.
                record['document']=None
                record['external_content']=inspect_docx(data)
            draft=self._draft_path(context,product_id)
            if draft.exists():
                try:
                    value=json.loads(draft.read_text(encoding='utf8'))
                    validate_document(value['document'])
                    self.service._register_internal(context,draft)
                    record['draft']=value
                    record['draft_current']=value['base_hash']==record['file_hash'] and not record['external_update']
                except (ValueError,KeyError,TypeError):
                    record['draft_error']='草稿无法读取，原文件已保留。'
            recovery=safe_path(root,f'{FOLDER}/编辑记录/{self._id(product_id)}-recovery.json')
            if recovery.exists():
                try:
                    value=json.loads(recovery.read_text(encoding='utf8'));validate_document(value['document']);record['recovery']=value
                    self.service._register_internal(context,recovery)
                except (ValueError,KeyError,TypeError):record['draft_error']='恢复草稿无法读取，原文件已保留。'
            return record

    def _finish_rename(self,context,journal):
        root=self.service._root(context,write=True)
        try:
            if journal.is_symlink() or journal.stat().st_size>2048:raise ValueError()
            item=json.loads(journal.read_bytes());product_id=self._id(item['id'])
            for key in ('old','new'):
                if Path(item[key]).name!=item[key] or item[key].startswith('.') or not item[key].endswith('.docx'):raise ValueError()
            if not re.fullmatch(r'\.verilex-edit-history-[a-f0-9]{32}\.docx',item['backup']):raise ValueError()
            old=safe_path(root,f"{FOLDER}/{item['old']}");new=safe_path(root,f"{FOLDER}/{item['new']}")
            backup=safe_path(root,FOLDER)/item['backup']
            if backup.is_symlink():raise ValueError()
            if not new.exists():
                if not old.exists():raise ValueError()
                journal.unlink();return
            data,record=self._read(new)
            if record['id']!=product_id or fingerprint(data)!=item['new_hash']:raise ValueError()
            if old.exists():
                if fingerprint(old.read_bytes())!=item['old_hash']:
                    # Keep both versions recoverable, and retain the externally updated original.
                    os.rename(new,safe_path(root,FOLDER)/f'.verilex-edit-history-{uuid.uuid4().hex}.docx')
                    journal.unlink()
                    raise _failure('litigation_file_conflict','重命名期间原文件已有更新，原文件及副本已保留，请刷新。')
                os.rename(old,backup)
                if fingerprint(backup.read_bytes())!=item['old_hash']:
                    try:os.link(backup,old)
                    except FileExistsError:pass
                    raise ValueError()
            index=safe_path(root,f'{FOLDER}/编辑记录/{product_id}-index.json')
            stored=json.loads(parts(data)[META])
            self._publish(context,index,json.dumps({'path':str(new.relative_to(root)),'record':stored},ensure_ascii=False).encode(),index.read_bytes() if index.exists() else None,history=False,internal=True)
            draft=self._draft_path(context,product_id)
            if draft.exists():
                raw=draft.read_bytes();value=json.loads(raw)
                if value.get('base_hash')==item['old_hash']:
                    value['base_hash']=item['new_hash']
                    self._publish(context,draft,json.dumps(value,ensure_ascii=False).encode(),raw,history=False,internal=True)
            journal.unlink()
        except (OSError,ValueError,KeyError,TypeError):
            raise _failure('litigation_save_failed','文书重命名尚未完成，原文件已保留，请重试。') from None

    def rename(self,context,product_id,base_hash,title):
        with _LOCK:
            root=self.service._root(context,write=True)
            path=self._file(context,product_id);data,current=self._read(path)
            if current['kind']!='documents' or current['file_hash']!=base_hash or current['external_update']:
                raise _failure('litigation_file_conflict','文书已有更新，请重新打开后修改名称。')
            if not isinstance(title,str):raise _failure()
            title=re.sub(r'\.docx$', '', title.strip(), flags=re.IGNORECASE).strip()
            if not title or title!=title.strip('. ') or len(title)>80 or re.search(r'[\\/:*?"<>|\x00-\x1f]',title):
                raise _failure('litigation_invalid','请输入有效的文件名称，不包含路径或特殊字符。')
            if title==path.stem and title==current['title']:return self.read(context,product_id)
            name=title;target=safe_path(root,f'{FOLDER}/{name}.docx');number=2
            while target.exists():
                target=safe_path(root,f'{FOLDER}/{title}（{number}）.docx');name=target.stem;number+=1
            record={k:v for k,v in current.items() if k not in ('file_hash','external_update')}
            record.update(title=name,revision=current['revision']+1,updated_at=datetime.now(timezone.utc).isoformat())
            encoded=self._encode(record,data)
            journal=safe_path(root,f'{FOLDER}/编辑记录/rename-pending-{uuid.uuid4().hex}.json')
            entry={'id':product_id,'old':path.name,'new':target.name,'backup':f'.verilex-edit-history-{uuid.uuid4().hex}.docx','old_hash':base_hash,'new_hash':fingerprint(encoded)}
            self._publish(context,journal,json.dumps(entry).encode(),history=False,internal=True)
            try:self._publish(context,target,encoded,history=False)
            except BaseException:
                journal.unlink();raise
            self._finish_rename(context,journal)
            return self.read(context,product_id)

    def delete(self,context,product_id,base_hash):
        """Move the current owned file out of the active list; retain recoverable bytes."""
        with _LOCK:
            root=self.service._root(context,write=True)
            path=self._file(context,product_id);data,current=self._read(path)
            if current['kind']!='documents' or current['file_hash']!=base_hash:
                raise _failure('litigation_file_conflict','文书已有更新，请重新打开后再删除。')
            folder=safe_path(root,f'{FOLDER}/已删除文书/{self._id(product_id)}-{uuid.uuid4().hex}')
            folder.mkdir(parents=True,exist_ok=False)
            target=safe_path(root,str((folder/path.name).relative_to(root)))
            try:
                os.rename(path,target)
                if target.read_bytes()!=data:
                    # Preserve a concurrent external edit, without overwriting another save.
                    try:os.link(target,path)
                    except FileExistsError:pass
                    raise _failure('litigation_file_conflict','删除期间文书已有更新，文件已保留，请刷新。')
            except OSError:
                raise _failure('litigation_save_failed','无法移动文书，请检查文件夹后重试。') from None
            return {'id':product_id,'path':str(target.relative_to(root))}

    def _draft_path(self,context,product_id):
        root=self.service._root(context)
        return safe_path(root,f'{FOLDER}/编辑记录/{self._id(product_id)}.json')

    def draft(self,context,product_id,base_hash,document):
        with _LOCK:
            self.service._root(context,write=True);validate_document(document)
            _,current=self._read(self._file(context,product_id))
            if current['file_hash']!=base_hash or current['external_update']:
                root=self.service._root(context,write=True)
                recovery=safe_path(root,f'{FOLDER}/编辑记录/{self._id(product_id)}-recovery.json')
                encoded=json.dumps({'base_hash':base_hash,'document':document,'updated_at':datetime.now(timezone.utc).isoformat()},ensure_ascii=False).encode()
                self._publish(context,recovery,encoded,recovery.read_bytes() if recovery.exists() else None,history=False,internal=True)
                raise _failure('litigation_file_conflict','文件已有外部更新，旧编辑已另存为恢复草稿。')
            path=self._draft_path(context,product_id)
            payload={'base_hash':base_hash,'document':document,'updated_at':datetime.now(timezone.utc).isoformat()}
            encoded=json.dumps(payload,ensure_ascii=False).encode()
            self._publish(context,path,encoded,path.read_bytes() if path.exists() else None,history=False,internal=True)
            return payload

    def save(self,context,product_id,base_hash,document,title=None,*,receipt=None):
        with _LOCK:
            self.service._root(context,write=True);validate_document(document)
            path=self._file(context,product_id);data,current=self._read(path)
            if current['file_hash']!=base_hash or current['external_update']:
                raise _failure('litigation_file_conflict','文件已有更新，未覆盖，请重新读取。')
            # Persist the complete recovery body before publication can fail.
            self.draft(context,product_id,base_hash,document)
            record={k:v for k,v in current.items() if k not in ('file_hash','external_update')}
            if title is not None:
                if not isinstance(title,str) or not title.strip() or len(title)>200:raise _failure()
                record['title']=title.strip()
            record.pop("editor_receipt",None)
            if receipt is not None:record["editor_receipt"]=receipt
            record.update(document=document,revision=current['revision']+1,updated_at=datetime.now(timezone.utc).isoformat())
            source=None
            if current.get('native_word'):
                from .native_word import update_document, project
                if receipt and receipt.get('action')=='undo' and current.get('editor_receipt',{}).get('native_before_hash'):
                    expected=current['editor_receipt']['native_before_hash'];source=None
                    for previous in path.parent.glob('.verilex-edit-history-*.docx'):
                        if previous.is_symlink():continue
                        candidate=checked_bytes(previous)
                        if fingerprint(candidate)==expected:source=candidate;break
                    if source is None or project(source)!=document:raise _failure('litigation_proposal_stale','原始版本无法核对，未撤销当前文书。')
                else:source=update_document(data,current['document'],document,pleading=bool(current.get('draft_format_version')))
                if receipt and receipt.get('action')=='accept':record['editor_receipt']={**receipt,'native_before_hash':base_hash}
                record['document']=project(source)
            encoded=self._encode(record,source)
            self._publish(context,path,encoded,data)
            # Office may discard the embedded metadata on its next save. Keep
            # the identity fallback at the last successfully published revision.
            root=self.service._root(context,write=True)
            index=safe_path(root,f'{FOLDER}/编辑记录/{self._id(product_id)}-index.json')
            stored=json.loads(parts(encoded)[META])
            self._publish(context,index,json.dumps({'path':str(path.relative_to(root)),'record':stored},ensure_ascii=False).encode(),index.read_bytes() if index.exists() else None,history=False,internal=True)
            return self.read(context,product_id)

    def open_word(self,context,product_id,base_hash):
        import base64
        from .native_word import project
        with _LOCK:
            current=self.read(context,product_id)
            if (current['kind']!='documents' and not current.get('native_word')) or current['external_update'] or current['file_hash']!=base_hash:
                raise _failure('litigation_file_conflict','文书已有变化，请重新打开。')
            if not current.get('native_word'):
                if current.get('draft_current') and current['draft']['document']!=current['document']:
                    current=self.save(context,product_id,base_hash,current['draft']['document'])
                path=self._file(context,product_id);data,record=self._read(path)
                record={k:v for k,v in record.items() if k not in ('file_hash','external_update')}
                record.update(native_word=True,document=project(data))
                self._publish(context,path,self._encode(record,data),data)
                current=self.read(context,product_id)
            if current['kind']=='documents' and not current.get('draft_spacing_version'):
                from .native_word import compact_generated_spacing
                path=self._file(context,product_id);data,record=self._read(path)
                record={k:v for k,v in record.items() if k not in ('file_hash','external_update')}
                record.update(draft_spacing_version=1,revision=record['revision']+1)
                encoded=self._encode(record,compact_generated_spacing(data))
                self._publish(context,path,encoded,data)
                index=safe_path(self.service._root(context),f"{FOLDER}/编辑记录/{self._id(product_id)}-index.json")
                stored=json.loads(parts(encoded)[META])
                self._publish(context,index,json.dumps({'path':str(path.relative_to(self.service._root(context))),'record':stored},ensure_ascii=False).encode(),index.read_bytes() if index.exists() else None,history=False,internal=True)
                current=self.read(context,product_id)
            data=self._file(context,product_id).read_bytes()
            if 'projection_complete' not in current:
                from .native_word import projection_is_complete
                current={**current,'projection_complete':projection_is_complete(data)}
            from .native_word import tracking_enabled
            return {'product':current,'content':base64.b64encode(data).decode(),'tracking_enabled':tracking_enabled(data)}

    def save_word(self,context,product_id,base_hash,content):
        import base64
        from .native_word import project
        try:data=base64.b64decode(content,validate=True)
        except (ValueError,TypeError):raise _failure() from None
        if len(data)>MAX_BYTES:raise _failure('litigation_too_large','文书超过保存上限。')
        checked_bytes(data)
        document=project(data)
        with _LOCK:
            self.service._root(context,write=True)
            path=self._file(context,product_id);before,current=self._read(path)
            if (current['kind']!='documents' and not current.get('native_word')) or current['external_update'] or current['file_hash']!=base_hash:
                raise _failure('litigation_file_conflict','文书已有变化，当前输入仍保留在编辑区，请另存副本。')
            # The editor owns bytes, not product identity or metadata supplied in them.
            record={k:v for k,v in current.items() if k not in ('file_hash','external_update','editor_receipt')}
            record.update(native_word=True,document=document,revision=current['revision']+1,updated_at=datetime.now(timezone.utc).isoformat())
            from .native_word import projection_is_complete
            record['projection_complete']=projection_is_complete(data)
            encoded=self._encode(record,data)
            self._publish(context,path,encoded,before)
            index=safe_path(self.service._root(context),f"{FOLDER}/编辑记录/{self._id(product_id)}-index.json")
            stored=json.loads(parts(encoded)[META])
            self._publish(context,index,json.dumps({'path':str(path.relative_to(self.service._root(context))),'record':stored},ensure_ascii=False).encode(),index.read_bytes() if index.exists() else None,history=False,internal=True)
            return self.read(context,product_id)

    def export_document(self,context,product_id,base_hash,document,title,format):
        import base64
        from .evidence_conversion import convert
        if format not in ('docx','pdf'):raise _failure()
        with _LOCK:
            current=self.read(context,product_id)
            if current['kind']!='documents' or current['file_hash']!=base_hash or current['external_update']:raise _failure('litigation_file_conflict','文书已有变化，请重新打开后导出。')
            if current.get('native_word'):
                if document!=current['document']:raise _failure('litigation_file_conflict','请等待 Word 正文保存完成后导出。')
                saved=current
            else:saved=self.save(context,product_id,base_hash,document,current['title'])
            data=self._file(context,product_id).read_bytes()
        exported=convert(data,'.docx') if format=='pdf' else data
        if len(exported)>20*1024*1024:raise _failure('litigation_invalid','导出文件超过20 MiB。')
        with _LOCK:
            latest=self.read(context,product_id)
            if latest['file_hash']!=saved['file_hash'] or (latest.get('draft_current') and latest['draft']['document']!=document):
                raise _failure('litigation_file_conflict','转换期间文书已有变化，请按最新正文重新导出。')
            name=re.sub(r'[\\/:*?"<>|\x00-\x1f\u202a-\u202e\u2066-\u2069]','_',saved['title']).strip('. ')[:80] or '诉讼文书'
            root=self.service._root(context,write=True)
            # Exports are user-facing copies in the active workspace, never overwrite a file.
            number=1
            while True:
                filename=f'{name}{"" if number==1 else f"（{number}）"}.{format}'
                path=safe_path(root,filename)
                try:self._publish(context,path,exported,history=False);break
                except ToolError as error:
                    if error.code!='litigation_file_conflict' or not path.exists():raise
                    number+=1
            return {'filename':filename,'path':str(path.relative_to(root)),'content':base64.b64encode(exported).decode(),'product':saved}

    def history(self,context,product_id):
        with _LOCK:
            current=self.read(context,product_id);path=self._file(context,product_id);rows=[]
            for old in path.parent.glob('.verilex-edit-history-*.docx'):
                if old.is_symlink():continue
                _,r=self._read(old)
                if r['id']==product_id:rows.append(r)
            return {'versions':sorted(rows,key=lambda r:r['revision'],reverse=True)}

    def open_word_history(self,context,product_id,revision,base_hash):
        import base64
        from .native_word import tracking_enabled
        with _LOCK:
            path=self._file(context,product_id)
            self.read(context,product_id)
            for old in path.parent.glob('.verilex-edit-history-*.docx'):
                if old.is_symlink():continue
                data,record=self._read(old)
                if record['id']==product_id and record['revision']==revision and record['file_hash']==base_hash and not record['external_update']:
                    return {'product':record,'content':base64.b64encode(data).decode(),'tracking_enabled':tracking_enabled(data)}
            raise _failure('litigation_file_conflict','历史版本已变化或不可用，请重新打开历史。')


    def copy_product(self,context,product_id,base_hash,document,title):
        """Create an independent copy without flattening the source Word package."""
        with _LOCK:
            data,current=self._read(self._file(context,product_id))
            if current['file_hash']!=base_hash or current['external_update']:
                raise _failure('litigation_file_conflict','文书已有更新，请重新读取。')
            if current.get('native_word'):
                from .native_word import update_document
                if document != current['document']:
                    data=update_document(data,current['document'],document,pleading=bool(current.get('draft_format_version')))
                return self.create(context,title,current['kind'],document,current.get('document_type'),source=data)
            return self.create(context,title,current['kind'],document,current.get('document_type'))

    def templates(self,context):
        root=self.service._root(context);folder=safe_path(root,'我的文书模板')
        if not folder.exists():return {'templates':[]}
        return {'templates':[{'path':str(p.relative_to(root)),'name':p.stem} for p in sorted(folder.glob('*.docx')) if not p.is_symlink() and not p.name.startswith('.')][:200]}

    def save_template(self,context,product_id,base_hash,document,title):
        with _LOCK:
            root=self.service._root(context,write=True)
            data,current=self._read(self._file(context,product_id))
            if current['file_hash']!=base_hash or current['external_update']:raise _failure('litigation_file_conflict','文书已有更新，请重新读取。')
            if current.get('native_word'):
                if document!=current['document']:raise _failure('litigation_file_conflict','请等待 Word 正文保存完成后再保存模板。')
            else:data=render_docx(document)
            name=re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',title.strip()).strip('. ')[:80] or '文书模板'
            path=safe_path(root,f'我的文书模板/{name}.docx');number=2
            while path.exists():
                path=safe_path(root,f'我的文书模板/{name}（{number}）.docx');number+=1
            self._publish(context,path,data)
            return {'path':str(path.relative_to(root)),'name':path.stem}

    def import_template(self,context,data,filename,title):
        from verilex_runtime.attachments.errors import AttachmentDomainError
        if not filename.lower().endswith('.docx'):
            raise _failure('litigation_invalid','请选择 .docx 文件；旧版 .doc 请先用 Word/WPS 另存为 .docx。')
        if not data or len(data)>MAX_BYTES:
            raise _failure('litigation_too_large','模板文件不能为空，且不能超过20 MiB。')
        name=title.strip()
        if name.lower().endswith('.docx'):name=name[:-5].rstrip()
        if not name.strip('. ') or len(name)>80 or re.search(r'[\\/:*?"<>|\x00-\x1f\u202a-\u202e\u2066-\u2069]',name):
            raise _failure('litigation_invalid','请填写有效的模板名称，不使用路径或特殊字符。')
        with _LOCK:
            root=self.service._root(context,write=True)
            try:checked_bytes(data)
            except AttachmentDomainError:
                raise _failure('litigation_invalid','无法读取这个 Word 文件，请用 Word/WPS 检查后另存为 .docx。') from None
            name=name.strip('. ');number=1
            while True:
                suffix='' if number==1 else f'（{number}）'
                path=safe_path(root,f'我的文书模板/{name}{suffix}.docx')
                if path.exists():number+=1;continue
                try:self._publish(context,path,data);break
                except ToolError as error:
                    if error.code!='litigation_file_conflict' or not path.exists():raise
                    number+=1
            return {'path':str(path.relative_to(root)),'name':path.stem}

    def import_copy(self,context,path,title,kind,text_only=False,document_type=None):
        from .editor import import_docx
        with _LOCK:
            root=self.service._root(context,write=True);source=safe_path(root,path)
            if source.suffix.lower()!='.docx':raise _failure('litigation_editor_readonly','请选择Word文档。')
            data=checked_bytes(source)
            if not text_only:
                from .native_word import project
                return self.create(context,title,kind,project(data),document_type,source=data)
            return self.create(context,title,kind,import_docx(data,text_only=text_only),document_type)
