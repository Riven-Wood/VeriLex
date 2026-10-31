from pathlib import Path

import anyio
from fastapi import APIRouter, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.storage.models import WorkspaceRecord
from verilex_runtime.tools.registry import ToolError
from verilex_runtime.workspace_preview import open_path, preview_file
from verilex_runtime.workspace_files import list_workspace_files


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceCreateRequest(StrictSchema):
    root_path: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    allow_read: bool = True
    allow_write: bool = True


class WorkspaceUpdateRequest(StrictSchema):
    allow_read: bool
    allow_write: bool


class WorkspaceRenameRequest(StrictSchema):
    display_name: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")


class WorkspaceResponse(StrictSchema):
    id: str
    root_path: str
    display_name: str
    allow_read: bool
    allow_write: bool
    created_at: str
    last_used_at: str


class FilePreviewResponse(StrictSchema):
    kind: str
    name: str
    path: str
    size_bytes: int
    modified_at: str
    truncated: bool
    content: str | None = None
    media_type: str | None = None
    data_url: str | None = None


class OpenPathRequest(StrictSchema):
    path: str = Field(min_length=1)
    mode: str = Field(pattern="^(open|reveal)$")


def _response(record: WorkspaceRecord) -> WorkspaceResponse:
    return WorkspaceResponse(
        id=record.id,
        root_path=record.root_path,
        display_name=record.display_name,
        allow_read=record.allow_read,
        allow_write=record.allow_write,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
    )


def build_workspaces_router(service: WorkspaceService) -> APIRouter:
    router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])

    @router.post(
        "",
        response_model=WorkspaceResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_workspace(request: WorkspaceCreateRequest) -> WorkspaceResponse:
        return _response(
            service.create(
                request.root_path,
                request.display_name,
                allow_read=request.allow_read,
                allow_write=request.allow_write,
            )
        )

    @router.get("", response_model=list[WorkspaceResponse])
    async def list_workspaces() -> list[WorkspaceResponse]:
        return [_response(record) for record in service.list()]

    @router.get("/{workspace_id}", response_model=WorkspaceResponse)
    async def get_workspace(workspace_id: str) -> WorkspaceResponse:
        return _response(service.get(workspace_id))

    @router.patch("/{workspace_id}", response_model=WorkspaceResponse)
    async def update_workspace(
        workspace_id: str,
        request: WorkspaceUpdateRequest,
    ) -> WorkspaceResponse:
        return _response(
            service.update_permissions(
                workspace_id,
                allow_read=request.allow_read,
                allow_write=request.allow_write,
            )
        )

    @router.post("/{workspace_id}/rename", response_model=WorkspaceResponse)
    async def rename_workspace(workspace_id: str, request: WorkspaceRenameRequest) -> WorkspaceResponse:
        return _response(service.rename(workspace_id, request.display_name))

    @router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke_workspace(workspace_id: str) -> Response:
        service.revoke(workspace_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/{workspace_id}/files")
    async def workspace_files(
        workspace_id: str,
        path: str = Query(default="", max_length=4096),
        offset: int = Query(default=0, ge=0, le=10_000),
        snapshot: str = Query(default="", max_length=24),
    ) -> dict:
        record = service.get(workspace_id)
        if not record.allow_read:
            raise ToolError("read_not_allowed", "此工作区尚未允许读取文件。")
        payload = await anyio.to_thread.run_sync(
            list_workspace_files, Path(record.root_path), path, offset, snapshot
        )
        # A permission change while a slow directory is being listed must not
        # return the names after its read grant was revoked.
        if not service.get(workspace_id).allow_read:
            raise ToolError("read_not_allowed", "此工作区的读取授权已收回。")
        return payload

    @router.get("/{workspace_id}/file-preview")
    async def file_preview(workspace_id: str, path: str) -> JSONResponse:
        record = service.get(workspace_id)
        if not record.allow_read:
            raise ToolError(
                "read_not_allowed", "Workspace read access is not allowed."
            )
        payload = await anyio.to_thread.run_sync(
            preview_file, Path(record.root_path), path
        )
        return JSONResponse(
            content=payload,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @router.post("/{workspace_id}/open-path")
    async def open_workspace_path(
        workspace_id: str, request: OpenPathRequest
    ) -> dict:
        record = service.get(workspace_id)
        if not record.allow_read:
            raise ToolError(
                "read_not_allowed", "Workspace read access is not allowed."
            )
        return await open_path(
            Path(record.root_path), request.path, request.mode
        )

    return router
