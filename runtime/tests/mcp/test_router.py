from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from verilex_runtime.app import create_app
from verilex_runtime.api.errors import install_error_handlers
from verilex_runtime.container import RuntimeServices
from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.models import (
    MCPDiscoveredTool,
    MCPSecretRef,
    MCPServerRecord,
    MCPRuntimeState,
    MCPToolRecord,
)
from verilex_runtime.mcp.service import MCPTestResult
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.models import ReasoningEffort, ThreadRecord, ThreadStatus
from verilex_runtime.tools import (
    ToolCategory,
    ToolDefinition,
    ToolRegistry,
    ToolScope,
    ToolSource,
)
from verilex_runtime.storage.models import RiskLevel


TOKEN = "mcp-router-token"
HEADERS = {"X-VeriLex-Token": TOKEN}
NOW = "2026-08-29T00:00:00Z"
DESCRIPTION_HASH = "40550dee8c8acedc29f3b807db4f36156082ddeada65388d5819c3b4cb9cc747"
SCHEMA_HASH = "a2c799262a3ce3c19ef5cdd983bf3d12b43ab3c426227091b909dcb7054738c0"


def _server(**changes) -> MCPServerRecord:
    values = dict(
        id="server-1",
        display_name="Case Reader",
        transport="stdio",
        command="case-reader",
        args=("--stdio",),
        cwd=None,
        url=None,
        env={"LOG_LEVEL": "info"},
        headers={"X-Tenant": "legal"},
        env_refs={"TOKEN": MCPSecretRef("secret://token")},
        header_refs={"Authorization": MCPSecretRef("secret://auth")},
        connect_timeout_ms=10_000,
        request_timeout_ms=30_000,
        config_revision=1,
        config_fingerprint="fingerprint",
        secret_revision=2,
        created_at=NOW,
        updated_at=NOW,
        config_review_state="reviewed",
        risk_findings=(),
    )
    values.update(changes)
    return MCPServerRecord(**values)


