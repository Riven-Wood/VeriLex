from __future__ import annotations

import os
from urllib.parse import quote, unquote_to_bytes

import anyio
from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from verilex_runtime.attachments.service import AttachmentService
from verilex_runtime.storage.models import ResolvedAttachment, ResolvedUpload


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UploadResponse(StrictSchema):
    id: str
    thread_id: str
    display_name: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: str
    expires_at: str


class AttachmentResponse(StrictSchema):
    id: str
    thread_id: str
    turn_id: str
    display_name: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: str


def _upload_response(resolved: ResolvedUpload) -> UploadResponse:
    return UploadResponse(
        id=resolved.upload.id,
        thread_id=resolved.upload.thread_id,
        display_name=resolved.upload.display_name,
        media_type=resolved.blob.media_type,
        size_bytes=resolved.blob.size_bytes,
        sha256=resolved.blob.sha256,
        created_at=resolved.upload.created_at,
        expires_at=resolved.upload.expires_at,
    )


def _attachment_response(resolved: ResolvedAttachment) -> AttachmentResponse:
    return AttachmentResponse(
        id=resolved.attachment.id,
        thread_id=resolved.attachment.thread_id,
        turn_id=resolved.attachment.turn_id,
        display_name=resolved.attachment.display_name,
        media_type=resolved.attachment.media_type,
        size_bytes=resolved.attachment.size_bytes,
        sha256=resolved.blob.sha256,
        created_at=resolved.attachment.created_at,
    )


async def _descriptor_chunks(descriptor: int):
    try:
        while True:
            chunk = await anyio.to_thread.run_sync(os.read, descriptor, 1024 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        os.close(descriptor)


def _content_disposition(filename: str) -> str:
    encoded = quote(filename, safe="")
    if encoded != filename:
        return f"attachment; filename=\"attachment\"; filename*=UTF-8''{encoded}"
    return f'attachment; filename="{filename}"'


def _strict_filename_values(request: Request) -> list[str]:
    values: list[str] = []
    try:
        for field in request.scope.get("query_string", b"").split(b"&"):
            key, separator, value = field.partition(b"=")
            _validate_percent_escapes(key)
            _validate_percent_escapes(value)
            decoded_key = unquote_to_bytes(key.replace(b"+", b" ")).decode(
                "utf-8", errors="strict"
            )
            if decoded_key == "filename":
                if not separator:
                    value = b""
                values.append(
                    unquote_to_bytes(value.replace(b"+", b" ")).decode(
                        "utf-8", errors="strict"
                    )
                )
    except (UnicodeDecodeError, ValueError):
        return []
    return values


def _validate_percent_escapes(value: bytes) -> None:
    hexadecimal = b"0123456789abcdefABCDEF"
    index = 0
    while index < len(value):
        if value[index] != ord("%"):
            index += 1
            continue
        if (
            index + 2 >= len(value)
            or value[index + 1] not in hexadecimal
            or value[index + 2] not in hexadecimal
        ):
            raise ValueError("Invalid percent escape.")
        index += 3


def build_attachment_router(
    service: AttachmentService,
) -> APIRouter:
    router = APIRouter(prefix="/v1/threads", tags=["attachments"])

    @router.post(
        "/{thread_id}/attachment-uploads",
        response_model=UploadResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_attachment(
        thread_id: str,
        request: Request,
        filename: str | None = None,
    ) -> UploadResponse:
        filename_values = _strict_filename_values(request)
        selected_filename = filename_values[0] if len(filename_values) == 1 else ""
        resolved = await service.upload(
            thread_id,
            selected_filename or "",
            request.headers.get("Content-Type"),
            request.stream(),
        )
        return _upload_response(resolved)

    @router.get(
        "/{thread_id}/attachment-uploads",
        response_model=list[UploadResponse],
    )
    async def list_uploads(thread_id: str) -> list[UploadResponse]:
        return [_upload_response(item) for item in service.list_uploads(thread_id)]

    @router.delete(
        "/{thread_id}/attachment-uploads/{upload_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_upload(thread_id: str, upload_id: str) -> Response:
        await anyio.to_thread.run_sync(service.delete_upload, thread_id, upload_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get(
        "/{thread_id}/attachments",
        response_model=list[AttachmentResponse],
    )
    async def list_attachments(
        thread_id: str,
        turn_id: str | None = None,
    ) -> list[AttachmentResponse]:
        return [
            _attachment_response(item)
            for item in service.list_attachments(thread_id, turn_id=turn_id)
        ]

    @router.get("/{thread_id}/attachments/{attachment_id}/content")
    async def download_attachment(thread_id: str, attachment_id: str) -> Response:
        resolved, descriptor = await anyio.to_thread.run_sync(
            service.open_attachment_content,
            thread_id,
            attachment_id,
        )
        return StreamingResponse(
            _descriptor_chunks(descriptor),
            headers={
                "Content-Type": resolved.blob.media_type,
                "Content-Length": str(resolved.blob.size_bytes),
                "Content-Disposition": _content_disposition(
                    resolved.attachment.display_name
                ),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    return router
