class RedactionError(Exception):
    def __init__(self, code='redaction_invalid'):
        self.code = code
        super().__init__(code)


def import_error(code):
    """Translate trusted categories only; never expose converter exception text."""
    return RedactionError({
        'attachment_doc_converter_missing': 'redaction_converter_missing',
        'attachment_doc_conversion_failed': 'redaction_conversion_failed',
        'attachment_media_type_mismatch': 'redaction_format_mismatch',
        'attachment_docx_encrypted': 'redaction_encrypted',
        'attachment_docx_macro': 'redaction_unsafe',
        'attachment_docx_unsafe': 'redaction_unsafe',
        'attachment_too_large': 'redaction_limit',
    }.get(code, 'redaction_invalid'))

MESSAGES = {
    'redaction_invalid': '文件或处理记录无法读取，请重新选择有效的 Word 文件。',
    'redaction_converter_missing': '导入旧版 .doc 需要本机安装 LibreOffice；也可在 Word/WPS 中另存为 .docx 后重试。',
    'redaction_conversion_failed': '本机转换组件未能完成旧版 Word 转换。请在 Word/WPS 中另存为 .docx 后重试，原件未改动。',
    'redaction_format_mismatch': '文件内容与 .doc 格式不符。请在 Word/WPS 中打开并另存为 .docx 后重试；只修改文件名后缀不能转换格式。',
    'redaction_encrypted': '这份 Word 文件已加密，请在 Word/WPS 中解密并另存后重试。',
    'redaction_unsafe': '文件含宏、可执行内容或内部结构异常，已停止处理。请在 Word/WPS 中整理并另存为标准 .docx 后重试。',
    'redaction_unsupported': '支持 .doc、.docx 和 .md，暂不支持 PDF 或扫描件。',
    'redaction_markdown_encoding': 'Markdown 文件不是有效的 UTF-8 文本，请转换为 UTF-8 后重试。',
    'redaction_markdown_format': 'Markdown 文件只能导出为 Markdown。',
    'redaction_complex': '文件含暂不能安全处理的域、图表或嵌入对象等内容，请先在 Word/WPS 中整理后重试。',
    'redaction_limit': '文件过大或内容过多，请拆分后重试（单份 20 MB、20 万字、5000 处）。',
    'redaction_model': '本地中文识别模型无法加载，请重新安装应用后重试。',
    'redaction_residue': '自检发现未处理或尚未复核的文字，未生成文件。请补充自定义词或调整文件后重试。',
    'redaction_format_changed': '无法确认脱敏修订版保留了原有格式，未生成文件。请在 Word/WPS 中另存为标准 .docx 后重试。',
    'redaction_markdown_structure': '这次替换会改变 Markdown 的标题、链接或其他排版结构，未生成文件。请调整替换内容后重试。',
    'restore_unavailable': '对照表缺失或损坏，已无法还原或重新处理。',
    'redaction_conflict': '任务已发生变化，请重新打开后操作。',
    'redaction_not_found': '处理记录不存在。',
    'redaction_busy': '已有文件正在处理，请稍后再试。',
}
