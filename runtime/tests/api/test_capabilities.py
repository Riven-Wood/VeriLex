from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.models import RiskLevel
from verilex_runtime.tools import (
    ApprovalBehavior,
    ToolCategory,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolScope,
    ToolSource,
)

TOKEN = "capability-api-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


async def _execute(context: ToolContext, args: dict) -> ToolResult:
    del context, args
    return ToolResult(ok=True, output="ok")


def _definition(
    name: str,
    description: str,
    *,
    category: ToolCategory,
    scope: ToolScope,
    risk_level: RiskLevel,
    source: ToolSource = ToolSource.BUILTIN,
    source_name: str | None = None,
    approval_behavior: ApprovalBehavior = ApprovalBehavior.RISK_DEFAULT,
    authorization_owner: str = "risk_policy",
    source_id: str | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {"secret": {"type": "string"}}},
        risk_level=risk_level,
        execute=_execute,
        source=source,
        source_name=source_name,
        category=category,
        scopes=frozenset({scope}),
        requires_workspace=source is ToolSource.BUILTIN,
        approval_behavior=approval_behavior,
        authorization_owner=authorization_owner,
        source_id=source_id,
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        _definition(
            "run_command",
            "Run a command",
            category=ToolCategory.TERMINAL,
            scope=ToolScope.EXECUTE,
            risk_level=RiskLevel.HIGH,
        )
    )
    registry.register(
        _definition(
            "read_file",
            "Read a file",
            category=ToolCategory.FILES,
            scope=ToolScope.READ,
            risk_level=RiskLevel.LOW,
        )
    )
    return registry


def _client(tmp_path, *, registry, mcp_manager=None) -> TestClient:
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token=TOKEN,
        ),
        RuntimeServices(
            provider_service=object(),
            tool_registry=registry,
            mcp_manager=mcp_manager,
        ),
    )
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )


def test_tool_capabilities_returns_authenticated_safe_catalog(tmp_path):
    client = _client(tmp_path, registry=_registry())

    response = client.get("/v1/capabilities/tools", headers=HEADERS)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == [
        {
            "name": "read_file",
            "description": "Read a file",
            "source": "builtin",
            "source_name": None,
            "category": "files",
            "scopes": ["read"],
            "risk_level": "low",
            "default_approval": "automatic",
            "approval_behavior": "risk_default",
            "authorization_owner": "risk_policy",
            "requires_workspace": True,
            "availability": "available",
            "availability_reason": None,
        },
        {
            "name": "run_command",
            "description": "Run a command",
            "source": "builtin",
            "source_name": None,
            "category": "terminal",
            "scopes": ["execute"],
            "risk_level": "high",
            "default_approval": "required",
            "approval_behavior": "risk_default",
            "authorization_owner": "risk_policy",
            "requires_workspace": True,
            "availability": "available",
            "availability_reason": None,
        },
    ]
    assert all("parameters" not in item for item in response.json())
    assert all("execute" not in item for item in response.json())


def test_tool_capabilities_projects_thread_first_use_approval(tmp_path):
    registry = ToolRegistry()
    registry.register(
        _definition(
            "web_search",
            "Search the public web",
            category=ToolCategory.EXTERNAL,
            scope=ToolScope.NETWORK,
            risk_level=RiskLevel.HIGH,
            approval_behavior=ApprovalBehavior.THREAD_FIRST_USE,
        )
    )

    response = _client(tmp_path, registry=registry).get(
        "/v1/capabilities/tools",
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert response.json()[0].get("approval_behavior") == "thread_first_use"


def test_tool_capabilities_projects_document_create_as_automatic(tmp_path):
    from unittest.mock import AsyncMock

    from verilex_runtime.documents.v1_service import V1DocumentService
    from verilex_runtime.tools.documents import register_document_tools

    registry = ToolRegistry()
    register_document_tools(
        registry, AsyncMock(spec=V1DocumentService)
    )

    response = _client(tmp_path, registry=registry).get(
        "/v1/capabilities/tools",
        headers=HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    tool = payload[0]
    assert tool["name"] == "document_create"
    assert tool["category"] == "documents"
    assert tool["approval_behavior"] == "additive_artifact"
    assert tool["default_approval"] == "automatic"
    assert tool["scopes"] == ["write"]
    assert tool["risk_level"] == "high"
    assert tool["requires_workspace"] is True


def test_tool_capabilities_projects_selected_mcp_as_automatic(tmp_path):
    registry = ToolRegistry()
    registry.register(
        _definition(
            "mcp__law__write",
            "Write through a selected legal server",
            category=ToolCategory.EXTERNAL,
            scope=ToolScope.EXTERNAL_SERVICE,
            risk_level=RiskLevel.HIGH,
            source=ToolSource.MCP,
            source_name="Law Server",
            source_id="server-law",
            authorization_owner="thread_mcp_selection",
        )
    )

    response = _client(tmp_path, registry=registry).get(
        "/v1/capabilities/tools",
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert response.json()[0]["default_approval"] == "automatic"
    assert response.json()[0]["authorization_owner"] == "thread_mcp_selection"
    assert "source_id" not in response.json()[0]


def test_tool_capabilities_requires_launch_token(tmp_path):
    client = _client(tmp_path, registry=_registry())

    response = client.get("/v1/capabilities/tools")

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


class _CachedMCPManager:
    def definitions(self):
        raise AssertionError("global capabilities must not expose cross-thread MCP")

    def start(self):
        raise AssertionError("catalog query must not start MCP servers")

    def ensure(self, name):
        raise AssertionError(f"catalog query must not ensure MCP server {name}")

    def verify(self, name):
        raise AssertionError(f"catalog query must not verify MCP server {name}")


def test_tool_capabilities_does_not_read_global_mcp_definitions(tmp_path):
    client = _client(
        tmp_path,
        registry=ToolRegistry(),
        mcp_manager=_CachedMCPManager(),
    )

    response = client.get("/v1/capabilities/tools", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == []


def test_tool_capabilities_returns_empty_list_for_empty_registry(tmp_path):
    client = _client(tmp_path, registry=ToolRegistry())

    response = client.get("/v1/capabilities/tools", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == []


def test_capability_route_is_not_mounted_without_optional_registry(tmp_path):
    client = _client(tmp_path, registry=None)

    response = client.get("/v1/capabilities/tools", headers=HEADERS)

    assert response.status_code == 404
