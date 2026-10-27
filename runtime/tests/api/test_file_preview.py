"""Workspace file preview endpoint contract.

`GET /v1/workspaces/{id}/file-preview?path=` serves read-only, bounded
previews for the desktop preview rail: text (truncated at the byte limit with
a flag), images and PDFs (base64 data URLs under the binary limit), and an
explicit ``unsupported`` kind for everything else. Path safety reuses the
file-tool chain: containment resolution, symlink endpoint rejection, and
workspace read permission.
"""

import base64

import pytest
from fastapi.testclient import TestClient

from verilex_runtime.app import create_app
from verilex_runtime.container import RuntimeServices
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.workspace_repository import WorkspaceRepository

TOKEN = "file-preview-api-token"
HEADERS = {"X-VeriLex-Token": TOKEN}

PNG_MAGIC = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    + b"\x00" * 40
)


@pytest.fixture
def api_fixture(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    workspaces_repo = WorkspaceRepository(database)

    ids = iter(f"ws-{n}" for n in range(1, 1000))
    workspace_service = WorkspaceService(
        workspaces_repo,
        id_factory=lambda: next(ids),
        now=lambda: "2026-08-26T00:00:00Z",
    )
    provider_service = ProviderService(
        ProviderRepository(database), object(), object()
    )
    settings = RuntimeSettings(
        host="127.0.0.1",
        port=0,
        state_dir=tmp_path,
        api_token=TOKEN,
    )
    app = create_app(
        settings,
        RuntimeServices(
            provider_service=provider_service,
            workspace_service=workspace_service,
        ),
    )
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )
    return client, workspace_service, tmp_path


def _make_workspace(client, workspace_service, root, *, allow_read=True):
    record = workspace_service.create(
        str(root), "Authorized", allow_read=allow_read, allow_write=True
    )
    del client
    return record.id


