import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from verilex_runtime.scheduling.errors import ScheduleDomainError
from verilex_runtime.case_calendar.errors import CaseCalendarError, validation_message as calendar_validation_message
from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.sessions.errors import SessionDomainError
from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.storage.preferences import PreferenceDomainError
from verilex_runtime.skills import SkillServiceError
from verilex_runtime.tools.registry import ToolError
from verilex_runtime.contract_review.errors import ContractReviewError


_TOOL_ERROR_STATUS = {
    "terminal_not_found": 404,
    "terminal_workspace": 403,
    "terminal_exited": 409,
    "terminal_limit": 409,
    "terminal_input_order": 409,
    "terminal_unsupported": 422,
    "litigation_revision_conflict": 409,
    "litigation_file_conflict": 409,
    "background_task_not_found": 404,
    "background_task_not_running": 409,
    "thread_not_found": 404,
    "turn_not_found": 404,
    "file_not_found": 404,
    "preview_not_a_file": 422,
    "reveal_unsupported": 422,
}

_SKILL_ERROR_STATUS = {
    "skill_not_found": 404,
    "skill_name_invalid": 422,
    "skill_name_conflict": 409,
    "skill_name_unchangeable": 409,
    "skill_too_large": 413,
    "skill_write_failed": 500,
}

_SKILL_ERROR_RETRYABLE = frozenset({"skill_write_failed"})

_CONTRACT_ERROR_STATUS = {
    "contract_requirements_invalid": 422,
    "contract_editor_conflict": 409,
    "contract_markup_changed": 409,
    "contract_editor_selection": 409,
    "contract_editor_proposal_closed": 409,
    "contract_editor_proposal_not_found": 404,
    "contract_editor_integrity": 422,
    "contract_editor_too_large": 413,
    "contract_file_unsupported": 422,
    "contract_annotation_write_failed": 422,
    "contract_integrity_failed": 500,
    "contract_report_failed": 422,
    "contract_export_blocked": 409,
    "contract_thread_busy": 409,
    "contract_not_found": 404,
    "contract_opinion_not_found": 404,
}


logger = logging.getLogger(__name__)


_MCP_ERROR_STATUS = {
    "mcp_config_invalid": 422,
    "mcp_config_blocked": 403,
    "mcp_secret_in_arguments": 403,
    "mcp_url_credentials": 403,
    "mcp_secret_in_url": 403,
    "mcp_config_review_required": 409,
    "mcp_name_conflict": 409,
    "mcp_revision_conflict": 409,
    "mcp_test_receipt_invalid": 409,
    "mcp_test_receipt_expired": 410,
    "mcp_keychain_unavailable": 503,
    "mcp_credentials_missing": 409,
    "mcp_connect_timeout": 504,
    "mcp_connection_failed": 502,
    "mcp_schema_unsupported": 422,
    "mcp_arguments_invalid": 422,
    "mcp_tool_changed": 409,
    "mcp_tool_disabled": 409,
    "mcp_not_selected": 409,
    "mcp_binary_output_omitted": 422,
    "mcp_output_too_large": 413,
    "mcp_result_unknown": 502,
    "mcp_transaction_failed": 503,
    "mcp_tool_catalog_invalid": 422,
    "mcp_connection_retiring": 409,
}

_MCP_ERROR_MESSAGES = {
    "mcp_config_invalid": "The MCP configuration is invalid.",
    "mcp_config_blocked": "The MCP configuration is blocked by a safety check.",
    "mcp_secret_in_arguments": "The MCP command arguments contain a secret.",
    "mcp_url_credentials": "The MCP URL contains credentials.",
    "mcp_secret_in_url": "The MCP URL contains a secret.",
    "mcp_config_review_required": "The MCP configuration must be reviewed first.",
    "mcp_name_conflict": "An MCP server with this name already exists.",
    "mcp_revision_conflict": "The MCP configuration changed. Refresh and try again.",
    "mcp_test_receipt_invalid": "Test the current MCP configuration again before saving.",
    "mcp_test_receipt_expired": "The MCP test expired. Test it again before saving.",
    "mcp_keychain_unavailable": "The local credential store is unavailable.",
    "mcp_credentials_missing": "This MCP server needs credentials.",
    "mcp_connect_timeout": "The MCP server did not connect in time.",
    "mcp_connection_failed": "The MCP server connection failed.",
    "mcp_schema_unsupported": "This MCP tool schema is not supported.",
    "mcp_arguments_invalid": "The MCP tool arguments are invalid.",
    "mcp_tool_changed": "This MCP tool changed. Review it before using it.",
    "mcp_tool_disabled": "This MCP tool is disabled.",
    "mcp_not_selected": "This MCP server is not selected for the current conversation.",
    "mcp_binary_output_omitted": "Binary MCP output was omitted.",
    "mcp_output_too_large": "The MCP result is too large.",
    "mcp_result_unknown": "The MCP connection stopped after sending the request; the result is unknown.",
    "mcp_transaction_failed": "The MCP change could not be saved. Try again.",
    "mcp_tool_catalog_invalid": "The MCP tool catalog is invalid.",
    "mcp_connection_retiring": "The previous MCP connection is stopping. Try again.",
}

