from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


MAX_RUNTIME_REQUEST_BYTES = 1024 * 1024
MAX_ATTACHMENT_REQUEST_BYTES = 25 * 1024 * 1024


def _is_attachment_upload(scope: Scope) -> bool:
    if scope.get("method") != "POST":
        return False
    parts = scope.get("path", "").split("/")
    return (
        len(parts) == 5
        and parts[:3] == ["", "v1", "threads"]
        and bool(parts[3])
        and parts[4] == "attachment-uploads"
    )


def _request_limit(scope: Scope) -> int:
    if _is_attachment_upload(scope):
        return MAX_ATTACHMENT_REQUEST_BYTES
    return MAX_RUNTIME_REQUEST_BYTES


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_RUNTIME_REQUEST_BYTES):
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        attachment_upload = _is_attachment_upload(scope)
        limit = _request_limit(scope) if attachment_upload else self._max_bytes
        if scope.get("method") == "POST" and scope.get("path") == "/v1/redaction":
            limit = 30 * 1024 * 1024
        if (scope.get("method") == "POST" and scope.get("path") in {"/v1/contract-reviews", "/v1/contract-reviews/documents"}
                or scope.get("method") == "PUT" and scope.get("path", "").startswith("/v1/contract-reviews/documents/")):
            limit = MAX_ATTACHMENT_REQUEST_BYTES
        path_parts = scope.get("path", "").split("/")
        if scope.get("method") == "POST" and len(path_parts)==6 and path_parts[:4]==["", "v1", "litigation", "workspaces"] and path_parts[4] and path_parts[5]=="editor-templates":
            limit = 20 * 1024 * 1024
        raw_headers = [
            (name.lower(), value) for name, value in scope.get("headers", [])
        ]
        declared_values = [
            value for name, value in raw_headers if name == b"content-length"
        ]
        has_transfer_encoding = any(
            name == b"transfer-encoding" for name, _ in raw_headers
        )
        if len(declared_values) > 1 or (
            declared_values and has_transfer_encoding
        ):
            await self._reject(scope, receive, send)
            return
        if declared_values:
            declared = declared_values[0]
            try:
                decoded = declared.decode("ascii")
                if not decoded.isdecimal() or int(decoded) > limit:
                    await self._reject(scope, receive, send)
                    return
            except (UnicodeDecodeError, ValueError):
                await self._reject(scope, receive, send)
                return

        if attachment_upload:
            await self._app(scope, receive, send)
            return

        received = 0
        response_started = False
        too_large = False
        replacement_sent = False
        replacement = self._response()

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    return {
                        "type": "http.request",
                        "body": b"",
                        "more_body": False,
                    }
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started, replacement_sent
            if message["type"] == "http.response.start":
                response_started = True
                if too_large:
                    await send(
                        {
                            "type": "http.response.start",
                            "status": replacement.status_code,
                            "headers": replacement.raw_headers,
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": replacement.body,
                        }
                    )
                    replacement_sent = True
                    return
            if replacement_sent:
                return
            await send(message)

        await self._app(scope, limited_receive, tracked_send)
        if too_large and not response_started:
            await replacement(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        await RequestBodyLimitMiddleware._response()(scope, receive, send)

    @staticmethod
    def _response() -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "request_too_large",
                    "message": "Runtime request body is too large.",
                    "retryable": False,
                }
            },
        )