def test_text_file_preview(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    (root / "note.md").write_text("# Title\n正文", encoding="utf-8")
    workspace_id = _make_workspace(client, workspaces, root)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        headers=HEADERS,
        params={"path": "note.md"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "text"
    assert body["name"] == "note.md"
    assert body["path"] == "note.md"
    assert body["size_bytes"] > 0
    assert body["truncated"] is False
    assert body["content"] == "# Title\n正文"
    assert "data_url" not in body
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_text_preview_truncation(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    (root / "big.txt").write_text("a" * (600 * 1024), encoding="utf-8")
    workspace_id = _make_workspace(client, workspaces, root)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        headers=HEADERS,
        params={"path": "big.txt"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "text"
    assert body["truncated"] is True
    assert len(body["content"]) == 512 * 1024


def test_binary_with_text_extension_is_unsupported(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    (root / "fake.txt").write_bytes(b"\xff\xfe\x00\xd8binary")
    workspace_id = _make_workspace(client, workspaces, root)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        headers=HEADERS,
        params={"path": "fake.txt"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "unsupported"
    assert body["name"] == "fake.txt"
    assert "content" not in body
    assert "data_url" not in body


def test_image_preview_returns_data_url(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    (root / "scan.png").write_bytes(PNG_MAGIC)
    workspace_id = _make_workspace(client, workspaces, root)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        headers=HEADERS,
        params={"path": "scan.png"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "image"
    assert body["media_type"] == "image/png"
    expected = "data:image/png;base64," + base64.b64encode(PNG_MAGIC).decode()
    assert body["data_url"] == expected
    assert "content" not in body


def test_pdf_preview_returns_data_url(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    payload = b"%PDF-1.4\n%fake but sufficient\n%%EOF"
    (root / "contract.pdf").write_bytes(payload)
    workspace_id = _make_workspace(client, workspaces, root)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        headers=HEADERS,
        params={"path": "contract.pdf"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "pdf"
    assert body["media_type"] == "application/pdf"
    expected = (
        "data:application/pdf;base64," + base64.b64encode(payload).decode()
    )
    assert body["data_url"] == expected


def test_oversized_binary_is_unsupported(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    (root / "huge.png").write_bytes(b"\x89" + b"0" * (25 * 1024 * 1024))
    workspace_id = _make_workspace(client, workspaces, root)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        headers=HEADERS,
        params={"path": "huge.png"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "unsupported"
    assert "data_url" not in body


def test_unknown_extension_is_unsupported(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    (root / "memo.bin").write_bytes(b"PK\x03\x04zip-like")
    workspace_id = _make_workspace(client, workspaces, root)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        headers=HEADERS,
        params={"path": "memo.bin"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "unsupported"


def test_docx_preview_returns_validated_original_bytes_without_modifying_file(api_fixture):
    from docx import Document
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    target = root / "申请书.docx"
    document = Document()
    document.add_heading("调取证据申请书", 0)
    document.add_paragraph("案号：[待补]")
    document.add_table(rows=1, cols=2).cell(0, 0).text = "材料清单"
    document.save(target)
    original = target.read_bytes()
    workspace_id = _make_workspace(client, workspaces, root)
    response = client.get(f"/v1/workspaces/{workspace_id}/file-preview", headers=HEADERS, params={"path": target.name})
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "docx"
    assert body["media_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert base64.b64decode(body["data_url"].split(",", 1)[1]) == original
    assert target.read_bytes() == original


def test_docx_preview_rejects_invalid_archive_before_browser_receives_it(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    (root / "invalid.docx").write_bytes(b"PK\x03\x04zip-like")
    workspace_id = _make_workspace(client, workspaces, root)
    response = client.get(f"/v1/workspaces/{workspace_id}/file-preview", headers=HEADERS, params={"path": "invalid.docx"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "attachment_docx_invalid"
    assert "data_url" not in response.json()


def test_directory_is_rejected(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    (root / "folder").mkdir()
    workspace_id = _make_workspace(client, workspaces, root)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        headers=HEADERS,
        params={"path": "folder"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "preview_not_a_file"


def test_missing_file_returns_404(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    workspace_id = _make_workspace(client, workspaces, root)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        headers=HEADERS,
        params={"path": "ghost.txt"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "file_not_found"


def test_out_of_bounds_path_rejected(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    workspace_id = _make_workspace(client, workspaces, root)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        headers=HEADERS,
        params={"path": "../outside.txt"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "path_out_of_bounds"


def test_symlink_endpoint_rejected(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    victim = root / "real.txt"
    victim.write_text("inside", encoding="utf-8")
    (root / "link.txt").symlink_to(victim)
    workspace_id = _make_workspace(client, workspaces, root)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        headers=HEADERS,
        params={"path": "link.txt"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "symlink_not_supported"


def test_requires_token(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    (root / "note.txt").write_text("x", encoding="utf-8")
    workspace_id = _make_workspace(client, workspaces, root)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        params={"path": "note.txt"},
    )

    assert response.status_code == 401


def test_read_not_allowed_workspace_rejected(api_fixture):
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / "authorized"
    root.mkdir()
    (root / "note.txt").write_text("x", encoding="utf-8")
    workspace_id = _make_workspace(client, workspaces, root, allow_read=False)

    response = client.get(
        f"/v1/workspaces/{workspace_id}/file-preview",
        headers=HEADERS,
        params={"path": "note.txt"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "read_not_allowed"


def test_unknown_workspace_returns_404(api_fixture):
    client, _, _ = api_fixture

    response = client.get(
        "/v1/workspaces/nope/file-preview",
        headers=HEADERS,
        params={"path": "x.txt"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "workspace_not_found"


@pytest.mark.parametrize('prefix', ['', 'a', 'ab'])
def test_truncated_chinese_preview_preserves_complete_characters(api_fixture, prefix):
    from verilex_runtime.workspace_preview import PREVIEW_MAX_TEXT_BYTES
    import hashlib
    client, workspaces, tmp_path = api_fixture
    root = tmp_path / 'chinese'; root.mkdir()
    raw = (prefix + '中' * 200000).encode()
    (root / '长文.txt').write_bytes(raw)
    wid = _make_workspace(client, workspaces, root)
    response = client.get(f'/v1/workspaces/{wid}/file-preview', headers=HEADERS, params={'path': '长文.txt'})
    result = response.json()
    assert result['kind'] == 'text' and result['truncated'] is True
    assert result['content'] == prefix + '中' * ((PREVIEW_MAX_TEXT_BYTES-len(prefix))//3)
    assert result['file_hash'] == hashlib.sha256(raw).hexdigest()
