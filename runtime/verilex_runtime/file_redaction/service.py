"""Private local task storage. Upload originals are never persisted."""
import hashlib
import json
import threading
import unicodedata
import uuid
from datetime import datetime,timezone
from .errors import RedactionError,import_error
from verilex_runtime.attachments.errors import AttachmentDomainError
from .ner import detect
from .pipeline import prepare,render,restore,validate,review_mapping
from .rules import LABELS


def payload(job):
    value=json.dumps({k:job[k] for k in ('mapping','kinds','custom')},ensure_ascii=False,sort_keys=True)
    return value,hashlib.sha256(value.encode()).hexdigest()


class RedactionService:
    def __init__(self,database,detector=detect):
        self.db=database; self.detector=detector; self.lock=threading.RLock(); self.processing=threading.Lock()

    def process(self, filename, raw, kinds, replacements, variant):
        if not filename.lower().endswith(('.doc', '.docx', '.md')):
            raise RedactionError('redaction_unsupported')
        if filename.lower().endswith('.md') and variant != 'markdown':
            raise RedactionError('redaction_markdown_format')
        self._options(kinds, [item['old'] for item in replacements])
        if len({item['old'] for item in replacements}) != len(replacements):
            raise RedactionError()
        if any(not isinstance(item['new'], str) or len(item['new']) > 200 for item in replacements):
            raise RedactionError()
        if len(raw) > 20*1024*1024:
            raise RedactionError('redaction_limit')
        if not self.processing.acquire(blocking=False):
            raise RedactionError('redaction_busy')
        try:
            if filename.lower().endswith('.doc'):
                from verilex_runtime.attachments.legacy_doc import convert_doc
                raw = convert_doc(raw)
            from .direct import export_direct, export_markdown
            if filename.lower().endswith('.md'):
                return export_markdown(raw, kinds, replacements, self.detector)
            return export_direct(raw, kinds, replacements, variant, self.detector)
        except AttachmentDomainError as error:
            raise import_error(error.code) from None
        finally:
            self.processing.release()

    def recent(self):
        with self.db.connect() as c:
            return [dict(r) for r in c.execute('SELECT j.id,j.filename,j.created_at,j.revision,EXISTS(SELECT 1 FROM redaction_mappings m WHERE m.job_id=j.id) AS restorable FROM redaction_jobs j ORDER BY created_at DESC LIMIT 100')]

    def _read(self,identifier,revision=None):
        with self.db.connect() as c:
            row=c.execute('SELECT * FROM redaction_jobs WHERE id=?',(identifier,)).fetchone()
            saved=c.execute('SELECT * FROM redaction_mappings WHERE job_id=?',(identifier,)).fetchone()
        if row is None: raise RedactionError('redaction_not_found')
        job=dict(row)
        if revision is not None and revision!=job['revision']: raise RedactionError('redaction_conflict')
        job['restorable']=False; job['mapping']=None; job['kinds']=[]; job['custom']=[]
        if hashlib.sha256(job['canonical']).hexdigest()!=job['digest']: raise RedactionError('restore_unavailable')
        # Lazy repair of old records, including records whose mapping was
        # deleted. Do not bump the user revision: only hidden package metadata
        # changes, never text or occurrence positions. Read/write connections
        # are separate so callers need no nested SQLite write transaction.
        from verilex_runtime.documents.scrub import remove_editor_metadata
        from verilex_runtime.attachments.errors import AttachmentDomainError
        try:
            cleaned = remove_editor_metadata(job['canonical'])
        except (AttachmentDomainError, ValueError, KeyError):
            raise RedactionError('restore_unavailable') from None
        if cleaned != job['canonical']:
            digest = hashlib.sha256(cleaned).hexdigest()
            with self.db.connect() as c:
                changed = c.execute(
                    'UPDATE redaction_jobs SET canonical=?,digest=? WHERE id=? AND revision=? AND digest=?',
                    (cleaned, digest, identifier, job['revision'], job['digest']))
                if changed.rowcount != 1:
                    raise RedactionError('redaction_conflict')
            job.update(canonical=cleaned, digest=digest)
        if saved:
            try:
                if hashlib.sha256(saved['payload'].encode()).hexdigest()!=saved['digest']: raise ValueError()
                job.update(json.loads(saved['payload'])); validate(job); job['restorable']=True
            except (ValueError,KeyError,TypeError,RedactionError):
                job.update(mapping=None,kinds=[],custom=[],restorable=False)
        return job

    def get(self,identifier):
        with self.lock:
            job=self._read(identifier)
            result={k:v for k,v in job.items() if k not in {'canonical','digest'}}
            if job['restorable']: result['mapping']=review_mapping(job)
            return result

    def _write_mapping(self,c,job):
        value,digest=payload(job)
        c.execute('INSERT OR REPLACE INTO redaction_mappings(job_id,payload,digest) VALUES (?,?,?)',(job['id'],value,digest))

    def create(self,filename,raw,kinds,custom):
        if not filename.lower().endswith(('.doc', '.docx')): raise RedactionError('redaction_unsupported')
        self._options(kinds,custom)
        if len(raw)>20*1024*1024: raise RedactionError('redaction_limit')
        if not self.processing.acquire(blocking=False): raise RedactionError('redaction_busy')
        try:
            if filename.lower().endswith('.doc'):
                from verilex_runtime.attachments.legacy_doc import convert_doc
                raw = convert_doc(raw)
            job=prepare(raw,kinds,custom,self.detector)
        except AttachmentDomainError as e:
            raise import_error(e.code) from None
        finally: self.processing.release()
        job['id']=str(uuid.uuid4()); job['revision']=1
        name=''.join(ch for ch in filename.replace('\\','/').rsplit('/',1)[-1] if not unicodedata.category(ch).startswith('C'))[:120] or 'Word 文件'
        with self.lock,self.db.connect() as c:
            c.execute('INSERT INTO redaction_jobs VALUES (?,?,?,?,?,?)',(job['id'],name,datetime.now(timezone.utc).isoformat(),1,job['canonical'],job['digest']))
            self._write_mapping(c,job)
        return self.get(job['id'])

    def _options(self,kinds,custom):
        if len(kinds)>len(LABELS) or any(k not in LABELS or k=='custom' for k in kinds): raise RedactionError()
        if len(custom)>100 or any(not isinstance(w,str) or not w.strip() or len(w)>200 for w in custom): raise RedactionError()

    def update(self,identifier,revision,item_id,keep):
        with self.lock:
            job=self._read(identifier,revision); validate(job)
            matches=[m for m in job['mapping'] if m['id']==item_id]
            if len(matches)!=1: raise RedactionError()
            matches[0]['keep']=keep
            self._save_change(job)
            return self.get(identifier)

    def _save_change(self,job):
        with self.db.connect() as c:
            changed=c.execute('UPDATE redaction_jobs SET canonical=?,digest=?,revision=revision+1 WHERE id=? AND revision=?',(job['canonical'],job['digest'],job['id'],job['revision']))
            if changed.rowcount!=1: raise RedactionError('redaction_conflict')
            self._write_mapping(c,job)

    def rerun(self,identifier,revision,kinds,custom):
        self._options(kinds,custom)
        if not self.processing.acquire(blocking=False): raise RedactionError('redaction_busy')
        try:
            with self.lock: job=self._read(identifier,revision); raw=restore(job)
            changed=prepare(raw,kinds,custom,self.detector,job['mapping'])
            changed.update(id=identifier,revision=revision)
            with self.lock: self._save_change(changed)
        finally: self.processing.release()
        return self.get(identifier)

    def export(self,identifier,revision,variant):
        with self.lock: job=self._read(identifier,revision)
        if not job['restorable']:
            if variant!='placeholder': raise RedactionError('restore_unavailable')
            return job['canonical']
        result=render(job,variant,self.detector)
        with self.lock: self._read(identifier,revision)
        return result

    def delete_mapping(self,identifier,revision):
        with self.lock,self.db.connect() as c:
            job=self._read(identifier,revision)
            c.execute('DELETE FROM redaction_mappings WHERE job_id=?',(identifier,))
            c.execute('UPDATE redaction_jobs SET revision=revision+1 WHERE id=?',(identifier,))
        return self.get(identifier)
