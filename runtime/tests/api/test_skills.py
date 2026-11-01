"""Authenticated skills management API tests."""

from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.skills import SkillService
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.skill_state_repository import SkillStateRepository

TOKEN = "skills-api-token"
HEADERS = {"X-VeriLex-Token": TOKEN}


class _ProviderStub:
    def count_enabled(self) -> int:
        return 0


def _client(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    service = SkillService(
        tmp_path / "skills",
        tmp_path / "skills-trash",
        SkillStateRepository(database),
    )
    app = create_app(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token=TOKEN,
        ),
        RuntimeServices(
            provider_service=_ProviderStub(),
            skill_service=service,
        ),
    )
    return (
        TestClient(
            app,
            base_url="http://127.0.0.1",
            raise_server_exceptions=False,
        ),
        service,
    )


def _rows(client):
    response = client.get("/v1/skills", headers=HEADERS)
    assert response.status_code == 200
    return {row["name"]: row for row in response.json()}


def test_list_skills_returns_full_fields_and_parse_failures(tmp_path):
    client, service = _client(tmp_path)
    service.create("test-contract-review", "审查合同", "# 审查流程\n1. 通读合同原文")
    (tmp_path / "skills" / "broken").mkdir()
    (tmp_path / "skills" / "broken" / "SKILL.md").write_text(
        "---\nname: broken\n---\n\n只有名称没有描述", encoding="utf-8"
    )

    rows = _rows(client)

    entry = rows["test-contract-review"]
    assert entry["description"] == "审查合同"
    assert "# 审查流程" in entry["instructions"]
    assert entry["enabled"] is True
    assert entry["valid"] is True
    assert entry["error"] is None
    assert entry["path"].endswith("SKILL.md")
    broken = rows["broken"]
    assert broken["valid"] is False
    assert broken["error"] == "SKILL.md 缺少名称或描述"
    assert broken["enabled"] is True


def test_list_skills_requires_launch_token(tmp_path):
    client, _ = _client(tmp_path)

    response = client.get("/v1/skills")

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_custom_categories_persist_and_deleting_restores_automatic_assignment(tmp_path):
    client, service = _client(tmp_path)
    service.create("test-contract-review", "审查合同", "正文")
    created = client.post("/v1/skills/categories", headers=HEADERS, json={"name": "我的合同"})
    assert created.status_code == 201
    category = created.json()
    assert client.post("/v1/skills/categories", headers=HEADERS, json={"name": "我的合同"}).status_code == 409
    assert client.post("/v1/skills/categories", headers=HEADERS, json={"name": "合同审查与起草"}).status_code == 409
    assert client.put("/v1/skills/test-contract-review/category", headers=HEADERS, json={"category": category["id"]}).status_code == 204
    assert _rows(client)["test-contract-review"]["categoryOverride"] == category["id"]
    client, _ = _client(tmp_path)
    assert _rows(client)["test-contract-review"]["categoryOverride"] == category["id"]
    assert client.patch(f"/v1/skills/categories/{category['id']}", headers=HEADERS, json={"name": "办案"}).status_code == 204
    assert client.get("/v1/skills/categories", headers=HEADERS).json() == [{"id": category["id"], "name": "办案"}]
    assert client.delete(f"/v1/skills/categories/{category['id']}", headers=HEADERS).status_code == 204
    assert _rows(client)["test-contract-review"]["categoryOverride"] is None
    assert client.put("/v1/skills/test-contract-review/category", headers=HEADERS, json={"category": "missing"}).status_code == 404
    assert client.put("/v1/skills/test-contract-review/category", headers=HEADERS, json={"category": "employment"}).status_code == 204
    assert _rows(client)["test-contract-review"]["categoryOverride"] == "employment"
    assert client.put("/v1/skills/test-contract-review/category", headers=HEADERS, json={"category": None}).status_code == 204
    assert _rows(client)["test-contract-review"]["categoryOverride"] is None


def test_patch_toggles_enabled(tmp_path):
    client, service = _client(tmp_path)
    service.create("demo", "描述", "正文")

    disable = client.patch(
        "/v1/skills/demo", headers=HEADERS, json={"enabled": False}
    )
    assert disable.status_code == 204
    assert _rows(client)["demo"]["enabled"] is False

    enable = client.patch(
        "/v1/skills/demo", headers=HEADERS, json={"enabled": True}
    )
    assert enable.status_code == 204
    assert _rows(client)["demo"]["enabled"] is True


def test_patch_unknown_skill_returns_not_found_error(tmp_path):
    client, _ = _client(tmp_path)

    response = client.patch(
        "/v1/skills/ghost", headers=HEADERS, json={"enabled": False}
    )

    assert response.status_code == 404
    payload = response.json()["error"]
    assert payload["code"] == "skill_not_found"
    assert payload["message"]


def test_patch_rejects_invalid_bodies(tmp_path):
    client, _ = _client(tmp_path)

    wrong_type = client.patch(
        "/v1/skills/demo", headers=HEADERS, json={"enabled": "yes"}
    )
    extra_field = client.patch(
        "/v1/skills/demo", headers=HEADERS, json={"enabled": True, "x": 1}
    )

    assert wrong_type.status_code == 422
    assert extra_field.status_code == 422


def test_delete_moves_skill_to_trash(tmp_path):
    client, service = _client(tmp_path)
    service.create("demo", "描述", "# 原正文")

    response = client.delete("/v1/skills/demo", headers=HEADERS)

    assert response.status_code == 200
    trashed_path = response.json()["trashedPath"]
    assert trashed_path.startswith("demo-")
    assert (
        tmp_path / "skills-trash" / trashed_path / "SKILL.md"
    ).is_file()
    assert "demo" not in _rows(client)


def test_delete_unknown_skill_returns_not_found_error(tmp_path):
    client, _ = _client(tmp_path)

    response = client.delete("/v1/skills/ghost", headers=HEADERS)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "skill_not_found"


def test_health_skills_count_tracks_enabled_skills(tmp_path):
    client, service = _client(tmp_path)

    def health_skills() -> int:
        response = client.get("/v1/health", headers=HEADERS)
        assert response.status_code == 200
        return response.json()["configured"]["skills"]

    baseline = health_skills()
    service.create("demo", "描述", "正文")
    assert health_skills() == baseline + 1

    disabled = client.patch(
        "/v1/skills/demo", headers=HEADERS, json={"enabled": False}
    )
    assert disabled.status_code == 204
    assert health_skills() == baseline


def test_reveal_skill_opens_finder_on_skill_directory(tmp_path, monkeypatch):
    client, service = _client(tmp_path)
    service.create("demo", "描述", "正文")
    opened = []

    async def fake_run_open(args, env):
        opened.append(args)
        return 0

    monkeypatch.setattr(
        "verilex_runtime.workspace_preview._run_open", fake_run_open
    )

    response = client.post("/v1/skills/demo/reveal", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"path": "demo", "mode": "reveal"}
    assert opened == [["/usr/bin/open", "-R", str(tmp_path / "skills" / "demo")]]


def test_reveal_unknown_skill_returns_not_found_error(tmp_path):
    client, _ = _client(tmp_path)

    response = client.post("/v1/skills/ghost/reveal", headers=HEADERS)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "skill_not_found"
