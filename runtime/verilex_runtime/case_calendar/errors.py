class CaseCalendarError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = False


def not_found() -> CaseCalendarError:
    return CaseCalendarError("calendar_event_not_found", "这条日程已不存在，请刷新列表。", 404)


def conflict() -> CaseCalendarError:
    return CaseCalendarError("calendar_revision_conflict", "这条日程已更新，请重新打开后再操作。", 409)


_VALIDATION_MESSAGES = {
    "calendar_date": "请填写有效日期（年-月-日）。",
    "calendar_time": "请填写有效时间（小时:分钟），或留空表示全天。",
    "calendar_timezone": "日程时区无效，请选择有效的地区时区。",
    "calendar_time_gap": "所选时间因夏令时调整而不存在，请改选其他时间。",
    "calendar_time_range": "该日期无法换算提醒时间，请选择其他日期。",
    "calendar_title": "请填写日程标题。",
    "calendar_reminder": "请选择列表中的提醒时间，或不提醒。",
}


def validation_message(errors: list[dict]) -> str:
    """Return fixed Chinese messages without echoing untrusted submitted content."""
    for error in errors:
        if error.get("type") in _VALIDATION_MESSAGES:
            return _VALIDATION_MESSAGES[error["type"]]
    labels = {
        "title": "标题应为1至120字。",
        "kind": "请选择有效的日程类型。",
        "customKind": "自定义类型名称不能超过40字。",
        "date": _VALIDATION_MESSAGES["calendar_date"],
        "time": _VALIDATION_MESSAGES["calendar_time"],
        "timeZone": _VALIDATION_MESSAGES["calendar_timezone"],
        "caseName": "案件名称不能超过200字。",
        "location": "地点不能超过300字。",
        "notes": "备注不能超过5000字。",
        "reminderMinutes": _VALIDATION_MESSAGES["calendar_reminder"],
        "revision": "日程版本无效，请重新打开后再操作。",
    }
    for error in errors:
        for field in error.get("loc", ()):
            if field in labels:
                return labels[field]
    return "日程信息格式有误，请检查后重试。"
