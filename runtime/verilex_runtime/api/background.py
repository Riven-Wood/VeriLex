"""Authenticated REST surface for background tasks of a thread."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.tools.background import BackgroundTaskManager
from verilex_runtime.tools.registry import ToolError


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


TaskStatus = Literal[
    "running", "completed", "failed", "stopped", "expired", "interrupted"
]


class BackgroundTaskListItem(_Strict):
    id: str
    toolCallId: str
    command: str
    description: str
    status: TaskStatus
    exitCode: int | None
    omittedBytes: int
    startedAt: str
    finishedAt: str | None
    createdAt: str


class BackgroundTaskDetail(BackgroundTaskListItem):
    outputText: str


class BackgroundTaskOutputResponse(_Strict):
    cursor: int
    text: str
    omittedBytes: int
    status: TaskStatus


class BackgroundTaskStopResponse(_Strict):
    taskId: str
    status: TaskStatus


def _list_item(record: dict) -> BackgroundTaskListItem:
    return BackgroundTaskListItem(
        id=record["id"],
        toolCallId=record["tool_call_id"],
        command=record["command"],
        description=record["description"],
        status=record["status"],
        exitCode=record["exit_code"],
        omittedBytes=record["omitted_bytes"],
        startedAt=record["started_at"],
        finishedAt=record["finished_at"],
        createdAt=record["created_at"],
    )


def build_background_tasks_router(
    threads: ThreadService,
    manager: BackgroundTaskManager,
) -> APIRouter:
    router = APIRouter(prefix="/v1/threads", tags=["background-tasks"])

    @router.get(
        "/{thread_id}/background-tasks",
        response_model=list[BackgroundTaskListItem],
    )
    async def list_background_tasks(thread_id: str) -> list[BackgroundTaskListItem]:
        threads.get(thread_id)
        return [
            _list_item(record) for record in manager.list_for_thread(thread_id)
        ]

    @router.get(
        "/{thread_id}/background-tasks/{task_id}",
        response_model=BackgroundTaskDetail,
    )
    async def get_background_task(
        thread_id: str, task_id: str
    ) -> BackgroundTaskDetail:
        threads.get(thread_id)
        record = manager.get(thread_id, task_id)
        if record is None:
            raise ToolError(
                "background_task_not_found",
                "Background task was not found for this conversation.",
            )
        detail = _list_item(record)
        return BackgroundTaskDetail(
            **detail.model_dump(), outputText=record["output_text"]
        )

    @router.get(
        "/{thread_id}/background-tasks/{task_id}/output",
        response_model=BackgroundTaskOutputResponse,
    )
    async def get_background_task_output(
        thread_id: str, task_id: str, after: int = 0
    ) -> BackgroundTaskOutputResponse:
        threads.get(thread_id)
        data = await manager.read(thread_id, task_id, after=after)
        return BackgroundTaskOutputResponse(
            cursor=data["cursor"],
            text=data["text"],
            omittedBytes=data["omitted_bytes"],
            status=data["status"],
        )

    @router.post(
        "/{thread_id}/background-tasks/{task_id}/stop",
        response_model=BackgroundTaskStopResponse,
    )
    async def stop_background_task(
        thread_id: str, task_id: str
    ) -> BackgroundTaskStopResponse:
        threads.get(thread_id)
        result = await manager.stop(thread_id, task_id)
        return BackgroundTaskStopResponse(
            taskId=result["task_id"], status=result["status"]
        )

    return router
