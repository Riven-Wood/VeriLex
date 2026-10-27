"""Authenticated REST surface for background tasks."""

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.models import (
    AuthMode,
    ProviderKind,
    ProviderRecord,
)
from verilex_runtime.storage.preferences import PreferenceRepository
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)
from verilex_runtime.tools import ToolContext
from verilex_runtime.tools.background import BackgroundTaskManager

TOKEN = "background-api-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


@pytest.fixture
def api(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    providers_repo = ProviderRepository(database)
    providers_repo.create(
        ProviderRecord(
            id="provider-1",
            name="Compat",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            default_model="model-1",
            auth_mode=AuthMode.NONE,
            secret_ref=None,
            enabled=True,
            created_at="2026-08-25T00:00:00Z",
            updated_at="2026-08-25T00:00:00Z",
        )
    )
    ids = iter(f"id-{n}" for n in range(1, 100))
    threads_service = ThreadService(
        ThreadRepository(database),
        providers_repo,
        turns=TurnRepository(database),
        workspaces=None,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-25T00:00:00Z",
    )
    turn_service = TurnService(
        ThreadRepository(database),
        TurnRepository(database),
        MessageRepository(database),
        EventRepository(database),
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-25T00:00:00Z",
    )
    thread = threads_service.create("Case", "model-1", provider_id="provider-1")

    with database.connect() as connection:
        connection.execute(
            "INSERT INTO turns (id, thread_id, status, error_code, started_at, finished_at, created_at) VALUES (?, ?, ?, NULL, ?, ?, ?)",
            (
                "turn-1",
                thread.id,
                "completed",
                "2026-08-25T00:00:00Z",
                "2026-08-25T00:00:01Z",
                "2026-08-25T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO tool_calls (id, turn_id, provider_call_id, name, arguments_json, risk_level, status, result_json, started_at, finished_at) VALUES (?, ?, NULL, ?, ?, ?, ?, NULL, ?, NULL)",
            (
                "tc-1",
                "turn-1",
                "run_background_command",
                "{}",
                "high",
                "succeeded",
                "2026-08-25T00:00:01Z",
            ),
        )

    preferences = PreferenceRepository(database)
    manager = BackgroundTaskManager(database, preferences, flush_interval=0.05)
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token=TOKEN,
        ),
        RuntimeServices(
            provider_service=object(),
            threads_service=threads_service,
            turn_service=turn_service,
            background_manager=manager,
        ),
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return {
        "app": app,
        "manager": manager,
        "threads_service": threads_service,
        "thread_id": thread.id,
        "workspace": workspace,
    }


@pytest_asyncio.fixture
async def client(api):
    transport = ASGITransport(app=api["app"])
    async with AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1",
        headers=HEADERS,
    ) as async_client:
        yield async_client


def _context(api):
    return ToolContext(
        root_path=api["workspace"],
        allow_read=True,
        allow_write=True,
        thread_id=api["thread_id"],
        turn_id="turn-1",
        tool_call_id="tc-1",
    )


async def _start(api, command, description="job"):
    return await api["manager"].start(
        context=_context(api), command=command, description=description
    )


async def _wait_status(client, api, task_id, timeout=15.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        response = await client.get(
            f"/v1/threads/{api['thread_id']}/background-tasks/{task_id}"
        )
        if response.status_code == 200 and response.json()["status"] != "running":
            return response.json()
        await asyncio.sleep(0.05)
    raise AssertionError("task did not leave running state")


@pytest.mark.asyncio
async def test_list_requires_token_and_sets_no_store(api):
    transport = ASGITransport(app=api["app"])
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1") as anon:
        response = await anon.get(f"/v1/threads/{api['thread_id']}/background-tasks")

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_list_returns_only_tasks_of_that_thread(api, client):
    task = await _start(api, "echo listed")
    await _wait_status(client, api, task["task_id"])

    response = await client.get(
        f"/v1/threads/{api['thread_id']}/background-tasks"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [item["id"] for item in body] == [task["task_id"]]
    entry = body[0]
    assert entry["command"] == "echo listed"
    assert entry["status"] == "completed"
    assert "outputText" not in entry
    assert entry["toolCallId"] == "tc-1"


@pytest.mark.asyncio
async def test_list_rejects_unknown_thread(api, client):
    response = await client.get("/v1/threads/no-such-thread/background-tasks")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "thread_not_found"


@pytest.mark.asyncio
async def test_detail_returns_task_with_bounded_output(api, client):
    task = await _start(api, "echo detail-marker")
    await _wait_status(client, api, task["task_id"])

    response = await client.get(
        f"/v1/threads/{api['thread_id']}/background-tasks/{task['task_id']}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == task["task_id"]
    assert body["omittedBytes"] == 0
    assert "detail-marker" in body["outputText"]
    assert len(body["outputText"]) <= 256 * 1024 + 200


@pytest.mark.asyncio
async def test_output_uses_cursor_semantics(api, client):
    task = await _start(api, "echo cursor-marker")
    await _wait_status(client, api, task["task_id"])

    first = await client.get(
        f"/v1/threads/{api['thread_id']}/background-tasks/{task['task_id']}/output"
    )
    assert first.status_code == 200
    assert "cursor-marker" in first.json()["text"]
    assert len(first.json()["text"].encode("utf-8")) <= 64 * 1024
    cursor = first.json()["cursor"]

    second = await client.get(
        f"/v1/threads/{api['thread_id']}/background-tasks/{task['task_id']}/output",
        params={"after": cursor},
    )
    assert second.status_code == 200
    assert second.json()["text"] == ""
    assert second.json()["cursor"] == cursor


@pytest.mark.asyncio
async def test_stop_terminates_running_task_and_rejects_terminal(api, client):
    holder = api["workspace"] / "hold.py"
    holder.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    task = await _start(api, "python3 hold.py", description="hold")

    response = await client.post(
        f"/v1/threads/{api['thread_id']}/background-tasks/{task['task_id']}/stop"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"

    again = await client.post(
        f"/v1/threads/{api['thread_id']}/background-tasks/{task['task_id']}/stop"
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "background_task_not_running"


@pytest.mark.asyncio
async def test_unknown_task_and_cross_thread_are_not_found(api, client):
    missing = await client.get(
        f"/v1/threads/{api['thread_id']}/background-tasks/no-such-task"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "background_task_not_found"

    holder = api["workspace"] / "hold2.py"
    holder.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    task = await _start(api, "python3 hold2.py", description="hold")
    other_thread = api["threads_service"].create(
        "Other", "model-1", provider_id="provider-1"
    )
    foreign = await client.get(
        f"/v1/threads/{other_thread.id}/background-tasks/{task['task_id']}"
    )
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "background_task_not_found"

    stop = await client.post(
        f"/v1/threads/{api['thread_id']}/background-tasks/{task['task_id']}/stop"
    )
    assert stop.status_code == 200


@pytest.mark.asyncio
async def test_responses_do_not_leak_environment_secrets(api, client, monkeypatch):
    monkeypatch.setenv("VERILEX_API_TOKEN", "SECRET-TOKEN-XYZ")
    task = await _start(api, "echo safe-output")
    await _wait_status(client, api, task["task_id"])

    detail = await client.get(
        f"/v1/threads/{api['thread_id']}/background-tasks/{task['task_id']}"
    )
    output = await client.get(
        f"/v1/threads/{api['thread_id']}/background-tasks/{task['task_id']}/output"
    )

    assert "SECRET-TOKEN-XYZ" not in detail.text
    assert "SECRET-TOKEN-XYZ" not in output.text
    assert "safe-output" in output.json()["text"]
