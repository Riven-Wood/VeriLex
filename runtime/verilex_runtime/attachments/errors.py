class AttachmentDomainError(Exception):
    def __init__(self, code: str, message: str, retryable: bool, http_status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status


_ERRORS: dict[str, tuple[str, bool, int]] = {
    "attachment_doc_converter_missing": ("导入旧版 .doc 需要本机安装 LibreOffice；也可在 Word/WPS 中另存为 .docx 后导入。", False, 422),
    "attachment_doc_conversion_failed": ("本机转换组件未能完成旧版 Word 转换。请在 Word/WPS 中另存为 .docx 后重试，原件未改动。", False, 422),
    "attachment_type_unsupported": ("Attachment type is not supported.", False, 415),
    "attachment_media_type_mismatch": (
        "Attachment type does not match its content.",
        False,
        422,
    ),
    "attachment_too_large": ("Attachment is too large.", False, 413),
    "attachment_count_exceeded": ("Too many attachments were selected.", False, 422),
    "attachment_total_size_exceeded": ("Attachment total size is too large.", False, 413),
    "attachment_invalid_utf8": ("Text attachment is not valid UTF-8.", False, 422),
    "attachment_pdf_invalid": ("PDF attachment is invalid.", False, 422),
    "attachment_pdf_encrypted": (
        "Encrypted PDF attachments are not supported.",
        False,
        422,
    ),
    "attachment_pdf_no_text": ("PDF attachment has no extractable text.", False, 422),
    "attachment_pdf_page_limit_exceeded": (
        "PDF attachment has too many pages.",
        False,
        413,
    ),
    "attachment_docx_unsupported": (
        "目前只支持标准 .docx，请先另存为该格式。",
        False,
        415,
    ),
    "attachment_docx_invalid": (
        "这份 Word 文件损坏或结构不完整，无法读取。",
        False,
        422,
    ),
    "attachment_docx_encrypted": (
        "这份 Word 文件已加密，请解密后重试。",
        False,
        422,
    ),
    "attachment_docx_macro": (
        "为安全起见，首发不处理含宏或可执行内容的 Word 文件。",
        False,
        422,
    ),
    "attachment_docx_unsafe": (
        "文件内部压缩包或关系异常，已停止处理。",
        False,
        422,
    ),
    "attachment_docx_no_text": (
        "没有读到可供分析的正文或表格文字。",
        False,
        422,
    ),
    "attachment_text_budget_exceeded": (
        "Attachment text exceeds the turn limit.",
        False,
        413,
    ),
    "attachment_upload_unavailable": ("Attachment upload is unavailable.", False, 409),
    "attachment_blob_missing": ("Attachment content is unavailable.", False, 409),
    "model_vision_unsupported": (
        "The selected model does not support image attachments.",
        False,
        422,
    ),
}


def attachment_error(code: str) -> AttachmentDomainError:
    try:
        message, retryable, http_status = _ERRORS[code]
    except KeyError:
        raise ValueError("Unknown attachment error code.") from None
    return AttachmentDomainError(code, message, retryable, http_status)
