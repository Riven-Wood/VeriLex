"""Authenticated user terminal API. No route is exposed as an Agent tool."""
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from verilex_runtime.terminal import TerminalManager


class Size(BaseModel):
    model_config = ConfigDict(extra="forbid")
    columns: int = Field(default=80, ge=10, le=400)
    rows: int = Field(default=24, ge=2, le=200)


class Create(Size):
    id: str = Field(pattern=r"^[a-zA-Z0-9-]{20,64}$")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1024)
    sequence: int = Field(ge=1)


def build_terminal_router(manager: TerminalManager) -> APIRouter:
    router = APIRouter(prefix="/v1/workspaces/{workspace_id}/terminals", tags=["terminal"])

    @router.get("")
    async def list_sessions(workspace_id: str):
        return manager.list(workspace_id)

    @router.post("")
    async def create_session(workspace_id: str, request: Create):
        return manager.create(workspace_id, request.id, request.columns, request.rows)

    @router.get("/{session_id}/output")
    async def output(workspace_id: str, session_id: str, after: int = Query(default=0, ge=0)):
        return await manager.output(workspace_id, session_id, after)

    @router.post("/{session_id}/input")
    async def write(workspace_id: str, session_id: str, request: Input):
        return manager.write(workspace_id, session_id, request.text, request.sequence)

    @router.post("/{session_id}/resize")
    async def resize(workspace_id: str, session_id: str, request: Size):
        return manager.resize(workspace_id, session_id, request.columns, request.rows)

    @router.delete("/{session_id}")
    async def stop(workspace_id: str, session_id: str):
        return await manager.stop(workspace_id, session_id)

    return router