def _tool(**changes) -> MCPToolRecord:
    values = dict(
        server_id="server-1",
        tool_name="read_case",
        description="Read a case",
        description_hash=DESCRIPTION_HASH,
        input_schema={"type": "object"},
        schema_hash=SCHEMA_HASH,
        enabled=True,
        review_state="known",
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    values.update(changes)
    return MCPToolRecord(**values)


def _thread() -> ThreadRecord:
    return ThreadRecord(
        id="thread-1",
        title="New task",
        provider_id="provider-1",
        model="model-1",
        workspace_id=None,
        status=ThreadStatus.IDLE,
        created_at=NOW,
        updated_at=NOW,
        is_draft=True,
        reasoning_effort=ReasoningEffort.AUTO,
    )


async def _execute(_context, _arguments):
    raise AssertionError("read-only catalog must not execute tools")


class FakeRepository:
    def __init__(self) -> None:
        self.server = _server()
        self.tool = _tool()

    def list_servers(self):
        return [self.server]

    def selected_thread_counts(self):
        return {self.server.id: 2}

    def get_server(self, server_id):
        return self.server if server_id == self.server.id else None

    def list_tools(self, server_id):
        return [self.tool] if server_id == self.server.id else []

    def selected_server_ids(self, thread_id):
        return [self.server.id] if thread_id == "thread-1" else []


class FakeService:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.calls: list[tuple[str, object]] = []
        self.fail_activation = False
        self.fail_select_after_commit = False
        self.ready_servers: set[str] = set()
        self.recent_tests: dict[str, tuple[str, str]] = {}

    async def test_draft(self, draft, secrets, accepted_risk_codes):
        self.calls.append(("test_draft", (draft, secrets, accepted_risk_codes)))
        return MCPTestResult(
            "receipt-1",
            (MCPDiscoveredTool("read_case", "Read a case", {"type": "object"}),),
            (),
        )

    async def test_server_patch(
        self,
        server_id,
        patch,
        secrets,
        *,
        expected_revision,
        accepted_risk_codes,
    ):
        self.calls.append(("test_server_patch", server_id))
        result = await self.test_draft(None, secrets, accepted_risk_codes)
        self.recent_tests[server_id] = ("passed", NOW)
        return result

    async def refresh_server_tools(self, server_id, *, expected_revision):
        self.calls.append(("refresh_server_tools", (server_id, expected_revision)))

    async def create_server(
        self,
        draft,
        secrets,
        receipt,
        decisions,
        *,
        accepted_risk_codes,
    ):
        self.calls.append(("create_server", receipt))
        return self.repository.server

    async def update_server(
        self,
        server_id,
        patch,
        *,
        expected_revision,
        receipt,
        decisions,
        secrets,
        accepted_risk_codes,
    ):
        self.calls.append(("update_server", (server_id, expected_revision)))
        self.repository.server = replace(
            self.repository.server,
            display_name=patch.get("display_name", self.repository.server.display_name),
            config_revision=expected_revision + 1,
        )
        return self.repository.server

    async def delete_server(self, server_id):
        self.calls.append(("delete_server", server_id))
        if server_id != self.repository.server.id:
            return SimpleNamespace(deleted=False, selected_thread_count=0)
        self.recent_tests.pop(server_id, None)
        return SimpleNamespace(deleted=True, selected_thread_count=2)

    async def review_tool(
        self,
        server_id,
        tool_name,
        *,
        enabled,
        expected_description_hash,
        expected_schema_hash,
    ):
        self.calls.append(("review_tool", tool_name))
        current = self.repository.tool
        if (
            server_id != current.server_id
            or tool_name != current.tool_name
            or expected_description_hash != current.description_hash
            or expected_schema_hash != current.schema_hash
        ):
            raise MCPDomainError("mcp_tool_changed", retryable=False)
        self.repository.tool = replace(
            self.repository.tool,
            enabled=enabled,
            review_state="known",
        )
        return self.repository.tool

    async def select(self, thread_id, server_id):
        self.calls.append(("select", (thread_id, server_id)))
        if self.fail_select_after_commit:
            return MCPRuntimeState(
                server_id=server_id,
                status="error",
                last_error_code="mcp_connection_failed",
                retryable=True,
            )
        return self.connection_status(server_id)

    async def deselect(self, thread_id, server_id):
        self.calls.append(("deselect", (thread_id, server_id)))

    async def activate_view(self, view_id, thread_id):
        self.calls.append(("activate_view", (view_id, thread_id)))
        if self.fail_activation:
            raise MCPDomainError("mcp_connection_failed", retryable=True)
        self.ready_servers.add(self.repository.server.id)

    async def deactivate_view(self, view_id):
        self.calls.append(("deactivate_view", view_id))

    async def create_thread_selection(self, request):
        self.calls.append(("create_thread_selection", request))
        return SimpleNamespace(
            thread=_thread(),
            server=self.repository.server,
            selected_at=NOW,
            created_thread=True,
            created_server=False,
        )

    def persisted_definitions_for_thread(self, thread_id):
        self.calls.append(("persisted_definitions_for_thread", thread_id))
        return (
            ToolDefinition(
                name="mcp__case_reader__read_case",
                description="Read a case",
                parameters={"type": "object"},
                risk_level=RiskLevel.LOW,
                execute=_execute,
                source=ToolSource.MCP,
                source_name="Case Reader",
                category=ToolCategory.EXTERNAL,
                scopes=frozenset({ToolScope.EXTERNAL_SERVICE}),
                requires_workspace=False,
            ),
        )

    def connection_status(self, server_id):
        return MCPRuntimeState(
            server_id=server_id,
            status="ready" if server_id in self.ready_servers else "idle",
            tool_count=1,
        )

    def recent_test_status(self, server_id):
        return self.recent_tests.get(server_id, (None, None))

    def selection_is_persisted(self, thread_id, server_id):
        return server_id in self.repository.selected_server_ids(thread_id)

    def thread_connection_status(self, thread_id):
        selected = self.repository.selected_server_ids(thread_id)
        if not selected:
            return MCPRuntimeState(server_id="", status="idle")
        states = [self.connection_status(server_id) for server_id in selected]
        return MCPRuntimeState(
            server_id="",
            status="ready" if all(state.status == "ready" for state in states) else "idle",
            tool_count=sum(state.tool_count for state in states),
        )


class FakeAudit:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, int]] = []

    def record_mcp_server_deleted(self, *, server_id, selected_thread_count):
        self.deleted.append((server_id, selected_thread_count))


class ProviderStub:
    def count_enabled(self):
        return 0


@pytest.fixture
def api(tmp_path):
    repository = FakeRepository()
    service = FakeService(repository)
    audit = FakeAudit()
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token=TOKEN,
        ),
        RuntimeServices(
            provider_service=ProviderStub(),
            mcp_service=service,
            mcp_repository=repository,
            audit_recorder=audit,
            tool_registry=ToolRegistry(),
        ),
    )
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )
    return SimpleNamespace(
        client=client,
        service=service,
        repository=repository,
        audit=audit,
    )


def _draft_payload():
    return {
        "displayName": "Case Reader",
        "transport": "stdio",
        "command": "case-reader",
        "args": ["--stdio"],
        "env": {"LOG_LEVEL": "info"},
        "headers": {},
        "secretEnvKeys": ["TOKEN"],
        "secretHeaderKeys": [],
        "connectTimeoutMs": 10000,
        "requestTimeoutMs": 30000,
    }


def _decision_payload():
    return {
        "toolName": "read_case",
        "enabled": True,
        "expectedDescriptionHash": DESCRIPTION_HASH,
        "expectedSchemaHash": SCHEMA_HASH,
    }


