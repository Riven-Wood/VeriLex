class ProviderDomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool,
        http_status: int,
        *,
        vision_rejected: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status
        self.vision_rejected = vision_rejected
        self.output_started = False


_ERRORS = {
    "provider_context_exceeded": ("The model context window was exceeded.", False, 422),
    "invalid_provider_url": (
        "Provider URL is invalid.",
        False,
        422,
    ),
    "insecure_remote_url": (
        "Remote Provider URLs must use HTTPS.",
        False,
        422,
    ),
    "secret_store_unavailable": (
        "The local secret store is unavailable.",
        True,
        503,
    ),
    "secret_store_write_failed": (
        "The local secret store could not save or delete the credential.",
        True,
        503,
    ),
    "provider_unreachable": (
        "Provider could not be reached.",
        True,
        502,
    ),
    "provider_stream_interrupted": (
        "The model service connection was interrupted mid-response.",
        True,
        502,
    ),
    "provider_timeout": (
        "Provider connection timed out.",
        True,
        504,
    ),
    "provider_auth_failed": (
        "Provider rejected the configured credentials.",
        False,
        401,
    ),
    "provider_rate_limited": (
        "Provider rate limit was exceeded.",
        True,
        429,
    ),
    "provider_model_not_found": (
        "The configured model was not found.",
        False,
        404,
    ),
    "provider_model_invalid": (
        "The model name is invalid.",
        False,
        422,
    ),
    "provider_protocol_error": (
        "Provider returned an incompatible response.",
        False,
        502,
    ),
    "provider_response_too_large": (
        "The model response exceeded the safe size limit. Narrow the task and retry.",
        False,
        502,
    ),
    "provider_reasoning_effort_unsupported": (
        "The selected model does not support this reasoning effort.",
        False,
        422,
    ),
    "provider_vision_unsupported": (
        "The selected model does not support image input.",
        False,
        422,
    ),
    "provider_has_no_models": (
        "Provider has no available models.",
        False,
        409,
    ),
    "provider_not_found": (
        "Provider was not found.",
        False,
        404,
    ),
    "provider_in_use": (
        "Provider has an active turn. Finish the turn before deleting the connection.",
        False,
        409,
    ),
    "provider_busy": (
        "Provider operations are busy. Try again shortly.",
        True,
        503,
    ),
}


def provider_error(code: str) -> ProviderDomainError:
    message, retryable, http_status = _ERRORS[code]
    return ProviderDomainError(code, message, retryable, http_status)