_MCP_RETRYABLE = frozenset(
    {
        "mcp_keychain_unavailable",
        "mcp_connect_timeout",
        "mcp_connection_failed",
        "mcp_transaction_failed",
        "mcp_connection_retiring",
    }
)


def _is_mcp_path(path: str) -> bool:
    segments = path.split("/")
    return path == "/v1/mcp" or path.startswith("/v1/mcp/") or (
        len(segments) >= 5
        and segments[1:3] == ["v1", "threads"]
        and segments[4] == "mcp"
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(CaseCalendarError)
    async def case_calendar_error_handler(request: Request, error: CaseCalendarError):
        return JSONResponse(status_code=error.http_status, content={"error": {
            "code": error.code, "message": error.message, "retryable": error.retryable,
        }})

    @app.exception_handler(ScheduleDomainError)
    async def schedule_error_handler(request, error):
        return JSONResponse(status_code=error.http_status, content={"error": {"code": error.code, "message": error.message, "retryable": error.retryable}})

    @app.exception_handler(AttachmentDomainError)
    async def attachment_domain_error(
        request: Request,
        error: AttachmentDomainError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.http_status,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request_schema(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        if request.url.path.startswith("/v1/case-calendar/"):
            return JSONResponse(status_code=422, content={"error": {
                "code": "calendar_invalid",
                "message": calendar_validation_message(error.errors()),
                "retryable": False,
            }})
        is_mcp = _is_mcp_path(request.url.path)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": (
                        "mcp_config_invalid"
                        if is_mcp
                        else "invalid_provider_request"
                    ),
                    "message": (
                        _MCP_ERROR_MESSAGES["mcp_config_invalid"]
                        if is_mcp
                        else "Provider request is invalid."
                    ),
                    "retryable": False,
                }
            },
        )

    @app.exception_handler(MCPDomainError)
    async def mcp_domain_error(
        request: Request,
        error: MCPDomainError,
    ) -> JSONResponse:
        del request
        if error.code not in _MCP_ERROR_STATUS:
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "The Runtime could not complete the request.",
                        "retryable": False,
                    }
                },
            )
        return JSONResponse(
            status_code=_MCP_ERROR_STATUS.get(error.code, 400),
            content={
                "error": {
                    "code": error.code,
                    "message": _MCP_ERROR_MESSAGES.get(
                        error.code,
                        "The MCP request could not be completed.",
                    ),
                    "retryable": error.code in _MCP_RETRYABLE,
                }
            },
        )

    @app.exception_handler(ProviderDomainError)
    async def provider_domain_error(
        request: Request,
        error: ProviderDomainError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.http_status,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                }
            },
        )

    @app.exception_handler(SessionDomainError)
    async def session_domain_error(
        request: Request,
        error: SessionDomainError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.http_status,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                }
            },
        )

    @app.exception_handler(PreferenceDomainError)
    async def preference_domain_error(
        request: Request,
        error: PreferenceDomainError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.http_status,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                }
            },
        )

    @app.exception_handler(SkillServiceError)
    async def skill_domain_error(
        request: Request,
        error: SkillServiceError,
    ) -> JSONResponse:
        del request
        # The fixed Chinese copy travels with the domain error itself.
        return JSONResponse(
            status_code=_SKILL_ERROR_STATUS.get(error.code, 400),
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.code in _SKILL_ERROR_RETRYABLE,
                }
            },
        )

    @app.exception_handler(ToolError)
    async def tool_domain_error(
        request: Request,
        error: ToolError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=_TOOL_ERROR_STATUS.get(error.code, 400),
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": False,
                }
            },
        )

    @app.exception_handler(ContractReviewError)
    async def contract_review_error(
        request: Request,
        error: ContractReviewError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=_CONTRACT_ERROR_STATUS.get(error.code, 400),
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": False,
                }
            },
        )

    @app.exception_handler(ValueError)
    async def invalid_provider_request(
        request: Request,
        error: ValueError,
    ) -> JSONResponse:
        del request, error
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_provider_request",
                    "message": "Provider request is invalid.",
                    "retryable": False,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_provider_error(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        del error
        logger.error(
            "Unexpected Runtime request failure for %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "The Runtime could not complete the request.",
                    "retryable": False,
                }
            },
        )