def test_parse_import_is_local_and_rejects_extra_fields_with_mcp_code(api):
    response = api.client.post(
        "/v1/mcp/import/parse",
        headers=HEADERS,
        json={"text": '{"mcpServers":{"law":{"command":"node"}}}'},
    )

    assert response.status_code == 200
    assert response.json()["drafts"][0]["displayName"] == "law"
    assert api.service.calls == []

    invalid = api.client.post(
        "/v1/mcp/import/parse",
        headers=HEADERS,
        json={"text": "node server.js", "unexpected": True},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"] == {
        "code": "mcp_config_invalid",
        "message": "The MCP configuration is invalid.",
        "retryable": False,
    }


def test_parse_import_returns_risks_but_never_secret_values_or_refs(api):
    marker = "PARSE-SECRET-MARKER"
    response = api.client.post(
        "/v1/mcp/import/parse",
        headers=HEADERS,
        json={
            "text": (
                '{"mcpServers":{"law":{"command":"sh","args":["-c",'
                '"curl https://example.com"],"env":{"TOKEN":"'
                + marker
                + '"}}}}'
            )
        },
    )

    assert response.status_code == 200
    draft = response.json()["drafts"][0]
    assert draft["secretEnvKeys"] == ["TOKEN"]
    assert draft["risks"] == []
    assert marker not in response.text
    assert "secret://" not in response.text


