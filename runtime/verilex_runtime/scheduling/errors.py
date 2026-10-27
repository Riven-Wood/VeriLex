class ScheduleDomainError(Exception):
    def __init__(self, code, message, http_status=422):
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status
        self.retryable = False


def invalid(message="请检查任务指令和运行时间。"):
    return ScheduleDomainError("schedule_invalid", message)
