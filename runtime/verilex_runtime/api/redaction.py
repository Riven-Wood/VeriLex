"""Launch-token protected local file processing. No paths or model endpoints accepted."""
import base64
import binascii
from typing import Literal
import anyio
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from verilex_runtime.file_redaction.errors import RedactionError,MESSAGES
from verilex_runtime.file_redaction.rules import DEFAULT_KINDS


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Options(Strict):
    kinds:list[str]=Field(default_factory=lambda:list(DEFAULT_KINDS),max_length=20)
    custom:list[str]=Field(default_factory=list,max_length=100)

class Create(Options):
    filename:str=Field(min_length=1,max_length=255)
    content:str=Field(max_length=28*1024*1024)

class Replacement(Strict):
    old: str = Field(min_length=1, max_length=200)
    new: str = Field(default='', max_length=200)

class Process(Strict):
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=28*1024*1024)
    kinds: list[str] = Field(default_factory=lambda:list(DEFAULT_KINDS), max_length=20)
    replacements: list[Replacement] = Field(default_factory=list, max_length=100)
    variant: Literal['word', 'markdown']

class Version(Strict):
    revision:int=Field(ge=1)

class Update(Version):
    item_id:str=Field(min_length=1,max_length=64)
    keep:bool

class Rerun(Options):
    revision:int=Field(ge=1)

class Export(Version):
    variant:Literal['placeholder','permanent','restore','markdown']


def build_redaction_router(service):
    router=APIRouter(prefix='/v1/redaction',tags=['redaction'])
    async def call(fn):
        try: return await anyio.to_thread.run_sync(fn)
        except RedactionError as e:
            return JSONResponse({'error':{'code':e.code,'message':MESSAGES.get(e.code,MESSAGES['redaction_invalid']),'retryable':False}},status_code=409 if e.code in {'redaction_conflict','redaction_busy'} else 422)
        except Exception:
            return JSONResponse({'error':{'code':'redaction_invalid','message':MESSAGES['redaction_invalid'],'retryable':False}},status_code=500)

    @router.get('')
    async def recent(): return await call(service.recent)

    @router.post('')
    async def create(body:Create):
        def work():
            try: raw=base64.b64decode(body.content,validate=True)
            except (ValueError,binascii.Error): raise RedactionError() from None
            return service.create(body.filename,raw,body.kinds,body.custom)
        return await call(work)

    @router.post('/process')
    async def process(body: Process):
        def work():
            try: raw = base64.b64decode(body.content, validate=True)
            except (ValueError, binascii.Error): raise RedactionError() from None
            data = service.process(body.filename, raw, body.kinds,
                                   [item.model_dump() for item in body.replacements], body.variant)
            return {'filename': '脱敏修订版.docx' if body.variant == 'word' else '脱敏版.md',
                    'content': base64.b64encode(data).decode()}
        return await call(work)

    @router.get('/{identifier}')
    async def get(identifier:str): return await call(lambda:service.get(identifier))

    @router.patch('/{identifier}')
    async def update(identifier:str,body:Update): return await call(lambda:service.update(identifier,body.revision,body.item_id,body.keep))

    @router.post('/{identifier}/rerun')
    async def rerun(identifier:str,body:Rerun): return await call(lambda:service.rerun(identifier,body.revision,body.kinds,body.custom))

    @router.post('/{identifier}/export')
    async def export(identifier:str,body:Export):
        def work():
            data=service.export(identifier,body.revision,body.variant)
            names={'placeholder':'脱敏版.docx','permanent':'永久抹除版.docx','restore':'文字还原版.docx','markdown':'脱敏版.md'}
            return {'filename':names[body.variant],'content':base64.b64encode(data).decode()}
        return await call(work)

    @router.post('/{identifier}/delete-mapping')
    async def delete(identifier:str,body:Version): return await call(lambda:service.delete_mapping(identifier,body.revision))
    return router
