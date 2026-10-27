"""Authenticated desktop-only ima setup; never registered as model tools."""
import anyio
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_id: SecretStr
    api_key: SecretStr


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(min_length=32, max_length=32)
    ids: list[str] = Field(min_length=1, max_length=50)


def build_ima_router(service):
    router = APIRouter(prefix='/v1/ima', tags=['ima'])

    @router.get('')
    async def status():
        return await anyio.to_thread.run_sync(service.status)

    @router.post('/connect')
    async def connect(body: Credentials):
        return await anyio.to_thread.run_sync(lambda: service.probe(
            body.client_id.get_secret_value().strip(), body.api_key.get_secret_value().strip()))

    @router.post('/resume')
    async def resume():
        return await anyio.to_thread.run_sync(service.resume)

    @router.get('/choices')
    async def choices(token: str = Query(min_length=32, max_length=32),
                      q: str = Query(default='', max_length=256), cursor: str = Query(default='', max_length=4096)):
        return await anyio.to_thread.run_sync(lambda: service.choices(token, q, cursor))

    @router.post('/selection')
    async def save(body: Selection):
        return await anyio.to_thread.run_sync(lambda: service.save(body.token, body.ids))

    @router.delete('')
    async def disconnect():
        await anyio.to_thread.run_sync(service.disconnect)
        return {'ok': True}

    return router
