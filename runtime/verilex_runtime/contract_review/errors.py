"""合同审查固定错误白名单与中文文案；不回显合同内容、当事人名称或堆栈。"""
from __future__ import annotations

_MESSAGES = {
    "contract_storage_busy": "本机数据正在忙，请稍后重新打开合同；原合同没有被改动。",
    "contract_requirements_invalid": "审查要求格式不正确，请输入不超过4000字的文字。",
    "contract_markup_changed": "当前文档已在本次审查后改动，未写入修订和批注。请按当前文档重新审查。",
    "contract_editor_conflict": "正文已有更新，未应用这次修改；请重新读取并核对后重试。",
    "contract_editor_readonly": "此段包含暂不支持编辑的内容，原内容已保留；请在原件预览中核对。",
    "contract_editor_invalid": "本次编辑无法保存；请只修改同一段内的文字，不要插入换行或制表符。",
    "contract_editor_too_large": "文档或修改超过编辑上限，请缩小修改范围。",
    "contract_editor_selection": "选中文字已变化或不属于当前合同，请重新选择后发送。",
    "contract_editor_proposal_closed": "这条修改建议已处理或所属请求已经结束，请重新提出修改要求。",
    "contract_editor_proposal_not_found": "没有找到这条合同修改建议。",
    "contract_editor_integrity": "修改副本未通过内容保留检查，已停止保存；原合同没有被改动。",
    "contract_report_failed": "审查意见书生成或检查没有成功，请重试；原合同没有被改动。",
    "contract_file_unsupported": (
        "这份合同文件暂时无法审查：目前支持普通 Word（.doc / .docx）文件，"
        "加密、损坏或扫描件请先用 Word/WPS 另存为可读取的文件。"
    ),
    "contract_annotation_write_failed": (
        "批注写入没有成功，导出已停止；原件没有被改动。"
    ),
    "contract_integrity_failed": (
        "批注版一致性校验没有通过，为安全起见已停止导出；原件没有被改动。"
    ),
    "contract_export_blocked": "导出功能尚未就绪，暂时无法生成批注版。",
    "contract_thread_busy": "已有一份合同正在审查，一次只能审一份；请先等它完成或停止它。",
    "contract_not_found": "没有找到这条审查记录。",
    "contract_opinion_not_found": "没有找到这条审查意见。",
}


class ContractReviewError(Exception):
    def __init__(self, code: str, detail: str | None = None):
        if code not in _MESSAGES:
            raise ValueError(code)
        self.code = code
        self.message = _MESSAGES[code]
        # Optional internal context; never shown to users in place of the fixed copy.
        self.detail = detail
        super().__init__(code)
