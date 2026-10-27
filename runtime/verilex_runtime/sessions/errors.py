class SessionDomainError(Exception):
    def __init__(self, code: str, message: str, retryable: bool, http_status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status


_ERRORS = {
    "edit_source_unavailable": ("这条指令已被修改，请重新打开会话后再试。", False, 409),
    "thread_not_found": ("Thread was not found.", False, 404),
    "turn_not_found": ("Turn was not found.", False, 404),
    "provider_not_found": ("Provider was not found.", False, 404),
    "provider_disabled": ("Provider is disabled.", False, 409),
    "reasoning_effort_unsupported": (
        "The selected model does not support that reasoning effort.",
        False,
        422,
    ),
    "thread_already_archived": ("Thread is already archived.", False, 409),
    "thread_not_archived": ("Thread is not archived.", False, 409),
    "thread_already_trashed": ("Thread is already in the trash.", False, 409),
    "thread_not_trashed": ("Thread is not in the trash.", False, 409),
    "thread_not_draft": ("Thread is not a hidden draft.", False, 409),
    "thread_draft_not_empty": (
        "Hidden draft already contains conversation content.",
        False,
        409,
    ),
    "thread_turn_active": (
        "Thread has an active turn; wait for it to finish or stop it first.",
        False,
        409,
    ),
    "thread_context_changed": (
        "Thread model settings changed; refresh and try again.",
        False,
        409,
    ),
    "turn_conflict": (
        "Thread already has an active turn.",
        False,
        409,
    ),
    "invalid_turn_transition": ("Turn cannot move to that status.", False, 409),
    "turn_limit_reached": (
        "The Runtime already has the maximum number of active turns.",
        True,
        429,
    ),
    "thread_has_no_provider": (
        "The thread has no provider configured.",
        False,
        409,
    ),
    "tools_unavailable": (
        "The model requested tools, but tools are not available yet.",
        False,
        422,
    ),
    "turn_limit_exceeded": (
        "The turn exceeded its resource limit.",
        True,
        429,
    ),
    "workspace_busy": ("Stop tasks using this workspace before changing its permissions.", True, 409),
    "workspace_not_found": ("Workspace was not found.", False, 404),
    "workspace_root_path_exists": (
        "A workspace already exists for that directory.",
        False,
        409,
    ),
    "workspace_root_path_invalid": (
        "Workspace root path must be an absolute directory.",
        False,
        422,
    ),
    "workspace_read_required": (
        "Workspace must keep read access enabled.",
        False,
        422,
    ),
    "approval_not_pending": (
        "Approval is not pending.",
        False,
        409,
    ),
    "approval_wait_expired": (
        "The operation was not executed because the approval wait expired.",
        False,
        409,
    ),
    "invalid_approval_decision": (
        "Approval decision must be allowed or denied.",
        False,
        422,
    ),
    "invalid_approval_mode": (
        "Approval mode must be ask, help, or full.",
        False,
        422,
    ),
    "thread_scene_invalid": (
        "Thread scene must be general or legal-retrieval.",
        False,
        422,
    ),
}


def session_error(code: str) -> SessionDomainError:
    message, retryable, http_status = _ERRORS[code]
    return SessionDomainError(code, message, retryable, http_status)
