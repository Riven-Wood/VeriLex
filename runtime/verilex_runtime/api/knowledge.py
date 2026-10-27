"""User-facing source management; protected by the runtime launch token."""
import anyio
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from verilex_runtime.knowledge.service import KnowledgeService


class CreateLibrary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str = Field(min_length=1, max_length=4096)
    name: str | None = Field(default=None, max_length=120)


class UpdateLibrary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    excluded: list[str] | None = Field(default=None, max_length=2000)
    root: str | None = Field(default=None, min_length=1, max_length=4096)


def build_knowledge_router(service: KnowledgeService):
    router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])

    @router.get("")
    async def libraries():
        return await anyio.to_thread.run_sync(service.libraries)

    @router.post("")
    async def associate(body: CreateLibrary):
        identifier = await anyio.to_thread.run_sync(lambda: service.associate(body.root, body.name))
        await anyio.to_thread.run_sync(lambda: service.sync(identifier))
        return {"id": identifier}

    @router.patch("/{identifier}")
    async def update(identifier: str, body: UpdateLibrary):
        await anyio.to_thread.run_sync(lambda: service.update(identifier, **body.model_dump(exclude_none=True)))
        await anyio.to_thread.run_sync(lambda: service.sync(identifier))
        return {"ok": True}

    @router.delete("/{identifier}")
    async def detach(identifier: str):
        await anyio.to_thread.run_sync(lambda: service.detach(identifier))
        return {"ok": True}

    @router.post("/{identifier}/sync")
    async def sync(identifier: str):
        await anyio.to_thread.run_sync(lambda: service.sync(identifier))
        return {"ok": True}

    @router.get("/quotes/{quote_id}")
    async def historical_quote(quote_id: str, thread_id: str):
        return await anyio.to_thread.run_sync(lambda: service.quote_view(quote_id, thread_id))

    @router.get("/{identifier}/documents")
    async def documents(identifier: str, q: str = Query(default="", max_length=256),
                        offset: int = Query(default=0, ge=0, le=20000),
                        folder_id: str = Query(default="", max_length=4096)):
        return await anyio.to_thread.run_sync(lambda: service.documents(identifier, q, offset, folder_id=folder_id))

    @router.get("/{identifier}/documents/{document_id}")
    async def read(identifier: str, document_id: str, version: str = Query(min_length=64, max_length=64),
                   start: int = Query(default=0, ge=0)):
        return await anyio.to_thread.run_sync(lambda: service.read(identifier, document_id, version, start))

    @router.get("/{identifier}/documents/{document_id}/matches")
    async def matches(identifier: str, document_id: str,
                      version: str = Query(min_length=64, max_length=64),
                      q: str = Query(min_length=1, max_length=256), start: int = Query(default=0, ge=0)):
        return await anyio.to_thread.run_sync(lambda: service.matches(identifier, document_id, version, q, start))

    return router