@pytest.mark.parametrize(
    ("text", "expected_code"),
    [
        ("https://example.test/mcp?api_key=TOPSECRET", "mcp_secret_in_url"),
        ("https://user:TOPSECRET@example.test/mcp", "mcp_url_credentials"),
        ("npx pkg --token TOPSECRET", "mcp_secret_in_arguments"),
    ],
)
def test_parse_import_blocks_secret_placement_without_echo(api, text, expected_code):
    response = api.client.post(
        "/v1/mcp/import/parse",
        headers=HEADERS,
        json={"text": text},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == expected_code
    assert "TOPSECRET" not in response.text


def test_parse_import_selected_names_do_not_expose_or_block_on_unselected_secret(api):
    response = api.client.post(
        "/v1/mcp/import/parse",
        headers=HEADERS,
        json={
            "text": (
                '{"mcpServers":{"safe":{"command":"node"},'
                '"blocked":{"command":"npx","args":["pkg","--token","TOPSECRET"]}}}'
            ),
            "selectedDisplayNames": ["safe"],
        },
    )

    assert response.status_code == 200
    assert [draft["displayName"] for draft in response.json()["drafts"]] == ["safe"]
    assert "TOPSECRET" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {
            "text": '{"mcpServers":{"Law":{"command":"node"},"law":{"command":"uvx"}}}',
        },
        {
            "text": '{"mcpServers":{"law":{"command":"node"}}}',
            "selectedDisplayNames": ["unknown"],
        },
        {
            "text": '{"mcpServers":{"law":{"command":"node"}}}',
            "selectedDisplayNames": ["law", "LAW"],
        },
    ],
)
def test_parse_import_rejects_normalized_duplicate_or_unknown_selected_names(api, body):
    response = api.client.post("/v1/mcp/import/parse", headers=HEADERS, json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "mcp_config_invalid"


def test_server_list_and_test_never_return_secret_values(api):
    listed = api.client.get("/v1/mcp/servers", headers=HEADERS)

    assert listed.status_code == 200
    body = listed.json()[0]
    assert body["secretEnvKeys"] == ["TOKEN"]
    assert body["secretHeaderKeys"] == ["Authorization"]
    assert body["env"] == {"LOG_LEVEL": "info"}
    assert body["headers"] == {"X-Tenant": "legal"}
    assert body["lastTestStatus"] is None
    assert body["lastTestedAt"] is None
    assert body["selectedThreadCount"] == 2
    assert "secret://" not in listed.text

    tested = api.client.post(
        "/v1/mcp/servers/test",
        headers=HEADERS,
        json={
            "draft": _draft_payload(),
            "secretPatch": {"env": {"TOKEN": "case-secret"}, "headers": {}},
            "acceptedRiskCodes": [],
        },
    )
    assert tested.status_code == 200
    assert tested.json()["testReceipt"] == "receipt-1"
    assert tested.json()["tools"][0]["descriptionHash"] == DESCRIPTION_HASH
    assert tested.json()["tools"][0]["schemaHash"] == SCHEMA_HASH
    assert "case-secret" not in tested.text


def test_server_create_patch_saved_test_tools_review_and_delete_contracts(api):
    created = api.client.post(
        "/v1/mcp/servers",
        headers=HEADERS,
        json={
            "draft": _draft_payload(),
            "secretPatch": {"env": {"TOKEN": "case-secret"}, "headers": {}},
            "testReceipt": "receipt-1",
            "toolDecisions": [_decision_payload()],
            "acceptedRiskCodes": [],
        },
    )
    assert created.status_code == 201
    assert created.json()["displayName"] == "Case Reader"
    assert "case-secret" not in created.text

    patched = api.client.patch(
        "/v1/mcp/servers/server-1",
        headers=HEADERS,
        json={
            "expectedRevision": 1,
            "patch": {"displayName": "Updated Reader"},
            "secretPatch": {},
            "toolDecisions": [],
            "acceptedRiskCodes": [],
        },
    )
    assert patched.status_code == 200
    assert patched.json()["displayName"] == "Updated Reader"
    assert patched.json()["configRevision"] == 2

    tested = api.client.post(
        "/v1/mcp/servers/server-1/test",
        headers=HEADERS,
        json={
            "expectedRevision": 2,
            "patch": {"requestTimeoutMs": 45000},
            "secretPatch": {},
            "acceptedRiskCodes": [],
        },
    )
    assert tested.status_code == 200
    assert tested.json()["testReceipt"] == "receipt-1"

    tested_state = api.client.get("/v1/mcp/servers", headers=HEADERS)
    assert tested_state.json()[0]["lastTestStatus"] == "passed"
    assert tested_state.json()[0]["lastTestedAt"] == NOW

    tools = api.client.get("/v1/mcp/servers/server-1/tools", headers=HEADERS)
    assert tools.status_code == 200
    assert tools.json()[0]["toolName"] == "read_case"

    reviewed = api.client.patch(
        "/v1/mcp/servers/server-1/tools/read_case",
        headers=HEADERS,
        json={
            "enabled": False,
            "expectedDescriptionHash": DESCRIPTION_HASH,
            "expectedSchemaHash": SCHEMA_HASH,
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["enabled"] is False

    unchanged = api.repository.tool
    mismatch = api.client.patch(
        "/v1/mcp/servers/server-1/tools/read_case",
        headers=HEADERS,
        json={
            "enabled": True,
            "expectedDescriptionHash": "0" * 64,
            "expectedSchemaHash": SCHEMA_HASH,
        },
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "mcp_tool_changed"
    assert api.repository.tool == unchanged

    deleted = api.client.delete("/v1/mcp/servers/server-1", headers=HEADERS)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "selectedThreadCount": 2}
    assert api.audit.deleted == [("server-1", 2)]

    missing = api.client.delete("/v1/mcp/servers/missing", headers=HEADERS)
    assert missing.status_code == 200
    assert missing.json() == {"deleted": False, "selectedThreadCount": 0}
    assert api.audit.deleted == [("server-1", 2)]


def test_create_and_update_committed_responses_do_not_depend_on_fresh_status_read(api):
    def failed_status_read(_server_id):
        raise RuntimeError("POST-COMMIT-STATUS-READ")

    api.service.connection_status = failed_status_read
    created = api.client.post(
        "/v1/mcp/servers",
        headers=HEADERS,
        json={
            "draft": _draft_payload(),
            "secretPatch": {"env": {"TOKEN": "secret"}},
            "testReceipt": "receipt-1",
            "toolDecisions": [_decision_payload()],
            "acceptedRiskCodes": [],
        },
    )
    assert created.status_code == 201
    assert created.json()["connectionStatus"] == "error"
    assert created.json()["errorCode"] == "mcp_connection_failed"
    assert created.json()["retryable"] is True

    updated = api.client.patch(
        "/v1/mcp/servers/server-1",
        headers=HEADERS,
        json={
            "expectedRevision": 1,
            "patch": {"displayName": "Updated"},
            "secretPatch": {},
            "toolDecisions": [],
            "acceptedRiskCodes": [],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["connectionStatus"] == "error"
    assert updated.json()["errorCode"] == "mcp_connection_failed"
    assert updated.json()["retryable"] is True
    assert "POST-COMMIT-STATUS-READ" not in created.text + updated.text


def test_delete_commit_is_not_misreported_when_audit_sink_raises(api):
    def broken_audit(**_kwargs):
        raise RuntimeError("AUDIT-SINK-FAILURE")

    api.audit.record_mcp_server_deleted = broken_audit
    response = api.client.delete("/v1/mcp/servers/server-1", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "selectedThreadCount": 2}
    assert "AUDIT-SINK-FAILURE" not in response.text


def test_tool_review_path_supports_slash_unicode_and_percent_without_crossing_server(api):
    raw_name = "folder/a%案"
    api.repository.tool = replace(api.repository.tool, tool_name=raw_name)

    response = api.client.patch(
        "/v1/mcp/servers/server-1/tools/" + quote(raw_name, safe=""),
        headers=HEADERS,
        json={
            "enabled": False,
            "expectedDescriptionHash": DESCRIPTION_HASH,
            "expectedSchemaHash": SCHEMA_HASH,
        },
    )

    assert response.status_code == 200
    assert response.json()["serverId"] == "server-1"
    assert response.json()["toolName"] == raw_name
    assert ("review_tool", raw_name) in api.service.calls

def test_thread_selection_snapshot_tools_and_active_view_contracts(api):
    snapshot = api.client.get("/v1/threads/thread-1/mcp", headers=HEADERS)
    assert snapshot.status_code == 200
    assert snapshot.json()["servers"][0] == {
        "serverId": "server-1",
        "displayName": "Case Reader",
        "selected": True,
        "connectionStatus": "idle",
        "toolCount": 1,
        "errorCode": None,
        "retryable": False,
    }

    selected = api.client.put(
        "/v1/threads/thread-1/mcp/server-1",
        headers=HEADERS,
    )
    assert selected.status_code == 200
    assert selected.json()["selected"] is True
    assert selected.json()["toolCount"] == 1

    tools = api.client.get("/v1/threads/thread-1/mcp/tools", headers=HEADERS)
    assert tools.status_code == 200
    assert tools.json() == [
        {
            "name": "mcp__case_reader__read_case",
            "description": "Read a case",
            "inputSchema": {"type": "object"},
        }
    ]

    activated = api.client.put(
        "/v1/mcp/active-views/view-1",
        headers=HEADERS,
        json={"threadId": "thread-1"},
    )
    assert activated.status_code == 200
    assert activated.json() == {
        "viewId": "view-1",
        "threadId": "thread-1",
        "active": True,
        "connectionStatus": "ready",
        "errorCode": None,
        "retryable": False,
    }

    ready_snapshot = api.client.get("/v1/threads/thread-1/mcp", headers=HEADERS)
    assert ready_snapshot.json()["servers"][0]["connectionStatus"] == "ready"
    assert ready_snapshot.json()["servers"][0]["toolCount"] == 1

    deactivated = api.client.delete(
        "/v1/mcp/active-views/view-1",
        headers=HEADERS,
    )
    assert deactivated.status_code == 200
    assert deactivated.json() == {"viewId": "view-1", "active": False}

    deselected = api.client.delete(
        "/v1/threads/thread-1/mcp/server-1",
        headers=HEADERS,
    )
    assert deselected.status_code == 200
    assert deselected.json()["selected"] is False


def test_existing_selection_preserves_committed_selection_when_view_sync_fails(api):
    api.service.fail_select_after_commit = True

    selected = api.client.put(
        "/v1/threads/thread-1/mcp/server-1",
        headers=HEADERS,
        json={},
    )

    assert selected.status_code == 200
    assert selected.json() == {
        "serverId": "server-1",
        "selected": True,
        "connectionStatus": "error",
        "toolCount": 0,
        "errorCode": "mcp_connection_failed",
        "retryable": True,
    }


def test_selection_body_is_optional_but_rejects_extra_fields(api):
    without_body = api.client.put(
        "/v1/threads/thread-1/mcp/server-1",
        headers=HEADERS,
    )
    assert without_body.status_code == 200

    empty_body = api.client.put(
        "/v1/threads/thread-1/mcp/server-1",
        headers=HEADERS,
        json={},
    )
    assert empty_body.status_code == 200

    calls_before = len(api.service.calls)
    invalid = api.client.put(
        "/v1/threads/thread-1/mcp/server-1",
        headers=HEADERS,
        json={"unexpected": True},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "mcp_config_invalid"
    assert len(api.service.calls) == calls_before


def test_existing_selection_does_not_hide_precommit_configuration_error(api):
    async def blocked(_thread_id, _server_id):
        raise MCPDomainError("mcp_config_blocked", retryable=False)

    api.service.select = blocked
    response = api.client.put(
        "/v1/threads/thread-1/mcp/server-1",
        headers=HEADERS,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "mcp_config_blocked"


def test_atomic_blank_draft_selection_uses_one_endpoint_and_keeps_selection_on_activation_failure(api):
    api.service.fail_activation = True
    response = api.client.post(
        "/v1/mcp/thread-selections",
        headers=HEADERS,
        json={
            "existingThreadId": None,
            "draftThread": {
                "title": "New task",
                "providerId": "provider-1",
                "model": "model-1",
                "workspaceId": None,
                "reasoningEffort": "auto",
            },
            "existingServerId": "server-1",
            "createServer": None,
            "viewId": "view-1",
        },
    )

    assert response.status_code == 201
    assert response.json()["thread"]["isDraft"] is True
    assert response.json()["selection"] == {
        "serverId": "server-1",
        "selected": True,
        "selectedAt": NOW,
        "connectionStatus": "error",
        "toolCount": 0,
        "errorCode": "mcp_connection_failed",
        "retryable": True,
    }
    assert [name for name, _ in api.service.calls].count(
        "create_thread_selection"
    ) == 1
    assert [name for name, _ in api.service.calls].count("activate_view") == 1


def test_atomic_selection_with_an_active_view_returns_the_real_tool_count(api):
    response = api.client.post(
        "/v1/mcp/thread-selections",
        headers=HEADERS,
        json={
            "existingThreadId": "thread-1",
            "draftThread": None,
            "existingServerId": "server-1",
            "createServer": None,
            "viewId": "view-1",
        },
    )

    assert response.status_code == 201
    assert response.json()["selection"]["connectionStatus"] == "ready"
    assert response.json()["selection"]["toolCount"] == 1


def test_atomic_committed_response_does_not_depend_on_fresh_status_read(api):
    def failed_status_read(_server_id):
        raise RuntimeError("POST-COMMIT-STATUS-READ")

    api.service.connection_status = failed_status_read
    response = api.client.post(
        "/v1/mcp/thread-selections",
        headers=HEADERS,
        json={
            "existingThreadId": "thread-1",
            "draftThread": None,
            "existingServerId": "server-1",
            "createServer": None,
            "viewId": None,
        },
    )

    assert response.status_code == 201
    assert response.json()["selection"]["selected"] is True
    assert response.json()["selection"]["toolCount"] == 0
    assert "POST-COMMIT-STATUS-READ" not in response.text


def test_atomic_selection_without_a_view_returns_the_reviewed_tool_count(api):
    response = api.client.post(
        "/v1/mcp/thread-selections",
        headers=HEADERS,
        json={
            "existingThreadId": "thread-1",
            "draftThread": None,
            "existingServerId": "server-1",
            "createServer": None,
            "viewId": None,
        },
    )

    assert response.status_code == 201
    assert response.json()["selection"]["connectionStatus"] == "idle"
    assert response.json()["selection"]["toolCount"] == 1


def test_mcp_domain_error_is_stable_and_unknown_error_is_not_leaked(api):
    async def domain_failure(*_args, **_kwargs):
        raise MCPDomainError("mcp_revision_conflict", retryable=True)

    api.service.create_server = domain_failure
    conflict = api.client.post(
        "/v1/mcp/servers",
        headers=HEADERS,
        json={
            "draft": _draft_payload(),
            "secretPatch": {},
            "testReceipt": "receipt-1",
            "toolDecisions": [_decision_payload()],
            "acceptedRiskCodes": [],
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"] == {
        "code": "mcp_revision_conflict",
        "message": "The MCP configuration changed. Refresh and try again.",
        "retryable": False,
    }

    async def unknown_failure(*_args, **_kwargs):
        raise RuntimeError("SECRET STACK TRACE")

    api.service.create_server = unknown_failure
    unknown = api.client.post(
        "/v1/mcp/servers",
        headers=HEADERS,
        json={
            "draft": _draft_payload(),
            "secretPatch": {},
            "testReceipt": "receipt-1",
            "toolDecisions": [_decision_payload()],
            "acceptedRiskCodes": [],
        },
    )
    assert unknown.status_code == 500
    assert unknown.json()["error"]["code"] == "internal_error"
    assert "SECRET STACK TRACE" not in unknown.text

    async def unknown_domain_failure(*_args, **_kwargs):
        raise MCPDomainError("SECRET_DOMAIN_CODE", retryable=True)

    api.service.create_server = unknown_domain_failure
    hidden = api.client.post(
        "/v1/mcp/servers",
        headers=HEADERS,
        json={
            "draft": _draft_payload(),
            "secretPatch": {},
            "testReceipt": "receipt-1",
            "toolDecisions": [_decision_payload()],
            "acceptedRiskCodes": [],
        },
    )
    assert hidden.status_code == 500
    assert hidden.json()["error"]["code"] == "internal_error"
    assert "SECRET_DOMAIN_CODE" not in hidden.text


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/v1/mcp/servers/server-1/test",
            {"expected_revision": 1, "patch": {}},
        ),
        (
            "/v1/mcp/servers/test",
            {"draft": _draft_payload(), "secret_patch": {}},
        ),
        (
            "/v1/mcp/active-views/view-1",
            {"thread_id": "thread-1"},
        ),
    ],
)
def test_mcp_requests_reject_snake_case_keys(api, path, payload):
    response = api.client.post(path, headers=HEADERS, json=payload)
    if "/active-views/" in path:
        response = api.client.put(path, headers=HEADERS, json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "mcp_config_invalid"


def test_thread_mcp_validation_uses_mcp_error_namespace(api):
    del api

    class InvalidBody(BaseModel):
        count: int

    app = FastAPI()
    install_error_handlers(app)

    @app.post("/v1/threads/thread-1/mcp/test-validation")
    async def invalid_mcp_body(_request: InvalidBody):
        return None

    response = TestClient(app).post(
        "/v1/threads/thread-1/mcp/test-validation",
        json={"count": "not-an-integer"},
    )
    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "mcp_config_invalid",
        "message": "The MCP configuration is invalid.",
        "retryable": False,
    }


def test_status_projection_never_exposes_unknown_cached_error_code(api):
    api.service.connection_status = lambda server_id: MCPRuntimeState(
        server_id=server_id,
        status="error",
        last_error_code="SECRET_INTERNAL_CONNECTOR_CODE",
        retryable=False,
    )

    response = api.client.get("/v1/mcp/servers", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()[0]["errorCode"] == "mcp_connection_failed"
    assert response.json()[0]["retryable"] is True
    assert "SECRET_INTERNAL_CONNECTOR_CODE" not in response.text


@pytest.mark.parametrize("connection_status", ["connecting", "stopping"])
def test_status_projection_preserves_non_error_lifecycle_states(
    api,
    connection_status,
):
    api.service.connection_status = lambda server_id: MCPRuntimeState(
        server_id=server_id,
        status=connection_status,
    )

    response = api.client.get("/v1/mcp/servers", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()[0]["connectionStatus"] == connection_status
    assert response.json()[0]["errorCode"] is None
    assert response.json()[0]["retryable"] is False


def test_retrying_status_preserves_state_but_sanitizes_error(api):
    api.service.connection_status = lambda server_id: MCPRuntimeState(
        server_id=server_id,
        status="retrying",
        last_error_code="SECRET_RETRY_CODE",
        retryable=False,
    )

    response = api.client.get("/v1/mcp/servers", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()[0]["connectionStatus"] == "retrying"
    assert response.json()[0]["errorCode"] == "mcp_connection_failed"
    assert response.json()[0]["retryable"] is True
    assert "SECRET_RETRY_CODE" not in response.text


def test_active_view_status_uses_the_same_error_whitelist(api):
    api.service.thread_connection_status = lambda _thread_id: MCPRuntimeState(
        server_id="",
        status="error",
        last_error_code="SECRET_THREAD_STATUS_CODE",
        retryable=False,
    )

    response = api.client.put(
        "/v1/mcp/active-views/view-1",
        headers=HEADERS,
        json={"threadId": "thread-1"},
    )

    assert response.status_code == 200
    assert response.json()["connectionStatus"] == "error"
    assert response.json()["errorCode"] == "mcp_connection_failed"
    assert response.json()["retryable"] is True
    assert "SECRET_THREAD_STATUS_CODE" not in response.text


def test_active_view_success_is_not_misreported_when_status_projection_fails(api):
    def failed_projection(_thread_id):
        raise RuntimeError("SECRET-ACTIVE-VIEW-PROJECTION")

    api.service.thread_connection_status = failed_projection
    response = api.client.put(
        "/v1/mcp/active-views/view-1",
        headers=HEADERS,
        json={"threadId": "thread-1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "viewId": "view-1",
        "threadId": "thread-1",
        "active": True,
        "connectionStatus": "error",
        "errorCode": "mcp_connection_failed",
        "retryable": True,
    }
    assert [name for name, _ in api.service.calls].count("activate_view") == 1
    assert "SECRET-ACTIVE-VIEW-PROJECTION" not in response.text


def test_thread_and_active_view_surface_missing_credentials_without_external_work(api):
    missing = MCPRuntimeState(
        server_id="server-1",
        status="needs_credentials",
        last_error_code="mcp_credentials_missing",
        retryable=True,
    )
    api.service.connection_status = lambda _server_id: missing
    api.service.thread_connection_status = lambda _thread_id: replace(
        missing,
        server_id="",
    )

    snapshot = api.client.get("/v1/threads/thread-1/mcp", headers=HEADERS)
    activated = api.client.put(
        "/v1/mcp/active-views/view-1",
        headers=HEADERS,
        json={"threadId": "thread-1"},
    )

    assert snapshot.status_code == 200
    assert snapshot.json()["servers"][0]["connectionStatus"] == "needs_credentials"
    assert snapshot.json()["servers"][0]["errorCode"] == "mcp_credentials_missing"
    assert snapshot.json()["servers"][0]["retryable"] is False
    assert activated.status_code == 200
    assert activated.json()["connectionStatus"] == "needs_credentials"
    assert activated.json()["errorCode"] == "mcp_credentials_missing"
    assert activated.json()["retryable"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {
            "existingThreadId": "thread-1",
            "draftThread": {
                "title": "New",
                "providerId": "provider-1",
                "model": "model-1",
                "workspaceId": None,
                "reasoningEffort": "auto",
            },
            "existingServerId": "server-1",
            "createServer": None,
        },
        {
            "existingThreadId": None,
            "draftThread": None,
            "existingServerId": "server-1",
            "createServer": None,
        },
        {
            "existingThreadId": "thread-1",
            "draftThread": None,
            "existingServerId": "server-1",
            "createServer": {
                "draft": _draft_payload(),
                "secretPatch": {"env": {"TOKEN": "secret"}},
                "acceptedRiskCodes": [],
                "testReceipt": "receipt",
                "toolDecisions": [_decision_payload()],
            },
        },
        {
            "existingThreadId": "thread-1",
            "draftThread": None,
            "existingServerId": None,
            "createServer": None,
        },
    ],
)
def test_atomic_selection_rejects_invalid_xor_before_service_write(api, payload):
    response = api.client.post(
        "/v1/mcp/thread-selections",
        headers=HEADERS,
        json=payload,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "mcp_config_invalid"
    assert "create_thread_selection" not in [name for name, _ in api.service.calls]


def test_atomic_draft_requires_explicit_nullable_workspace_id(api):
    response = api.client.post(
        "/v1/mcp/thread-selections",
        headers=HEADERS,
        json={
            "existingThreadId": None,
            "draftThread": {
                "title": "New",
                "providerId": "provider-1",
                "model": "model-1",
                "reasoningEffort": "auto",
            },
            "existingServerId": "server-1",
            "createServer": None,
        },
    )
    assert response.status_code == 422
    assert "create_thread_selection" not in [name for name, _ in api.service.calls]


@pytest.mark.parametrize(
    ("use_draft_thread", "create_server"),
    [(False, False), (False, True), (True, False), (True, True)],
)
def test_atomic_selection_accepts_all_four_exclusive_combinations(
    api,
    use_draft_thread,
    create_server,
):
    draft_thread = {
        "title": "New task",
        "providerId": "provider-1",
        "model": "model-1",
        "workspaceId": None,
        "reasoningEffort": "auto",
    }
    new_server = {
        "draft": _draft_payload(),
        "secretPatch": {"env": {"TOKEN": "secret"}},
        "acceptedRiskCodes": [],
        "testReceipt": "receipt",
        "toolDecisions": [_decision_payload()],
    }
    response = api.client.post(
        "/v1/mcp/thread-selections",
        headers=HEADERS,
        json={
            "existingThreadId": None if use_draft_thread else "thread-1",
            "draftThread": draft_thread if use_draft_thread else None,
            "existingServerId": None if create_server else "server-1",
            "createServer": new_server if create_server else None,
            "viewId": None,
        },
    )
    assert response.status_code == 201
    assert response.json()["selection"]["selected"] is True


def test_wrong_launch_token_rejects_mcp_route_before_service(api):
    response = api.client.get(
        "/v1/mcp/servers",
        headers={"X-VeriLex-Token": "wrong"},
    )
    assert response.status_code == 401
    assert api.service.calls == []


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("get", "/v1/mcp/servers", None),
        ("post", "/v1/mcp/import/parse", {"text": "node server.js"}),
        ("post", "/v1/mcp/servers/test", {}),
        ("post", "/v1/mcp/servers", {}),
        ("patch", "/v1/mcp/servers/server-1", {}),
        ("delete", "/v1/mcp/servers/server-1", None),
        ("post", "/v1/mcp/servers/server-1/test", {}),
        ("get", "/v1/mcp/servers/server-1/tools", None),
        ("patch", "/v1/mcp/servers/server-1/tools/read_case", {}),
        ("get", "/v1/threads/thread-1/mcp", None),
        ("put", "/v1/threads/thread-1/mcp/server-1", {}),
        ("delete", "/v1/threads/thread-1/mcp/server-1", None),
        ("get", "/v1/threads/thread-1/mcp/tools", None),
        ("post", "/v1/mcp/thread-selections", {}),
        ("put", "/v1/mcp/active-views/view-1", {}),
        ("delete", "/v1/mcp/active-views/view-1", None),
    ],
)
def test_all_mcp_routes_require_launch_token(api, method, path, json_body):
    request = getattr(api.client, method)
    response = request(path) if json_body is None else request(path, json=json_body)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_plugin_refresh_endpoint_is_strict_and_does_not_accept_drafts(api):
    path = "/v1/mcp/servers/server-1/refresh-tools"
    response = api.client.post(path, headers=HEADERS, json={"expectedRevision": 1})
    assert response.status_code == 200
    assert response.json()[0]["toolName"] == "read_case"
    for payload in ({"expectedRevision": True}, {"expectedRevision": 0},
                    {"expectedRevision": 1, "patch": {}},
                    {"expectedRevision": 1, "secretPatch": {}},
                    {"expectedRevision": 1, "toolDecisions": []}):
        assert api.client.post(path, headers=HEADERS, json=payload).status_code == 422
    assert api.client.post(path, json={"expectedRevision": 1}).status_code == 401
